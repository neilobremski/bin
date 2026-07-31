"""OpenAI deep research via the Responses API (stdlib only).

Uses ``gpt-5.6-sol`` for research jobs and merge; ``gpt-5.6-luna`` for
Stage 0 planning and the citation-check pass.
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
PLANNER_MODEL = "gpt-5.6-luna"
POLL_INTERVAL_S = 30
JOB_TIMEOUT_S = 20 * 60
FANOUT_MIN = 1
FANOUT_MAX = 8
FANOUT_AUTO = 0
CANDIDATE_FLOOR = 12
# gpt-5.6-luna $/1M tokens.
EST_PLANNER_INPUT_PER_M = 1.0
EST_PLANNER_OUTPUT_PER_M = 6.0
# Unverified: no published per-job figure for gpt-5.6-sol deep research.
# Do not seed from deprecated o3 / o4-mini deep-research price tables.
UNVERIFIED_EST_RESEARCH_JOB_USD = 0.50
BASE_MAX_TOOL_CALLS = 50
QPD_WARN_THRESHOLD = 0.4


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


def _merge_quorum(n: int) -> int:
    return math.ceil(0.67 * n)


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


def _call_model(api_key: str, model: str, prompt: str) -> dict:
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": model,
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


def _call_planner(api_key: str, prompt: str) -> dict:
    return _call_model(api_key, PLANNER_MODEL, prompt)


def _call_sol_text(api_key: str, prompt: str) -> dict:
    return _call_model(api_key, RESEARCH_MODEL, prompt)


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


def _est_planner_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * EST_PLANNER_INPUT_PER_M / 1_000_000
        + output_tokens * EST_PLANNER_OUTPUT_PER_M / 1_000_000
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
    """Conservative URL key for citation unions.

    Lowercases scheme/host and drops trailing slashes / empty fragments only.
    Keeps path, query (incl. rev/version), and distinct paths so revisions,
    filings, and primary-vs-summary URLs stay separate. Over-merging citations
    hides independent corroboration.
    """
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
    # Keep all query params (sorted for stable keys); never drop rev/version.
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

    selected.append(remaining.pop(0))

    while len(selected) < n and remaining:
        best_i = remaining[0]
        best_score = float("-inf")
        for i in remaining:
            toks = tokenized[i]
            relevance = 1.0
            max_sim = max(_jaccard(toks, tokenized[j]) for j in selected)
            score = lambda_ * relevance - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_i = i
        remaining.remove(best_i)
        selected.append(best_i)

    return [candidates[i] for i in selected]


def select_with_adversarial(
    candidates: list[dict], adversarial: dict, n: int
) -> list[dict]:
    """Select N-1 topical angles by MMR; always append the adversarial slot.

    MMR rejects a skeptic's query because it is lexically close to the topical
    query it attacks — so the adversarial angle is reserved, never competed.
    """
    if n <= 0:
        return []
    if n == 1:
        return [adversarial]
    topical_n = n - 1
    # Drop candidates that are the same angle label as the adversarial slot.
    adv_angle = str(adversarial.get("angle") or "").strip().lower()
    pool = [
        c
        for c in candidates
        if str(c.get("angle") or "").strip().lower() != adv_angle
    ]
    topical = mmr_select(pool if pool else candidates, topical_n, lambda_=0.0)
    return topical + [adversarial]


def _build_sub_prompt(
    goal: str, brief: str, selected: dict, all_selected: list[dict]
) -> str:
    angle = str(selected.get("angle") or "").strip()
    seed = str(selected.get("seed_query") or angle).strip()
    siblings = [
        str(o.get("angle") or "").strip()
        for o in all_selected
        if o is not selected and str(o.get("angle") or "").strip()
    ]
    if siblings:
        sibling_block = (
            "Sibling assignments (other sub-jobs own these scopes):\n"
            + "\n".join(f"- {o}" for o in siblings)
            + "\nDo not enter a sibling's scope except to surface a "
            "contradiction with it."
        )
    else:
        sibling_block = "Stay tightly scoped to this angle only."
    return (
        f"## Global objective\n{goal}\n\n"
        f"## Shared brief\n{brief}\n\n"
        f"## Your objective\n"
        f"Research this angle only: {angle}\n"
        f"Seed query / search focus: {seed}\n\n"
        f"## Output format\n"
        "Markdown brief with: short summary, numbered findings with inline "
        "source URLs (include observation dates when the source states them), "
        "and a Sources list. Every claim needs a URL.\n\n"
        f"## Source guidance\n"
        "Prefer primary sources, peer-reviewed work, official docs, and "
        "high-signal reporting. Note date and provenance when relevant.\n\n"
        f"## Boundaries\n"
        f"{sibling_block}\n"
    )


def _clean_angle(c: dict) -> dict | None:
    if not isinstance(c, dict):
        return None
    angle = c.get("angle")
    seed = c.get("seed_query") or angle
    if isinstance(angle, str) and angle.strip() and isinstance(seed, str) and seed.strip():
        return {"angle": angle.strip(), "seed_query": seed.strip()}
    return None


def _brief_and_candidates(
    api_key: str, prompt: str
) -> tuple[str | None, list[dict] | None, dict | None, int | None, str, dict]:
    """Stage 0: brief, >=12 candidates, adversarial slot, recommended_fanout."""
    decompose_prompt = (
        "You are planning a multi-angle deep-research run.\n"
        "Given the research question below, return ONLY a JSON object with:\n"
        '  "brief": a planning brief of at most 200 words,\n'
        '  "recommended_fanout": an integer in [1, 8] for how many parallel '
        "angles this question warrants (use 1 for simple factual questions "
        "that need no fanout),\n"
        f'  "candidates": an array of at least {CANDIDATE_FLOOR} objects, '
        'each with "angle" (short label) and "seed_query" (search-oriented '
        "phrase). Candidates must cover DISTINCT dimensions — not paraphrases "
        "of the same anchor,\n"
        '  "adversarial": one object with "angle" and "seed_query" that '
        "actively seeks contradictions, counter-evidence, or reasons the "
        "main thesis may be wrong. This is reserved separately from "
        "candidates and must not be omitted.\n\n"
        f"Question:\n{prompt}"
    )
    resp = _call_planner(api_key, decompose_prompt)
    if resp.get("error"):
        return None, None, None, None, f"decompose failed: {resp['error']}", resp
    text = _output_text(resp)
    parsed = _parse_json_obj(text)
    if not isinstance(parsed, dict):
        return (
            None,
            None,
            None,
            None,
            f"decompose did not return a JSON object (got: {text[:200]!r})",
            resp,
        )
    brief = parsed.get("brief")
    candidates = parsed.get("candidates")
    adv_raw = parsed.get("adversarial")
    rec = parsed.get("recommended_fanout")
    if not isinstance(brief, str) or not brief.strip():
        return None, None, None, None, "decompose brief must be a non-empty string", resp
    if not isinstance(candidates, list):
        return None, None, None, None, "decompose candidates must be a list", resp
    adversarial = _clean_angle(adv_raw) if isinstance(adv_raw, dict) else None
    if adversarial is None:
        return None, None, None, None, "decompose adversarial must be an angle object", resp
    cleaned: list[dict] = []
    for c in candidates:
        item = _clean_angle(c)
        if item is not None:
            cleaned.append(item)
    if len(cleaned) < CANDIDATE_FLOOR:
        return (
            None,
            None,
            None,
            None,
            f"decompose returned {len(cleaned)} usable candidates, "
            f"need >= {CANDIDATE_FLOOR}",
            resp,
        )
    if not isinstance(rec, int) or isinstance(rec, bool):
        try:
            rec = int(rec)
        except (TypeError, ValueError):
            return (
                None,
                None,
                None,
                None,
                "decompose recommended_fanout must be an integer",
                resp,
            )
    rec, _ = _clamp_fanout(rec)
    return brief.strip(), cleaned, adversarial, rec, "", resp


def _citation_union(reports: list[tuple[str, str, list[str]]]) -> list[dict]:
    counts: dict[str, dict] = {}
    for i, (_q, _body, urls) in enumerate(reports, start=1):
        for u in urls:
            norm = _normalize_url(u)
            entry = counts.setdefault(
                norm, {"url": u.strip(), "norm": norm, "count": 0, "reports": []}
            )
            entry["count"] += 1
            label = f"report-{i:02d}.md"
            if label not in entry["reports"]:
                entry["reports"].append(label)
    return sorted(counts.values(), key=lambda e: (-e["count"], e["norm"]))


def _compose_reports(
    api_key: str,
    prompt: str,
    reports: list[tuple[int, str, str, list[str]]],
    missing: list[dict],
) -> tuple[str | None, str, dict]:
    """Pass 1: compose prose only — no citation re-attachment yet."""
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
    compose_prompt = (
        "Compose ONE markdown research brief from the completed sub-reports.\n"
        "This is the COMPOSITION pass only — do NOT attach citation markers "
        "yet; a later pass will re-attach them.\n"
        "Requirements:\n"
        "1. Cluster related claims across sub-reports into coherent prose.\n"
        "2. Include ## Contradictions listing every point where sub-reports "
        "disagree. SURFACING is mandatory — do NOT pick a winner, reconcile, "
        "or silently drop either side. Phrase each side with its observation "
        "date: 'A reports X as of <date>' / 'B reports Y as of <date>' so a "
        "staleness gap is visible as one. If a side gives no date, write "
        "'as of (undated)'.\n"
        "3. If any sub-questions are missing, include ## Missing sub-questions "
        "naming each and the reason (timed out / failed).\n"
        "4. Start with # Merged research and a short ## Summary.\n"
        "5. Output markdown only. Do not invent sources or resolve conflicts.\n\n"
        f"Original question:\n{prompt}\n"
        f"{missing_block}\n"
        + "\n".join(blocks)
    )
    resp = _call_sol_text(api_key, compose_prompt)
    if resp.get("error"):
        return None, f"compose failed: {resp['error']}", resp
    text = _output_text(resp).strip()
    if not text:
        return None, "compose returned empty text", resp
    return text, "", resp


def _attach_citations(
    api_key: str,
    composed: str,
    reports: list[tuple[int, str, str, list[str]]],
) -> tuple[str | None, str, dict]:
    """Pass 2: re-attach per-claim citations; separate from composition."""
    citation_rows = _citation_union([(q, b, u) for _, q, b, u in reports])
    cite_lines = "\n".join(
        f"- {c['url']} (norm={c['norm']}; corroboration={c['count']}; "
        f"{', '.join(c['reports'])})"
        for c in citation_rows
    ) or "- (none)"
    report_blobs = []
    for i, question, body, urls in reports:
        report_blobs.append(
            f"report-{i:02d}.md | {question}\n"
            f"URLs: {', '.join(urls) if urls else '(none)'}\n"
            f"Excerpt:\n{body[:2000]}\n"
        )
    attach_prompt = (
        "Citation re-attachment pass. The composed brief below has NO "
        "citation markers yet. Attach per-claim source attribution of the "
        "form [report-NN.md | URL], using the citation union and sub-reports.\n"
        "Rules:\n"
        "1. Claim-level citation unions over normalized URLs — but do NOT "
        "collapse distinct sources: different document revisions, separate "
        "filings, a primary source and a summary of it, or similar-titled "
        "papers must stay separate. When in doubt, keep them separate.\n"
        "2. Include ## Citations with the URL union below, preserving "
        "corroboration counts.\n"
        "3. Preserve ## Contradictions exactly (including per-side "
        "'as of <date>' phrasing); only add citation markers, never smooth "
        "disagreement away.\n"
        "4. Output the full markdown brief with citations attached. "
        "Do not invent sources.\n\n"
        f"Composed brief:\n{composed}\n\n"
        f"Citation union:\n{cite_lines}\n\n"
        "Sub-reports:\n" + "\n".join(report_blobs)
    )
    resp = _call_sol_text(api_key, attach_prompt)
    if resp.get("error"):
        return None, f"citation attach failed: {resp['error']}", resp
    text = _output_text(resp).strip()
    if not text:
        return None, "citation attach returned empty text", resp
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
        "Citation check pass. For each claim in the merged brief, verify:\n"
        "1. The cited URL resolves (or is present in the sub-report sources).\n"
        "2. It is the correct document for that claim (not a neighbour's).\n"
        "3. The document actually supports the adjacent claim.\n"
        "4. Numerics and dates in the claim match the source.\n"
        "5. No citations were inherited from a neighbouring claim.\n"
        "6. Conflicts / contradictions are still preserved, not smoothed away.\n"
        "Return markdown:\n"
        "## Citation check\n"
        "- OK / WEAK / MISSING lines per claim (brief).\n"
        "Do not rewrite the brief. Do not resolve contradictions.\n\n"
        f"Merged brief:\n{merged}\n\n"
        "Sub-reports:\n" + "\n".join(report_blobs)
    )
    resp = _call_planner(api_key, check_prompt)
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


def _count_web_queries(data: dict) -> int:
    n = 0
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "")
        if "web_search" in t:
            n += 1
            continue
        if t == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and "web_search" in str(
                    block.get("type") or ""
                ):
                    n += 1
    return n


def _domain_of(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _mean_qpd(
    completed_data: list[dict], url_lists: list[list[str]]
) -> float:
    total_q = 0
    total_d = 0
    for data, urls in zip(completed_data, url_lists):
        total_q += _count_web_queries(data)
        total_d += len({_normalize_url(u) for u in urls})
    if total_d == 0:
        return 0.0
    return total_q / total_d


def _citation_overlap_ratio(url_lists: list[list[str]]) -> float:
    sets = [{_normalize_url(u) for u in urls} for urls in url_lists]
    if len(sets) < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            total += _jaccard(sets[i], sets[j])
            pairs += 1
    return total / pairs if pairs else 0.0


def _unique_domains(url_lists: list[list[str]]) -> int:
    domains: set[str] = set()
    for urls in url_lists:
        for u in urls:
            d = _domain_of(u)
            if d:
                domains.add(d)
    return len(domains)


def _count_contradictions(merged: str) -> int:
    if "## Contradictions" not in merged:
        return 0
    section = merged.split("## Contradictions", 1)[1]
    next_h = re.search(r"\n##\s+", section)
    if next_h:
        section = section[: next_h.start()]
    count = 0
    for line in section.splitlines():
        s = line.strip()
        if not s.startswith("-"):
            continue
        body = s.lstrip("-").strip().lower()
        if not body or body.startswith("none"):
            continue
        count += 1
    return count


def _format_metrics(metrics: dict) -> str:
    lines = [
        "--- fanout metrics ---",
        f"mean QPD: {metrics['mean_qpd']:.3f}",
        f"citation overlap ratio: {metrics['citation_overlap_ratio']:.3f}",
        f"unique authoritative domains: {metrics['unique_domains']}",
        f"contradiction count: {metrics['contradiction_count']}",
        f"total cost: {metrics['total_cost_label']}",
    ]
    if metrics.get("qpd_warning"):
        lines.append(
            f"WARNING: mean QPD {metrics['mean_qpd']:.3f} "
            f"< {QPD_WARN_THRESHOLD} (retrieval-efficiency gate)"
        )
    lines.append("---")
    return "\n".join(lines) + "\n"


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


def run_research_plan_only(
    prompt_parts: list[str], n: int, *, n_source: str = "flag"
) -> int:
    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    prompt = " ".join(prompt_parts)
    auto = n == FANOUT_AUTO
    if not auto:
        n, was_clamped = _clamp_fanout(n)
        if was_clamped:
            sys.stderr.write(
                f"Clamped --fanout to {n} (allowed {FANOUT_MIN}-{FANOUT_MAX})\n"
            )

    brief, candidates, adversarial, recommended, err, decomp_resp = (
        _brief_and_candidates(api_key, prompt)
    )
    if brief is None or candidates is None or adversarial is None or recommended is None:
        print(json.dumps({"error": err}))
        return 1
    inp, out = _usage_tokens(decomp_resp)
    planner_cost = _est_planner_usd(inp, out)

    if auto:
        n = recommended
        n_source = "recommendation"
        sys.stderr.write(
            f"Using fanout N={n} (from Stage 0 recommendation)\n"
        )
    else:
        sys.stderr.write(
            f"Using fanout N={n} (from --fanout flag"
            f"; Stage 0 recommended {recommended})\n"
        )

    if n <= 1:
        sys.stderr.write(
            "recommended/selected N=1 — no fanout jobs; plan-only exits "
            "without submitting research.\n"
        )
        print(f"# Research plan (N=1, single-shot)\n")
        print("## Brief\n")
        print(brief)
        print()
        print("## Selected sub-questions\n")
        print("(none — N=1 uses the single-shot path)\n")
        print("## Adversarial (reserved, unused at N=1)\n")
        print(f"- {adversarial['angle']}")
        print(f"  seed_query: {adversarial['seed_query']}")
        return 0

    selected = select_with_adversarial(candidates, adversarial, n)
    sys.stderr.write(
        f"Plan-only: {n} sub-questions "
        f"({n - 1} MMR + 1 reserved adversarial) from "
        f">={CANDIDATE_FLOOR} candidates "
        f"(planner estimate ${planner_cost:.4f}); no research jobs submitted.\n"
    )
    print(f"# Research plan (N={n}, source={n_source})\n")
    print("## Brief\n")
    print(brief)
    print()
    print("## Selected sub-questions\n")
    for i, s in enumerate(selected, start=1):
        tag = " [adversarial]" if s is selected[-1] else ""
        print(f"{i}. {s['angle']}{tag}")
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
    quorum: int | None = None,
) -> tuple[dict[int, dict], dict[int, str]]:
    """Poll a set of background jobs until quorum or all settle.

    Persists each completion immediately (OpenAI retains background results
    ~10 minutes — deferred writes are a data-loss bug).
    """
    if timeout_s is None:
        timeout_s = float(JOB_TIMEOUT_S)
    n = len(jobs)
    if quorum is None:
        quorum = _merge_quorum(n)
    completed: dict[int, dict] = {}
    terminal: dict[int, str] = {}
    started_at = {i: time.monotonic() for i in jobs}
    pending = set(jobs)

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
    prompt_parts: list[str],
    n: int,
    *,
    plan_only: bool = False,
    n_source: str | None = None,
) -> int:
    if n_source is None:
        n_source = "recommendation" if n == FANOUT_AUTO else "flag"

    if plan_only:
        return run_research_plan_only(prompt_parts, n, n_source=n_source)

    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1

    auto = n == FANOUT_AUTO
    if not auto:
        n, was_clamped = _clamp_fanout(n)
        if was_clamped:
            sys.stderr.write(
                f"Clamped --fanout to {n} (allowed {FANOUT_MIN}-{FANOUT_MAX})\n"
            )
        if n == 1:
            return run_research(prompt_parts)

    prompt = " ".join(prompt_parts)
    prompt_hash = _get_hash(prompt)
    run_dir = _research_cache_dir() / f"fanout-{prompt_hash}"
    manifest_path = run_dir / "manifest.json"
    planner_cost = 0.0

    if manifest_path.is_file() and not auto:
        run_dir.mkdir(parents=True, exist_ok=True)
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
        sys.stderr.write(f"Using fanout N={n} (from --fanout flag)\n")
    else:
        sys.stderr.write(
            f"Planning fanout: brief + >={CANDIDATE_FLOOR} candidate angles, "
            f"MMR-select N-1 + reserved adversarial…\n"
        )
        brief, candidates, adversarial, recommended, err, decomp_resp = (
            _brief_and_candidates(api_key, prompt)
        )
        if (
            brief is None
            or candidates is None
            or adversarial is None
            or recommended is None
        ):
            print(json.dumps({"error": err}))
            return 1
        inp, out = _usage_tokens(decomp_resp)
        planner_cost += _est_planner_usd(inp, out)

        if auto:
            n = recommended
            n_source = "recommendation"
            sys.stderr.write(
                f"Using fanout N={n} (from Stage 0 recommendation)\n"
            )
            if n <= 1:
                sys.stderr.write(
                    "Stage 0 recommended N=1 — falling through to single-shot "
                    "(zero fanout jobs).\n"
                )
                return run_research(prompt_parts)
        else:
            sys.stderr.write(
                f"Using fanout N={n} (from --fanout flag"
                f"; Stage 0 recommended {recommended})\n"
            )

        tool_budget = _max_tool_calls(n)
        selected = select_with_adversarial(candidates, adversarial, n)
        if len(selected) < n:
            print(
                json.dumps(
                    {
                        "error": (
                            f"selected {len(selected)} angles, need {n}"
                        )
                    }
                )
            )
            return 1
        sub_prompts = [
            _build_sub_prompt(prompt, brief, s, selected) for s in selected
        ]
        sys.stderr.write(
            f"Plan ready (planner estimate ${planner_cost:.4f}); "
            f"max_tool_calls={tool_budget} per job; "
            f"{n - 1} MMR + 1 adversarial\n"
        )
        for i, s in enumerate(selected, start=1):
            tag = " [adversarial]" if i == n else ""
            sys.stderr.write(
                f"  {i}. {s['angle']}{tag}  [{s['seed_query']}]\n"
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "prompt": prompt,
            "n": n,
            "n_source": n_source,
            "recommended_fanout": recommended,
            "model": RESEARCH_MODEL,
            "planner_model": PLANNER_MODEL,
            "brief": brief,
            "selected": selected,
            "adversarial": adversarial,
            "sub_prompts": sub_prompts,
            "max_tool_calls": tool_budget,
            "jobs": [],
        }
        _save_manifest(manifest_path, manifest)

    tool_budget = int(manifest.get("max_tool_calls") or _max_tool_calls(n))

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

    manifest["jobs"] = [
        existing_jobs[i] for i in range(1, n + 1) if i in existing_jobs
    ]
    _save_manifest(manifest_path, manifest)

    live = {i: rec for i, rec in job_records.items() if rec.get("id")}
    for i, j in existing_jobs.items():
        if j.get("state") == "completed" and i not in live:
            live[i] = {"id": j.get("id") or "cached", "key": j.get("key", "")}

    submitted = sum(
        1
        for j in manifest["jobs"]
        if j.get("state") in ("submitted", "completed") and j.get("id")
    )
    unverified = UNVERIFIED_EST_RESEARCH_JOB_USD
    sys.stderr.write(
        f"Jobs submitted/rejoined: {submitted}/{n} "
        f"(unverified estimate {submitted} x ${unverified:.2f} = "
        f"${submitted * unverified:.2f} research + "
        f"${planner_cost:.4f} planner; max_tool_calls={tool_budget})\n"
    )
    if not live:
        print(json.dumps({"error": "all fanout submits failed"}))
        return 1

    quorum = _merge_quorum(n)
    completed, terminal = _poll_set(
        api_key,
        live,
        run_dir,
        manifest,
        manifest_path,
        quorum=quorum,
    )

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
    completed_data: list[dict] = []
    url_lists: list[list[str]] = []
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
        completed_data.append(data)
        url_lists.append(urls)

    sys.stderr.write(
        f"Composing {len(reports)}/{n} completed sub-reports "
        f"({len(missing)} missing)…\n"
    )
    composed, err, compose_resp = _compose_reports(
        api_key, prompt, reports, missing
    )
    if composed is None:
        print(json.dumps({"error": err, "run_dir": str(run_dir)}))
        return 1
    compose_usage = _usage_tokens(compose_resp)

    sys.stderr.write("Attaching citations (separate pass)…\n")
    merged, err, attach_resp = _attach_citations(api_key, composed, reports)
    if merged is None:
        print(json.dumps({"error": err, "run_dir": str(run_dir)}))
        return 1
    attach_usage = _usage_tokens(attach_resp)

    check_text, check_err, check_resp = _citation_check(api_key, merged, reports)
    check_usage = (0, 0)
    if check_text is None:
        sys.stderr.write(f"Citation check skipped: {check_err}\n")
    else:
        check_usage = _usage_tokens(check_resp)
        planner_cost += _est_planner_usd(*check_usage)
        if not merged.endswith("\n"):
            merged += "\n"
        merged = merged + "\n" + check_text
        if not merged.endswith("\n"):
            merged += "\n"

    if missing:
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

    mean_qpd = _mean_qpd(completed_data, url_lists)
    overlap = _citation_overlap_ratio(url_lists)
    domains = _unique_domains(url_lists)
    contra = _count_contradictions(merged)
    research_est = submitted * UNVERIFIED_EST_RESEARCH_JOB_USD
    planner_actual = planner_cost
    # Sol compose/attach: usage reported but no verified $/M — label estimated.
    sol_tokens = compose_usage[0] + compose_usage[1] + attach_usage[0] + attach_usage[1]
    total_est = research_est + planner_actual
    cost_label = (
        f"${total_est:.4f} "
        f"(research ${research_est:.2f} unverified estimate; "
        f"planner ${planner_actual:.4f} from reported usage @ "
        f"${EST_PLANNER_INPUT_PER_M:.0f}/${EST_PLANNER_OUTPUT_PER_M:.0f} per 1M; "
        f"sol compose+attach {sol_tokens} tokens reported, $/M unverified)"
    )
    metrics = {
        "mean_qpd": mean_qpd,
        "citation_overlap_ratio": overlap,
        "unique_domains": domains,
        "contradiction_count": contra,
        "total_cost_usd": total_est,
        "total_cost_label": cost_label,
        "qpd_warning": mean_qpd < QPD_WARN_THRESHOLD,
        "research_cost_unverified_usd": research_est,
        "planner_cost_usd": planner_actual,
        "sol_compose_attach_tokens": sol_tokens,
    }
    metrics_text = _format_metrics(metrics)
    sys.stderr.write(metrics_text)

    manifest["state"] = "merged"
    manifest["completed"] = sorted(completed)
    manifest["missing"] = missing
    manifest["metrics"] = metrics
    _save_manifest(manifest_path, manifest)

    failed_or_missing = n - len(completed)
    sys.stderr.write(
        f"Fanout complete: {len(completed)}/{n} jobs ok, "
        f"{failed_or_missing} missing/failed\n"
        f"Run directory: {run_dir}\n"
    )
    print(str(run_dir))
    return 0 if not missing else 1
