"""OpenAI deep research via the Responses API (stdlib only).

Uses ``gpt-5.6-sol`` (OpenAI's replacement after ``o4-mini-deep-research``
shut down 2026-07-23).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from commands.secrets_cmd import resolve
from paths import BIN_ROOT

RESEARCH_MODEL = "gpt-5.6-sol"
CHEAP_MODEL = "gpt-4.1-mini"
POLL_INTERVAL_S = 30
# Rough per-job estimate until a completed response carries usage.
EST_RESEARCH_JOB_USD = 0.50
# Rough gpt-4.1-mini $/1M tokens (estimate; labeled as such in stderr).
EST_CHEAP_INPUT_PER_M = 0.40
EST_CHEAP_OUTPUT_PER_M = 1.60


def _get_hash(prompt: str) -> str:
    clean = "".join(prompt.split())
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _research_cache_dir() -> Path:
    d = BIN_ROOT / ".files" / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _call_openai(api_key: str, prompt: str) -> dict:
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": RESEARCH_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "background": True,
    }
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


def _load_or_submit(api_key: str, prompt: str, cache_file: Path) -> dict:
    if cache_file.is_file():
        return json.loads(cache_file.read_text())
    response_data = _call_openai(api_key, prompt)
    if response_data.get("error"):
        return response_data
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(response_data))
    return response_data


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
    # Fallbacks some Responses shapes use.
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


def _parse_json_list(text: str) -> object | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    bracket = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket:
        try:
            return json.loads(bracket.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _decompose(api_key: str, prompt: str, n: int) -> tuple[list[str] | None, str, dict]:
    decompose_prompt = (
        f"Decompose the research question below into exactly {n} complementary "
        "sub-questions. Each sub-question must cover a DISTINCT dimension "
        "(examples: mechanism, evidence, counter-evidence, adoption/practice, "
        "risks, alternatives). Do NOT paraphrase the original; avoid overlap.\n\n"
        f"Return ONLY a JSON array of exactly {n} strings. No other text.\n\n"
        f"Question:\n{prompt}"
    )
    resp = _call_cheap(api_key, decompose_prompt)
    if resp.get("error"):
        return None, f"decompose failed: {resp['error']}", resp
    text = _output_text(resp)
    parsed = _parse_json_list(text)
    if not isinstance(parsed, list):
        return None, f"decompose did not return a JSON list (got: {text[:200]!r})", resp
    if len(parsed) != n:
        return None, f"decompose returned {len(parsed)} items, expected {n}", resp
    if not all(isinstance(x, str) and x.strip() for x in parsed):
        return None, "decompose list must contain only non-empty strings", resp
    return [x.strip() for x in parsed], "", resp


def _merge_reports(
    api_key: str, prompt: str, reports: list[tuple[str, str, list[str]]]
) -> tuple[str | None, str, dict]:
    blocks: list[str] = []
    for i, (question, body, urls) in enumerate(reports, start=1):
        url_lines = "\n".join(f"- {u}" for u in urls) or "- (none extracted)"
        blocks.append(
            f"### Sub-report {i}\n"
            f"File: report-{i:02d}.md\n"
            f"Question: {question}\n\n"
            f"{body}\n\n"
            f"Cited URLs:\n{url_lines}\n"
        )
    merge_prompt = (
        "Merge the sub-reports below into a single markdown research brief.\n"
        "Requirements:\n"
        "1. Union the findings. Every claim must have per-claim source "
        "attribution of the form [report-NN.md | URL] (use the sub-report "
        "filename and the cited URL that supports the claim; if no URL, "
        "use [report-NN.md | no-url]).\n"
        "2. Include an explicit ## Conflicts section listing points where "
        "sub-reports disagree, with both sides attributed the same way.\n"
        "3. Start with # Merged research and a short ## Summary.\n"
        "4. Output markdown only.\n\n"
        f"Original question:\n{prompt}\n\n"
        + "\n".join(blocks)
    )
    resp = _call_cheap(api_key, merge_prompt)
    if resp.get("error"):
        return None, f"merge failed: {resp['error']}", resp
    text = _output_text(resp).strip()
    if not text:
        return None, "merge returned empty text", resp
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


def _poll_one(
    api_key: str, response_id: str, label: str
) -> tuple[str, dict]:
    while True:
        status_data = _check_status(api_key, response_id)
        status = status_data.get("status", "unknown")
        if status == "completed":
            sys.stderr.write(f"[{label}] completed\n")
            return "completed", status_data
        if status == "failed":
            sys.stderr.write(f"[{label}] failed\n")
            return "failed", status_data
        if status_data.get("error") and "status" not in status_data:
            sys.stderr.write(f"[{label}] error: {status_data.get('error')}\n")
            return "error", status_data
        sys.stderr.write(
            f"[{label}] status={status} — polling again in {POLL_INTERVAL_S}s\n"
        )
        time.sleep(POLL_INTERVAL_S)


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


def run_research_fanout(prompt_parts: list[str], n: int) -> int:
    api_key = resolve("OPENAI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not found (try: n0b secrets set OPENAI_API_KEY)"}))
        return 1
    if not prompt_parts:
        print(json.dumps({"error": "No prompt provided"}))
        return 1
    if n < 2:
        print(json.dumps({"error": "--fanout N requires N >= 2"}))
        return 1

    prompt = " ".join(prompt_parts)
    prompt_hash = _get_hash(prompt)
    run_dir = _research_cache_dir() / f"fanout-{prompt_hash}"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_file = run_dir / "meta.json"

    cheap_cost = 0.0
    sub_questions: list[str]

    if meta_file.is_file():
        meta = json.loads(meta_file.read_text())
        sub_questions = list(meta["sub_questions"])
        if len(sub_questions) != n:
            print(
                json.dumps(
                    {
                        "error": (
                            f"cached fanout has {len(sub_questions)} sub-questions, "
                            f"but --fanout {n} was requested"
                        )
                    }
                )
            )
            return 1
        sys.stderr.write(
            f"Rejoining fanout run at {run_dir} ({n} sub-questions from cache)\n"
        )
    else:
        sys.stderr.write(f"Decomposing into {n} complementary sub-questions…\n")
        sub_questions, err, decomp_resp = _decompose(api_key, prompt, n)
        if sub_questions is None:
            print(json.dumps({"error": err}))
            return 1
        inp, out = _usage_tokens(decomp_resp)
        cheap_cost += _est_cheap_usd(inp, out)
        sys.stderr.write(
            f"Decompose done ({inp} in / {out} out tokens; "
            f"cheap-call estimate ${cheap_cost:.4f})\n"
        )
        for i, q in enumerate(sub_questions, start=1):
            sys.stderr.write(f"  {i}. {q}\n")
        meta_file.write_text(
            json.dumps(
                {"prompt": prompt, "n": n, "sub_questions": sub_questions},
                indent=2,
            )
            + "\n"
        )

    # Submit (or rejoin) all jobs — parallel submits, shared cache files.
    job_ids: list[str] = []

    def _submit_one(i: int, question: str) -> tuple[int, dict, bool]:
        cache_file = run_dir / f"job-{i}.json"
        rejoined = cache_file.is_file()
        return i, _load_or_submit(api_key, question, cache_file), rejoined

    with ThreadPoolExecutor(max_workers=min(n, 8)) as pool:
        futures = [
            pool.submit(_submit_one, i, q) for i, q in enumerate(sub_questions, start=1)
        ]
        results: dict[int, tuple[dict, bool]] = {}
        for fut in as_completed(futures):
            i, data, rejoined = fut.result()
            results[i] = (data, rejoined)

    for i in range(1, n + 1):
        data, rejoined = results[i]
        if data.get("error"):
            job_ids.append("")
            sys.stderr.write(f"[job-{i}] submit error: {data.get('error')}\n")
            continue
        rid = data.get("id") or ""
        job_ids.append(rid)
        sys.stderr.write(
            f"[job-{i}] {'rejoined' if rejoined else 'submitted'} id={rid}\n"
        )

    submitted = sum(1 for j in job_ids if j)
    sys.stderr.write(
        f"Jobs submitted/rejoined: {submitted}/{n} "
        f"(estimate {submitted} x ${EST_RESEARCH_JOB_USD:.2f} = "
        f"${submitted * EST_RESEARCH_JOB_USD:.2f} research + "
        f"${cheap_cost:.4f} cheap-calls)\n"
    )
    if submitted == 0:
        print(json.dumps({"error": "all fanout submits failed"}))
        return 1

    # Poll all jobs concurrently (each thread polls its own job).
    completed: dict[int, dict] = {}
    failed = 0
    pending = {i: job_ids[i - 1] for i in range(1, n + 1) if job_ids[i - 1]}

    with ThreadPoolExecutor(max_workers=min(n, 8)) as pool:
        future_map = {
            pool.submit(_poll_one, api_key, rid, f"job-{i}"): i
            for i, rid in pending.items()
        }
        done_count = 0
        total = len(future_map)
        for fut in as_completed(future_map):
            i = future_map[fut]
            status, data = fut.result()
            done_count += 1
            running = total - done_count
            if status == "completed":
                completed[i] = data
                sys.stderr.write(
                    f"Progress: {done_count} done, {running} running "
                    f"(research estimate ${submitted * EST_RESEARCH_JOB_USD:.2f} + "
                    f"cheap ${cheap_cost:.4f})\n"
                )
            else:
                failed += 1
                sys.stderr.write(
                    f"Progress: {done_count} done ({failed} failed), {running} running\n"
                )

    reports: list[tuple[str, str, list[str]]] = []
    for i, question in enumerate(sub_questions, start=1):
        data = completed.get(i)
        if data is None:
            body = "(job did not complete)"
            urls: list[str] = []
        else:
            body = _output_text(data)
            urls = _extract_urls(body)
            # Also pull URL annotations if present.
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
        report_path = run_dir / f"report-{i:02d}.md"
        _write_sub_report(report_path, i, question, body, urls)
        reports.append((question, body, urls))

    sys.stderr.write("Merging sub-reports…\n")
    merged, err, merge_resp = _merge_reports(api_key, prompt, reports)
    if merged is None:
        print(json.dumps({"error": err, "run_dir": str(run_dir)}))
        return 1
    inp, out = _usage_tokens(merge_resp)
    cheap_cost += _est_cheap_usd(inp, out)
    (run_dir / "merged.md").write_text(merged if merged.endswith("\n") else merged + "\n")

    est_total = submitted * EST_RESEARCH_JOB_USD + cheap_cost
    sys.stderr.write(
        f"Fanout complete: {len(completed)}/{n} jobs ok, {failed} failed\n"
        f"Cost estimate: {submitted} research jobs x ${EST_RESEARCH_JOB_USD:.2f} "
        f"= ${submitted * EST_RESEARCH_JOB_USD:.2f} (estimate) + "
        f"cheap calls ${cheap_cost:.4f} (estimate) ≈ ${est_total:.2f}\n"
        f"Run directory: {run_dir}\n"
    )
    print(str(run_dir))
    return 0 if failed == 0 and len(completed) == n else 1
