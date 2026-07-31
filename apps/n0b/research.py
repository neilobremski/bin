"""OpenAI deep research via the Responses API (stdlib only).

Uses ``gpt-5.6-sol`` (OpenAI's replacement after ``o4-mini-deep-research``
shut down 2026-07-23).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from commands.secrets_cmd import resolve
from paths import BIN_ROOT

RESEARCH_MODEL = "gpt-5.6-sol"
CHEAP_MODEL = "gpt-4.1-mini"
POLL_INTERVAL_S = 30
JOB_TIMEOUT_S = 20 * 60
FANOUT_MIN = 1
FANOUT_MAX = 8
FANOUT_DEFAULT_N = 4
CANDIDATE_MIN = 12
CANDIDATE_MAX = 16
# Rough per-job estimate until a completed response carries usage.
EST_RESEARCH_JOB_USD = 0.50
# Rough gpt-4.1-mini $/1M tokens (estimate; labeled as such in stderr).
EST_CHEAP_INPUT_PER_M = 0.40
EST_CHEAP_OUTPUT_PER_M = 1.60
# Base tool budget for a single research job; scaled down as N rises.
BASE_MAX_TOOL_CALLS = 50


def _get_hash(prompt: str) -> str:
    clean = "".join(prompt.split())
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _job_key(question: str, fanout: int, model: str) -> str:
    raw = f"{question}\0{fanout}\0{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _research_cache_dir() -> Path:
    d = BIN_ROOT / ".files" / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_tool_calls(n: int) -> int:
    if n <= 1:
        return BASE_MAX_TOOL_CALLS
    return max(10, BASE_MAX_TOOL_CALLS // n)


def _clamp_fanout(n: int) -> tuple[int, bool]:
    clamped = max(FANOUT_MIN, min(FANOUT_MAX, n))
    return clamped, clamped != n


def _call_openai(
    api_key: str, prompt: str, *, max_tool_calls: int | None = None
) -> dict:
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data: dict = {
        "model": RESEARCH_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "background": True,
    }
    if max_tool_calls is not None:
        data["max_tool_calls"] = max_tool_calls
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP Error {e.code}: {e.read().decode('utf-8')}"}))
        return {"error": "http"}
    except OSError as e:
        print(json.dumps({"error": str(e)}))
        return {"error": "os"}


def _check_status(api_key: str, response_id: str) -> dict:
    url = f"https://api.openai.com/v1/responses/{response_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except OSError as e:
        return {"error": str(e)}


def _call_cheap(api_key: str, prompt: str) -> dict:
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": CHEAP_MODEL,
        "input": prompt,
    }
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return {"error": f"HTTP Error {e.code}: {body}"}
    except OSError as e:
        return {"error": str(e)}


def _output_text(response_data: dict) -> str:
    parts: list[str] = []
    for item in response_data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in (
                    "output_text",
                    "text",
                ):
                    text = block.get("text")
                    if text:
                        parts.append(text)
        elif item.get("type") == "output_text" and item.get("text"):
            parts.append(item["text"])
    if parts:
        return "\n".join(parts)
    for key in ("output_text", "text"):
        val = response_data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _usage_tokens(response_data: dict) -> tuple[int, int]:
    usage = response_data.get("usage") or {}
    inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return inp, out


def _est_cheap_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * EST_CHEAP_INPUT_PER_M / 1_000_000
        + output_tokens * EST_CHEAP_OUTPUT_PER_M / 1_000_000
    )


def _extract_urls(text: str) -> list[str]:
    found = re.findall(r"https?://[^\s\]\)>'\"<>]+", text)
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        u = u.rstrip(".,;:)")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _normalize_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    )
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def _urls_from_response(data: dict, body: str) -> list[str]:
    urls = _extract_urls(body)
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            for ann in block.get("annotations") or []:
                if isinstance(ann, dict) and ann.get("url"):
                    u = ann["url"]
                    if u not in urls:
                        urls.append(u)
    return urls


def _parse_json_obj(text: str) -> object | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def mmr_select(
    candidates: list[dict], n: int, *, lambda_: float = 0.0
) -> list[dict]:
    """Select ``n`` candidates by MMR on token Jaccard of seed_query.

    ``lambda_=0`` is pure diversity (minimize similarity to the selected set).
    """
    if n <= 0 or not candidates:
        return []
    n = min(n, len(candidates))
    tokenized = [_tokenize(str(c.get("seed_query") or c.get("angle") or "")) for c in candidates]
    selected: list[int] = []
    remaining = list(range(len(candidates)))

    # Seed with the first candidate (stable order) then diversify.
    selected.append(remaining.pop(0))

    while len(selected) < n and remaining:
        best_i = remaining[0]
        best_score = float("-inf")
        for i in remaining:
            toks = tokenized[i]
            # Relevance term unused at λ=0; keep the shape for clarity.
            relevance = 1.0
            max_sim = max(_jaccard(toks, tokenized[j]) for j in selected)
            score = lambda_ * relevance - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_i = i
        remaining.remove(best_i)
        selected.append(best_i)

    return [candidates[i] for i in selected]


def _build_sub_prompt(
    goal: str, brief: str, selected: dict, all_selected: list[dict]
) -> str:
    angle = str(selected.get("angle") or "").strip()
    seed = str(selected.get("seed_query") or angle).strip()
    others = [
        str(o.get("angle") or "").strip()
        for o in all_selected
        if o is not selected and str(o.get("angle") or "").strip()
    ]
    boundaries = (
        "Do NOT cover these sibling angles (other sub-jobs own them):\n"
        + "\n".join(f"- {o}" for o in others)
        if others
        else "Stay tightly scoped to this angle only."
    )
    return (
        f"## Top-level research goal\n{goal}\n\n"
        f"## Shared brief\n{brief}\n\n"
        f"## Your objective\n"
        f"Research this angle only: {angle}\n"
        f"Seed query / search focus: {seed}\n\n"
        f"## Output format\n"
        "Markdown brief with: short summary, numbered findings with inline "
        "source URLs, and a Sources list. Every claim needs a URL.\n\n"
        f"## Source guidance\n"
        "Prefer primary sources, peer-reviewed work, official docs, and "
        "high-signal reporting. Note date and provenance when relevant.\n\n"
        f"## Boundaries (what this sub-question does NOT cover)\n"
        f"{boundaries}\n"
    )


def _brief_and_candidates(
    api_key: str, prompt: str
) -> tuple[str | None, list[dict] | None, str, dict]:
    decompose_prompt = (
        "You are planning a multi-angle deep-research run.\n"
        "Given the research question below, return ONLY a JSON object with:\n"
        '  "brief": a planning brief of at most 200 words,\n'
        f'  "candidates": an array of {CANDIDATE_MIN}-{CANDIDATE_MAX} objects, '
        'each with "angle" (short label) and "seed_query" (search-oriented '
        "phrase). Candidates must cover DISTINCT dimensions — not paraphrases "
        "of the same anchor.\n\n"
        f"Question:\n{prompt}"
    )
    resp = _call_cheap(api_key, decompose_prompt)
    if resp.get("error"):
        return None, None, f"decompose failed: {resp['error']}", resp
    text = _output_text(resp)
    parsed = _parse_json_obj(text)
    if not isinstance(parsed, dict):
        return None, None, f"decompose did not return a JSON object (got: {text[:200]!r})", resp
    brief = parsed.get("brief")
    candidates = parsed.get("candidates")
    if not isinstance(brief, str) or not brief.strip():
        return None, None, "decompose brief must be a non-empty string", resp
    if not isinstance(candidates, list):
        return None, None, "decompose candidates must be a list", resp
    cleaned: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        angle = c.get("angle")
        seed = c.get("seed_query") or angle
        if isinstance(angle, str) and angle.strip() and isinstance(seed, str) and seed.strip():
            cleaned.append({"angle": angle.strip(), "seed_query": seed.strip()})
    if len(cleaned) < CANDIDATE_MIN:
        return (
            None,
            None,
            f"decompose returned {len(cleaned)} usable candidates, need >= {CANDIDATE_MIN}",
            resp,
        )
    return brief.strip(), cleaned[:CANDIDATE_MAX], "", resp


def _citation_union(reports: list[tuple[str, str, list[str]]]) -> list[dict]:
    counts: dict[str, dict] = {}
    for i, (_q, _body, urls) in enumerate(reports, start=1):
        for u in urls:
            norm = _normalize_url(u)
            entry = counts.setdefault(norm, {"url": norm, "count": 0, "reports": []})
            entry["count"] += 1
            label = f"report-{i:02d}.md"
            if label not in entry["reports"]:
                entry["reports"].append(label)
    return sorted(counts.values(), key=lambda e: (-e["count"], e["url"]))


def _merge_reports(
    api_key: str,
    prompt: str,
    reports: list[tuple[int, str, str, list[str]]],
    missing: list[dict],
) -> tuple[str | None, str, dict]:
    blocks: list[str] = []
    for i, question, body, urls in reports:
        url_lines = "\n".join(f"- {u}" for u in urls) or "- (none extracted)"
        blocks.append(
            f"### Sub-report {i}\n"
            f"File: report-{i:02d}.md\n"
            f"Question: {question}\n\n"
            f"{body}\n\n"
            f"Cited URLs:\n{url_lines}\n"
        )
    missing_block = ""
    if missing:
        lines = [
            f"- Sub-question {m['index']} ({m['question']}): {m['reason']}"
            for m in missing
        ]
        missing_block = (
            "\n## Incomplete coverage (MUST include in output)\n"
            "The following sub-questions did not produce a usable report. "
            "State them plainly in a ## Missing sub-questions section — "
            "do not invent findings for them:\n"
            + "\n".join(lines)
            + "\n"
        )
    citation_rows = _citation_union([(q, b, u) for _, q, b, u in reports])
    cite_lines = "\n".join(
        f"- {c['url']} (corroboration={c['count']}; {', '.join(c['reports'])})"
        for c in citation_rows
    ) or "- (none)"

    merge_prompt = (
        "Merge the completed sub-reports below into ONE markdown research brief.\n"
        "Requirements:\n"
        "1. Cluster related claims across sub-reports. Every claim must have "
        "per-claim source attribution of the form [report-NN.md | URL].\n"
        "2. Include ## Citations with the URL-normalized union below, preserving "
        "corroboration counts (how many sub-reports independently cite each URL).\n"
        "3. Include ## Contradictions listing every point where sub-reports "
        "disagree. SURFACING is mandatory — do NOT pick a winner, reconcile, "
        "or silently drop either side. Attribute both sides "
        "[report-NN.md | URL].\n"
        "4. If any sub-questions are missing, include ## Missing sub-questions "
        "naming each and the reason (timed out / failed).\n"
        "5. Start with # Merged research and a short ## Summary.\n"
        "6. Output markdown only. One merge pass — do not invent sources.\n\n"
        f"Original question:\n{prompt}\n\n"
        f"Citation union (normalized):\n{cite_lines}\n"
        f"{missing_block}\n"
        + "\n".join(blocks)
    )
    resp = _call_cheap(api_key, merge_prompt)
    if resp.get("error"):
        return None, f"merge failed: {resp['error']}", resp
    text = _output_text(resp).strip()
    if not text:
        return None, "merge returned empty text", resp
    return text, "", resp


def _citation_check(
    api_key: str, merged: str, reports: list[tuple[int, str, str, list[str]]]
) -> tuple[str | None, str, dict]:
    report_blobs = []
    for i, question, body, urls in reports:
        report_blobs.append(
            f"report-{i:02d}.md | {question}\n"
            f"URLs: {', '.join(urls) if urls else '(none)'}\n"
            f"Excerpt:\n{body[:1500]}\n"
        )
    check_prompt = (
        "Citation check pass. Given the merged brief and the sub-reports, "
        "verify each claim in the merged brief actually traces to a cited "
        "source present in the sub-reports. Return markdown:\n"
        "## Citation check\n"
        "- OK / WEAK / MISSING lines per claim (brief).\n"
        "Do not rewrite the brief. Do not resolve contradictions.\n\n"
        f"Merged brief:\n{merged}\n\n"
        "Sub-reports:\n" + "\n".join(report_blobs)
    )
    resp = _call_cheap(api_key, check_prompt)
    if resp.get("error"):
        return None, f"citation check failed: {resp['error']}", resp
    text = _output_text(resp).strip()
    if not text:
        return None, "citation check returned empty text", resp
    return text, "", resp


def _write_sub_report(
    path: Path, index: int, question: str, body: str, urls: list[str]
) -> None:
    lines = [
        f"# Sub-report {index}",
        "",
        "## Question",
        "",
        question,
        "",
        "## Findings",
        "",
        body.strip() or "(no text extracted from response)",
        "",
        "## Sources",
        "",
    ]
    if urls:
        lines.extend(f"- {u}" for u in urls)
    else:
        lines.append("- (none extracted)")
    lines.append("")
    path.write_text("\n".join(lines))


def _save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def _save_job(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def run_research(prompt_parts: list[str]) -> int:
    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    prompt = " ".join(prompt_parts)
    prompt_hash = _get_hash(prompt)
    cache_dir = _research_cache_dir()
    cache_file = cache_dir / f"{prompt_hash}.json"

    if cache_file.is_file():
        response_data = json.loads(cache_file.read_text())
    else:
        response_data = _call_openai(api_key, prompt)
        if response_data.get("error"):
            return 1
        cache_file.write_text(json.dumps(response_data))

    response_id = response_data.get("id")
    if not response_id:
        print(json.dumps(response_data))
        return 1

    while True:
        status_data = _check_status(api_key, response_id)
        if status_data.get("status") == "completed":
            print(json.dumps(status_data))
            return 0
        if status_data.get("status") == "failed":
            print(json.dumps(status_data))
            return 1
        sys.stderr.write(
            f"Status: {status_data.get('status', 'unknown')} — polling again in 30s\n"
        )
        time.sleep(30)


def _plan_fanout(
    api_key: str, prompt: str, n: int
) -> tuple[str | None, list[dict] | None, list[str] | None, float, str]:
    """Decompose + MMR select. Returns brief, selected, sub_prompts, cheap_cost, err."""
    brief, candidates, err, decomp_resp = _brief_and_candidates(api_key, prompt)
    if brief is None or candidates is None:
        return None, None, None, 0.0, err
    inp, out = _usage_tokens(decomp_resp)
    cheap_cost = _est_cheap_usd(inp, out)
    selected = mmr_select(candidates, n, lambda_=0.0)
    if len(selected) < n:
        return (
            None,
            None,
            None,
            cheap_cost,
            f"MMR selected {len(selected)} candidates, need {n}",
        )
    sub_prompts = [_build_sub_prompt(prompt, brief, s, selected) for s in selected]
    return brief, selected, sub_prompts, cheap_cost, ""


def run_research_plan_only(prompt_parts: list[str], n: int) -> int:
    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    n, was_clamped = _clamp_fanout(n)
    if was_clamped:
        sys.stderr.write(f"Clamped --fanout to {n} (allowed {FANOUT_MIN}-{FANOUT_MAX})\n")

    prompt = " ".join(prompt_parts)
    brief, selected, _sub_prompts, cheap_cost, err = _plan_fanout(api_key, prompt, n)
    if brief is None or selected is None:
        print(json.dumps({"error": err}))
        return 1

    sys.stderr.write(
        f"Plan-only: {n} sub-questions selected from {CANDIDATE_MIN}+ candidates "
        f"(cheap-call estimate ${cheap_cost:.4f}); no research jobs submitted.\n"
    )
    print(f"# Research plan (N={n})\n")
    print("## Brief\n")
    print(brief)
    print()
    print("## Selected sub-questions\n")
    for i, s in enumerate(selected, start=1):
        print(f"{i}. {s['angle']}")
        print(f"   seed_query: {s['seed_query']}")
    return 0


def _poll_set(
    api_key: str,
    jobs: dict[int, dict],
    run_dir: Path,
    manifest: dict,
    manifest_path: Path,
    *,
    timeout_s: float | None = None,
) -> tuple[dict[int, dict], dict[int, str]]:
    """Poll a set of background jobs until quorum or all settle.

    ``jobs`` maps index -> job record with at least ``id``. Mutates manifest
    job entries and persists each completion immediately (OpenAI retains
    background results ~10 minutes).
    """
    if timeout_s is None:
        timeout_s = float(JOB_TIMEOUT_S)
    n = len(jobs)
    quorum = math.ceil(n / 2)
    completed: dict[int, dict] = {}
    terminal: dict[int, str] = {}
    started_at = {i: time.monotonic() for i in jobs}
    pending = set(jobs)

    # Already-finished jobs from a prior run (result on disk).
    for i in list(pending):
        job_meta = next(
            (j for j in manifest["jobs"] if j["index"] == i), None
        )
        if not job_meta:
            continue
        result_path = run_dir / f"job-{i}-result.json"
        if job_meta.get("state") == "completed" and result_path.is_file():
            completed[i] = json.loads(result_path.read_text())
            terminal[i] = "completed"
            pending.discard(i)
            sys.stderr.write(f"[job-{i}] already completed (resumed from disk)\n")

    while pending:
        for i in list(pending):
            rid = jobs[i]["id"]
            status_data = _check_status(api_key, rid)
            status = status_data.get("status", "unknown")
            if status == "completed":
                completed[i] = status_data
                terminal[i] = "completed"
                pending.discard(i)
                result_path = run_dir / f"job-{i}-result.json"
                _save_job(result_path, status_data)
                for j in manifest["jobs"]:
                    if j["index"] == i:
                        j["state"] = "completed"
                        j["result_file"] = result_path.name
                _save_manifest(manifest_path, manifest)
                sys.stderr.write(f"[job-{i}] completed (persisted)\n")
            elif status == "failed":
                terminal[i] = "failed"
                pending.discard(i)
                fail_path = run_dir / f"job-{i}-result.json"
                _save_job(fail_path, status_data)
                for j in manifest["jobs"]:
                    if j["index"] == i:
                        j["state"] = "failed"
                _save_manifest(manifest_path, manifest)
                sys.stderr.write(f"[job-{i}] failed\n")
            elif status_data.get("error") and "status" not in status_data:
                terminal[i] = "error"
                pending.discard(i)
                for j in manifest["jobs"]:
                    if j["index"] == i:
                        j["state"] = "error"
                        j["error"] = str(status_data.get("error"))
                _save_manifest(manifest_path, manifest)
                sys.stderr.write(
                    f"[job-{i}] error: {status_data.get('error')}\n"
                )
            else:
                elapsed = time.monotonic() - started_at[i]
                if elapsed >= timeout_s:
                    terminal[i] = "timed out"
                    pending.discard(i)
                    for j in manifest["jobs"]:
                        if j["index"] == i:
                            j["state"] = "timed_out"
                    _save_manifest(manifest_path, manifest)
                    sys.stderr.write(
                        f"[job-{i}] timed out after {int(elapsed)}s\n"
                    )
                else:
                    sys.stderr.write(
                        f"[job-{i}] status={status} — "
                        f"polling again in {POLL_INTERVAL_S}s\n"
                    )

        sys.stderr.write(
            f"Progress: {len(completed)} completed, "
            f"{len(terminal) - len(completed)} settled-other, "
            f"{len(pending)} pending (quorum {quorum}/{n})\n"
        )

        if not pending:
            break

        # Once quorum is met, stop waiting on stragglers past their budget.
        if len(completed) >= quorum and all(
            (time.monotonic() - started_at[i]) >= timeout_s for i in pending
        ):
            for i in list(pending):
                terminal[i] = "timed out"
                pending.discard(i)
                for j in manifest["jobs"]:
                    if j["index"] == i:
                        j["state"] = "timed_out"
                sys.stderr.write(
                    f"[job-{i}] abandoned after quorum (timed out)\n"
                )
            _save_manifest(manifest_path, manifest)
            break

        # If quorum already met, sleep only until the soonest job timeout.
        if len(completed) >= quorum:
            waits = [
                max(0.0, timeout_s - (time.monotonic() - started_at[i]))
                for i in pending
            ]
            sleep_for = min(POLL_INTERVAL_S, min(waits)) if waits else 0.0
        else:
            sleep_for = float(POLL_INTERVAL_S)
        if sleep_for > 0:
            time.sleep(sleep_for)

    return completed, terminal


def run_research_fanout(
    prompt_parts: list[str], n: int, *, plan_only: bool = False
) -> int:
    if plan_only:
        return run_research_plan_only(prompt_parts, n)

    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    n, was_clamped = _clamp_fanout(n)
    if was_clamped:
        sys.stderr.write(f"Clamped --fanout to {n} (allowed {FANOUT_MIN}-{FANOUT_MAX})\n")
    if n == 1:
        return run_research(prompt_parts)

    prompt = " ".join(prompt_parts)
    prompt_hash = _get_hash(prompt)
    run_dir = _research_cache_dir() / f"fanout-{prompt_hash}"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    tool_budget = _max_tool_calls(n)
    cheap_cost = 0.0

    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if int(manifest.get("n", 0)) != n:
            print(
                json.dumps(
                    {
                        "error": (
                            f"cached fanout has n={manifest.get('n')}, "
                            f"but --fanout {n} was requested"
                        )
                    }
                )
            )
            return 1
        brief = manifest["brief"]
        selected = list(manifest["selected"])
        sub_prompts = list(manifest["sub_prompts"])
        sys.stderr.write(
            f"Resuming fanout run at {run_dir} "
            f"({n} sub-questions from manifest)\n"
        )
    else:
        sys.stderr.write(
            f"Planning fanout: brief + {CANDIDATE_MIN}-{CANDIDATE_MAX} "
            f"candidate angles, MMR-select {n}…\n"
        )
        brief, selected, sub_prompts, cheap_cost, err = _plan_fanout(
            api_key, prompt, n
        )
        if brief is None or selected is None or sub_prompts is None:
            print(json.dumps({"error": err}))
            return 1
        sys.stderr.write(
            f"Plan ready (cheap-call estimate ${cheap_cost:.4f}); "
            f"max_tool_calls={tool_budget} per job\n"
        )
        for i, s in enumerate(selected, start=1):
            sys.stderr.write(f"  {i}. {s['angle']}  [{s['seed_query']}]\n")
        manifest = {
            "prompt": prompt,
            "n": n,
            "model": RESEARCH_MODEL,
            "brief": brief,
            "selected": selected,
            "sub_prompts": sub_prompts,
            "max_tool_calls": tool_budget,
            "jobs": [],
        }
        _save_manifest(manifest_path, manifest)

    # Sequential submit (background POSTs return immediately). Resume skips
    # jobs that already have an id / completed result on disk.
    job_records: dict[int, dict] = {}
    existing_jobs = {j["index"]: j for j in manifest.get("jobs") or []}

    for i in range(1, n + 1):
        question = selected[i - 1]["angle"]
        sub_prompt = sub_prompts[i - 1]
        key = _job_key(question, n, RESEARCH_MODEL)
        cache_file = run_dir / f"job-{i}.json"
        result_file = run_dir / f"job-{i}-result.json"
        prior = existing_jobs.get(i)

        if prior and prior.get("state") == "completed" and result_file.is_file():
            job_records[i] = {
                "id": prior.get("id") or "",
                "key": key,
                "state": "completed",
            }
            sys.stderr.write(f"[job-{i}] skip submit (already completed)\n")
            continue

        if cache_file.is_file():
            data = json.loads(cache_file.read_text())
            rid = data.get("id") or ""
            if rid:
                job_records[i] = {"id": rid, "key": key, "state": "submitted"}
                if not prior or prior.get("id") != rid:
                    existing_jobs[i] = {
                        "index": i,
                        "key": key,
                        "id": rid,
                        "question": question,
                        "state": "submitted",
                    }
                sys.stderr.write(f"[job-{i}] rejoined id={rid}\n")
                continue

        data = _call_openai(api_key, sub_prompt, max_tool_calls=tool_budget)
        if data.get("error"):
            sys.stderr.write(f"[job-{i}] submit error: {data.get('error')}\n")
            existing_jobs[i] = {
                "index": i,
                "key": key,
                "id": "",
                "question": question,
                "state": "submit_failed",
                "error": str(data.get("error")),
            }
            continue
        rid = data.get("id") or ""
        _save_job(cache_file, data)
        job_records[i] = {"id": rid, "key": key, "state": "submitted"}
        existing_jobs[i] = {
            "index": i,
            "key": key,
            "id": rid,
            "question": question,
            "state": "submitted",
        }
        sys.stderr.write(f"[job-{i}] submitted id={rid}\n")

    manifest["jobs"] = [existing_jobs[i] for i in range(1, n + 1) if i in existing_jobs]
    _save_manifest(manifest_path, manifest)

    live = {i: rec for i, rec in job_records.items() if rec.get("id")}
    # Include already-completed so poll_set can load them; exclude submit failures.
    for i, j in existing_jobs.items():
        if j.get("state") == "completed" and i not in live:
            live[i] = {"id": j.get("id") or "cached", "key": j.get("key", "")}

    submitted = sum(
        1
        for j in manifest["jobs"]
        if j.get("state") in ("submitted", "completed") and j.get("id")
    )
    sys.stderr.write(
        f"Jobs submitted/rejoined: {submitted}/{n} "
        f"(estimate {submitted} x ${EST_RESEARCH_JOB_USD:.2f} = "
        f"${submitted * EST_RESEARCH_JOB_USD:.2f} research + "
        f"${cheap_cost:.4f} cheap-calls; max_tool_calls={tool_budget})\n"
    )
    if not live:
        print(json.dumps({"error": "all fanout submits failed"}))
        return 1

    completed, terminal = _poll_set(
        api_key, live, run_dir, manifest, manifest_path
    )

    quorum = math.ceil(n / 2)
    if len(completed) < quorum:
        print(
            json.dumps(
                {
                    "error": (
                        f"only {len(completed)}/{n} jobs completed "
                        f"(need >= {quorum} for partial merge)"
                    ),
                    "run_dir": str(run_dir),
                }
            )
        )
        return 1

    reports: list[tuple[int, str, str, list[str]]] = []
    missing: list[dict] = []
    for i in range(1, n + 1):
        question = selected[i - 1]["angle"]
        data = completed.get(i)
        if data is None:
            reason = terminal.get(i) or "failed"
            missing.append({"index": i, "question": question, "reason": reason})
            continue
        body = _output_text(data)
        urls = _urls_from_response(data, body)
        report_path = run_dir / f"report-{i:02d}.md"
        _write_sub_report(report_path, i, question, body, urls)
        reports.append((i, question, body, urls))

    sys.stderr.write(
        f"Merging {len(reports)}/{n} completed sub-reports "
        f"({len(missing)} missing)…\n"
    )
    merged, err, merge_resp = _merge_reports(api_key, prompt, reports, missing)
    if merged is None:
        print(json.dumps({"error": err, "run_dir": str(run_dir)}))
        return 1
    inp, out = _usage_tokens(merge_resp)
    cheap_cost += _est_cheap_usd(inp, out)

    check_text, check_err, check_resp = _citation_check(api_key, merged, reports)
    if check_text is None:
        sys.stderr.write(f"Citation check skipped: {check_err}\n")
    else:
        inp, out = _usage_tokens(check_resp)
        cheap_cost += _est_cheap_usd(inp, out)
        if not merged.endswith("\n"):
            merged += "\n"
        merged = merged + "\n" + check_text
        if not merged.endswith("\n"):
            merged += "\n"

    if missing:
        # Guarantee the partial-merge disclosure even if the model omitted it.
        missing_section = "\n## Missing sub-questions\n\n"
        for m in missing:
            missing_section += (
                f"- Sub-question {m['index']} ({m['question']}): {m['reason']}\n"
            )
        if "## Missing sub-questions" not in merged:
            merged = merged.rstrip() + "\n" + missing_section + "\n"

    (run_dir / "merged.md").write_text(
        merged if merged.endswith("\n") else merged + "\n"
    )
    manifest["state"] = "merged"
    manifest["completed"] = sorted(completed)
    manifest["missing"] = missing
    _save_manifest(manifest_path, manifest)

    failed_or_missing = n - len(completed)
    est_total = submitted * EST_RESEARCH_JOB_USD + cheap_cost
    sys.stderr.write(
        f"Fanout complete: {len(completed)}/{n} jobs ok, "
        f"{failed_or_missing} missing/failed\n"
        f"Cost estimate: {submitted} research jobs x ${EST_RESEARCH_JOB_USD:.2f} "
        f"= ${submitted * EST_RESEARCH_JOB_USD:.2f} (estimate) + "
        f"cheap calls ${cheap_cost:.4f} (estimate) ≈ ${est_total:.2f}\n"
        f"Run directory: {run_dir}\n"
    )
    print(str(run_dir))
    return 0 if not missing else 1
