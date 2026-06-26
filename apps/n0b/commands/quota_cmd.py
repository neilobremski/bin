"""AI tool quota checks — starting with Antigravity (agy)."""
from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LIVE_ENDPOINTS = {
    "userStatus": "/exa.language_server_pb.LanguageServerService/GetUserStatus",
    "commandModelConfigs": "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs",
    "userQuotaSummary": "/exa.language_server_pb.LanguageServerService/GetUserQuotaSummary",
    "cascadeModelConfig": "/exa.language_server_pb.LanguageServerService/GetCascadeModelConfigData",
}

REQUEST_TIMEOUT_S = 8

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_flag(command: str, flag: str) -> str | None:
    escaped = re.escape(flag)
    match = re.search(rf"{escaped}(?:=|\s+)([^\s]+)", command, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_number(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def detect_antigravity_process() -> dict[str, Any]:
    proc = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("Could not list processes (ps failed).")

    candidates: list[tuple[int, dict[str, Any]]] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\d+)\s+(.+)$", line.strip())
        if not match:
            continue
        pid = int(match.group(1))
        command = match.group(2)
        lower = command.lower()
        is_language_server = (
            "language_server" in lower
            or "agentapi" in lower
            or lower == "agy"
            or lower.endswith("/agy")
            or " /agy" in lower
        )
        is_antigravity = (
            "antigravity" in lower
            or "agy" in lower
            or ".gemini/antigravity-cli" in lower
        )
        if not is_language_server or not is_antigravity:
            continue

        score = 0
        if "language_server" in lower:
            score += 100
        if _extract_flag(command, "--csrf_token"):
            score += 50
        if "--continue" in lower:
            score += 10
        if "--print" in lower or "--prompt" in lower:
            score -= 25
        candidates.append(
            (
                score,
                {
                    "pid": pid,
                    "command": command,
                    "csrf_token": _extract_flag(command, "--csrf_token") or "",
                    "extension_port": _parse_number(_extract_flag(command, "--extension_server_port")),
                },
            )
        )

    if not candidates:
        raise RuntimeError(
            "Antigravity language server is not running. "
            "Start Antigravity IDE, an interactive agy session, or agy --continue."
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _cli_log_port_map(pid: int) -> dict[str, int]:
    log_dir = Path.home() / ".gemini" / "antigravity-cli" / "log"
    if not log_dir.is_dir():
        return {}

    for log_path in sorted(log_dir.glob("cli-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if f"language server process with pid {pid}" not in text:
            continue
        ports: dict[str, int] = {}
        for match in re.finditer(r"listening on random port at (\d+) for (HTTPS \(gRPC\)|HTTP)", text):
            port = int(match.group(1))
            if "HTTPS" in match.group(2):
                ports["https"] = port
            else:
                ports["http"] = port
        if ports:
            return ports
    return {}


def _lsof_listen_ports(pid: int) -> list[int]:
    lsof = next((p for p in ("/usr/bin/lsof", "/usr/sbin/lsof") if shutil.which(p)), None)
    if not lsof:
        raise RuntimeError("lsof is required to discover Antigravity's local API port.")

    proc = subprocess.run(
        [lsof, "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(
        {int(m.group(1)) for m in re.finditer(r":(\d+)\s+\(LISTEN\)", proc.stdout) if int(m.group(1)) > 0}
    )


def resolve_api_ports(process_info: dict[str, Any]) -> dict[str, int | None]:
    pid = process_info["pid"]
    port_map = _cli_log_port_map(pid)
    lsof_ports = _lsof_listen_ports(pid)
    https_port = port_map.get("https")
    http_port = port_map.get("http") or process_info.get("extension_port")

    if len(lsof_ports) >= 2:
        lower, upper = lsof_ports[0], lsof_ports[-1]
        https_port = https_port or lower
        http_port = http_port or upper
    elif len(lsof_ports) == 1:
        only = lsof_ports[0]
        https_port = https_port or only
        http_port = http_port or only

    if not https_port and not http_port:
        raise RuntimeError(
            f"Antigravity pid {pid} has no local quota API listeners. "
            "agy --print exits too quickly; use a persistent agy --continue or interactive session."
        )
    return {"https": https_port, "http": http_port}


def get_listening_ports(pid: int) -> list[int]:
    ports = resolve_api_ports({"pid": pid, "extension_port": None})
    return sorted({p for p in (ports["https"], ports["http"]) if p}, reverse=True)


def _default_request_body() -> dict[str, Any]:
    return {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "n0b-quota",
            "ideVersion": "unknown",
            "locale": "en",
        }
    }


def _post_local(
    port: int,
    csrf_token: str,
    path: str,
    body: dict[str, Any],
    *,
    use_ssl: bool,
    timeout: float = REQUEST_TIMEOUT_S,
) -> str:
    body_text = json.dumps(body).encode("utf-8")
    url = f"{'https' if use_ssl else 'http'}://127.0.0.1:{port}{path}"
    request = urllib.request.Request(
        url,
        data=body_text,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body_text)),
            "Connect-Protocol-Version": "1",
            "X-Codeium-Csrf-Token": csrf_token,
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=_SSL_CTX if use_ssl else None,
        ) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Antigravity API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Antigravity API request failed: {exc.reason}") from exc

    return data


def _make_request(
    https_port: int | None,
    http_port: int | None,
    csrf_token: str,
    path: str,
    body: dict[str, Any],
    *,
    timeout: float = REQUEST_TIMEOUT_S,
) -> str:
    last_error: RuntimeError | None = None
    if https_port:
        try:
            return _post_local(https_port, csrf_token, path, body, use_ssl=True, timeout=timeout)
        except RuntimeError as exc:
            last_error = exc
    if http_port and http_port != https_port:
        try:
            return _post_local(http_port, csrf_token, path, body, use_ssl=False, timeout=timeout)
        except RuntimeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("No Antigravity API port available.")


def fetch_raw_agy_quota() -> dict[str, Any]:
    process_info = detect_antigravity_process()
    api_ports = resolve_api_ports(process_info)
    api_port = api_ports["https"] or api_ports["http"]

    try:
        raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["userStatus"],
            _default_request_body(),
        )
        payload = json.loads(raw)
        parsed = parse_quota_response(payload, "userStatus")
        if parsed["models"]:
            return {"json": payload, "shape": "userStatus", "api_port": api_port}
    except (RuntimeError, json.JSONDecodeError):
        pass

    try:
        raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["cascadeModelConfig"],
            _default_request_body(),
        )
        status_raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["userStatus"],
            _default_request_body(),
        )
        payload = json.loads(raw)
        payload["userStatus"] = json.loads(status_raw).get("userStatus", {})
        parsed = parse_quota_response(payload, "cascadeModelConfig")
        if parsed["models"]:
            return {"json": payload, "shape": "cascadeModelConfig", "api_port": api_port}
    except (RuntimeError, json.JSONDecodeError):
        pass

    try:
        raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["userQuotaSummary"],
            _default_request_body(),
        )
        payload = json.loads(raw)
        parsed = parse_quota_response(payload, "quotaSummary")
        if parsed["models"]:
            return {"json": payload, "shape": "quotaSummary", "api_port": api_port}
    except (RuntimeError, json.JSONDecodeError):
        pass

    try:
        raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["userStatus"],
            _default_request_body(),
        )
        return {"json": json.loads(raw), "shape": "userStatus", "api_port": api_port}
    except (RuntimeError, json.JSONDecodeError):
        raw = _make_request(
            api_ports["https"],
            api_ports["http"],
            process_info["csrf_token"],
            LIVE_ENDPOINTS["commandModelConfigs"],
            _default_request_body(),
        )
        return {"json": json.loads(raw), "shape": "commandModelConfigs", "api_port": api_port}


def fetch_agy_quota(*, include_raw: bool = False) -> dict[str, Any]:
    raw = fetch_raw_agy_quota()
    parsed = parse_quota_response(raw["json"], raw["shape"])
    result = {
        "tool": "agy",
        "generated_at": _utc_now().isoformat(),
        "source": raw["shape"],
        "api_port": raw["api_port"],
        **parsed,
    }
    if include_raw:
        result["raw"] = raw["json"]
    return result


def _is_ok_code(code: Any) -> bool:
    if code is None:
        return True
    if isinstance(code, int):
        return code == 0
    if isinstance(code, str):
        return code.lower() in {"ok", "success", "0"}
    if isinstance(code, dict):
        return code.get("isOK") is True
    return False


def _read_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _read_first(node: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in node:
            return node[name]
    return None


def _parse_reset_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value)
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _title_case(value: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    spaced = re.sub(r"[_-]+", " ", spaced)
    return " ".join(part.capitalize() for part in spaced.split())


def _has_quota_shape(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(key in value for key in ("quotaInfo", "quotaSummary", "usageLimits", "commandQuota")):
        return True
    return any(re.search(r"quota|limit|remaining|reset|usage", key, re.I) for key in value)


def _walk_object(value: Any, visitor: Callable[[Any, list[str]], None], path: list[str] | None = None, seen: set[int] | None = None) -> None:
    if path is None:
        path = []
    if seen is None:
        seen = set()
    if not isinstance(value, (dict, list)) or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        visitor(value, path)
        for key, child in value.items():
            _walk_object(child, visitor, path + [str(key)], seen)
    else:
        for index, child in enumerate(value):
            _walk_object(child, visitor, path + [str(index)], seen)


def _find_model_configs(value: dict[str, Any]) -> list[dict[str, Any]]:
    direct = (
        value.get("cascadeModelConfigData", {}).get("clientModelConfigs")
        or value.get("clientModelConfigs")
        or value.get("modelConfigs")
        or value.get("models")
    )
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]

    arrays: list[list[dict[str, Any]]] = []

    def visitor(node: Any, path_parts: list[str]) -> None:
        if not isinstance(node, list):
            return
        serialized = ".".join(path_parts).lower()
        if any(
            isinstance(item, dict)
            and (
                "model" in serialized
                or item.get("model")
                or item.get("modelId")
                or item.get("modelOrAlias")
                or item.get("label")
            )
            and _has_quota_shape(item)
            for item in node
        ):
            arrays.append([item for item in node if isinstance(item, dict)])

    _walk_object(value, visitor)
    if not arrays:
        return []
    return max(arrays, key=len)


def _read_fraction(node: dict[str, Any]) -> float | None:
    remaining = _read_number(
        _read_first(node, ["remainingFraction", "remainingRatio", "remainingPercent", "remainingPercentage"])
    )
    if remaining is not None:
        return remaining / 100 if remaining > 1 else remaining

    used = _read_number(_read_first(node, ["used", "usedCount", "consumed", "consumedCount", "usage"]))
    limit = _read_number(_read_first(node, ["limit", "max", "maximum", "total", "quota", "capacity"]))
    if used is not None and limit and limit > 0:
        return max(0.0, min(1.0, 1.0 - used / limit))
    return None


def _quota_bucket_label(path_parts: list[str], node: dict[str, Any]) -> str:
    explicit = _read_first(node, ["label", "displayName", "name", "period", "bucket", "window"])
    if explicit and not isinstance(explicit, (dict, list)):
        return _title_case(str(explicit))

    text = " ".join(path_parts).lower()
    if re.search(r"hourly|perhour|per_hour|\bhour\b", text):
        return "Hourly"
    if re.search(r"weekly|perweek|per_week|\bweek\b", text):
        return "Weekly"
    if re.search(r"daily|perday|per_day|\bday\b", text):
        return "Daily"
    if re.search(r"monthly|permonth|per_month|\bmonth\b", text):
        return "Monthly"

    useful = [part for part in path_parts if not part.isdigit()]
    return _title_case(useful[-1]) if useful else "Quota"


def _extract_quota_buckets(quota: Any) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []

    def visitor(node: Any, path_parts: list[str]) -> None:
        if not isinstance(node, dict):
            return
        remaining_fraction = _read_fraction(node)
        reset_time = _parse_reset_time(_read_first(node, ["resetTime", "resetAt", "resetsAt", "nextResetTime"]))
        used = _read_number(_read_first(node, ["used", "usedCount", "consumed", "consumedCount", "usage"]))
        limit = _read_number(_read_first(node, ["limit", "max", "maximum", "total", "quota", "capacity"]))
        if remaining_fraction is None and reset_time is None and (used is None or limit is None):
            return
        buckets.append(
            {
                "label": _quota_bucket_label(path_parts, node),
                "remaining_fraction": remaining_fraction,
                "reset_time": reset_time.isoformat() if reset_time else None,
                "used": used,
                "limit": limit,
            }
        )

    _walk_object(quota, visitor)

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for bucket in buckets:
        key = "|".join(
            str(bucket.get(field, ""))
            for field in ("label", "remaining_fraction", "reset_time", "used", "limit")
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(bucket)
    return deduped


def _parse_model_quota(config: dict[str, Any]) -> dict[str, Any] | None:
    quota = (
        config.get("quotaInfo")
        or config.get("quotaSummary")
        or config.get("usageLimits")
        or config.get("commandQuota")
        or config
    )
    label = (
        config.get("label")
        or config.get("displayName")
        or config.get("name")
        or (config.get("modelOrAlias") or {}).get("model")
        or config.get("model")
        or "Unknown model"
    )
    buckets = _extract_quota_buckets(quota)
    if not buckets:
        return None

    for bucket in buckets:
        if bucket["label"] == "Quota":
            bucket["label"] = "Hourly"

    fractions = [b["remaining_fraction"] for b in buckets if isinstance(b["remaining_fraction"], (int, float))]
    reset_times = sorted(
        b["reset_time"] for b in buckets if b.get("reset_time")
    )
    return {
        "label": label,
        "model_id": (config.get("modelOrAlias") or {}).get("model") or config.get("model") or config.get("modelId"),
        "buckets": buckets,
        "remaining_fraction": min(fractions) if fractions else None,
        "reset_time": reset_times[0] if reset_times else None,
    }


QUOTA_GROUPS = [
    {
        "name": "GEMINI MODELS",
        "description": "Gemini Flash, Gemini Pro",
        "match": lambda label: "gemini" in label.lower(),
    },
    {
        "name": "CLAUDE AND GPT MODELS",
        "description": "Claude Opus, Claude Sonnet, GPT-OSS",
        "match": lambda label: "claude" in label.lower() or "gpt" in label.lower(),
    },
]


def _quota_window_for_label(label: str) -> str:
    lower = label.lower()
    if "gemini" in lower:
        return "weekly"
    return "five_hour"


def group_model_quotas(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for grp in QUOTA_GROUPS:
        matched = [model for model in models if grp["match"](model["label"])]
        if not matched:
            continue

        buckets: list[dict[str, Any]] = []
        for window, display in (("weekly", "Weekly Limit"), ("five_hour", "Five Hour Limit")):
            candidates: list[dict[str, Any]] = []
            for model in matched:
                if _quota_window_for_label(model["label"]) != window:
                    continue
                for bucket in model.get("buckets") or []:
                    candidates.append({**bucket, "label": display})
            if not candidates:
                buckets.append({"label": display, "remaining_fraction": None, "reset_time": None})
                continue
            best = min(
                candidates,
                key=lambda bucket: bucket["remaining_fraction"]
                if isinstance(bucket.get("remaining_fraction"), (int, float))
                else 1.0,
            )
            buckets.append(
                {
                    "label": display,
                    "remaining_fraction": best.get("remaining_fraction"),
                    "reset_time": best.get("reset_time"),
                    "used": best.get("used"),
                    "limit": best.get("limit"),
                }
            )

        grouped.append(
            {
                "name": grp["name"],
                "description": grp["description"],
                "buckets": buckets,
            }
        )
    return grouped


def parse_quota_response(response: dict[str, Any], shape: str) -> dict[str, Any]:
    if not _is_ok_code(response.get("code")):
        raise RuntimeError("Antigravity quota API returned a non-OK response.")

    if shape == "userStatus":
        status = response.get("userStatus", response)
    else:
        status = response
    configs = _find_model_configs(status)
    models = sorted(
        (m for m in (_parse_model_quota(cfg) for cfg in configs) if m),
        key=lambda item: item["label"].lower(),
    )
    account_root = response.get("userStatus", status)
    plan_status = account_root.get("planStatus") or {}
    plan_info = plan_status.get("planInfo") or {}
    return {
        "error": None,
        "account": {
            "email": account_root.get("email"),
            "plan": plan_info.get("planDisplayName")
            or plan_info.get("displayName")
            or plan_info.get("productName")
            or plan_info.get("planName"),
        },
        "available_prompt_credits": plan_status.get("availablePromptCredits"),
        "models": models,
        "groups": group_model_quotas(models),
    }


def _format_duration_until(iso_time: str | None) -> str | None:
    if not iso_time:
        return None
    try:
        reset = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    diff_mins = max(0, round((reset - _utc_now()).total_seconds() / 60))
    hours, mins = divmod(diff_mins, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _format_bucket(bucket: dict[str, Any]) -> str:
    fraction = bucket.get("remaining_fraction")
    if not isinstance(fraction, (int, float)):
        return "unknown"
    if fraction >= 0.9999 and bucket.get("label") == "Five Hour Limit":
        return "Quota available"
    percent = fraction * 100
    percent_text = f"{round(percent)}%" if abs(percent - round(percent)) < 0.05 else f"{percent:.2f}%"
    reset = _format_duration_until(bucket.get("reset_time"))
    if reset:
        return f"{percent_text} remaining · refreshes in {reset}"
    return f"{percent_text} remaining"


def format_agy_quota_text(payload: dict[str, Any]) -> str:
    lines = ["Antigravity (agy)"]
    plan = (payload.get("account") or {}).get("plan")
    if plan:
        lines[0] += f" — {plan}"

    if payload.get("error"):
        lines.append(f"Error: {payload['error']}")
        return "\n".join(lines)

    groups = payload.get("groups") or []
    if not groups:
        lines.append("No model quota data returned.")
        return "\n".join(lines)

    for group in groups:
        lines.extend(["", group["name"], f"({group['description']})"])
        for bucket in group.get("buckets") or []:
            lines.append(f"  {bucket.get('label', 'Quota')}: {_format_bucket(bucket)}")

    credits = payload.get("available_prompt_credits")
    if credits is not None:
        lines.extend(["", f"Prompt credits: {credits}"])
    return "\n".join(lines)


QUOTA_TOOLS: dict[str, dict[str, Any]] = {
    "agy": {
        "name": "Antigravity",
        "installed": lambda: shutil.which("agy") is not None,
        "fetch": fetch_agy_quota,
        "format": format_agy_quota_text,
    },
}


def available_tool_names() -> list[str]:
    return sorted(name for name, meta in QUOTA_TOOLS.items() if meta["installed"]())


def cmd_quota(tools: list[str] | None, *, as_json: bool = False, raw: bool = False) -> int:
    selected = [t.lower() for t in tools] if tools else available_tool_names()
    if not selected:
        print("n0b quota: no supported AI tools found on PATH.", file=sys.stderr)
        print("Supported tools: agy (Antigravity)", file=sys.stderr)
        return 1

    unknown = [t for t in selected if t not in QUOTA_TOOLS]
    if unknown:
        print(
            f"n0b quota: unknown tool(s): {', '.join(unknown)}",
            file=sys.stderr,
        )
        print(f"Supported: {', '.join(sorted(QUOTA_TOOLS))}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    exit_code = 0
    for tool in selected:
        meta = QUOTA_TOOLS[tool]
        if not meta["installed"]():
            payload = {"tool": tool, "error": f"{meta['name']} is not installed"}
            results.append(payload)
            exit_code = 1
            continue
        try:
            payload = meta["fetch"](include_raw=raw)
        except RuntimeError as exc:
            payload = {"tool": tool, "error": str(exc)}
            exit_code = 1
        results.append(payload)

    if as_json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
    else:
        chunks = [meta["format"](payload) for meta, payload in zip((QUOTA_TOOLS[r["tool"]] for r in results), results)]
        print("\n\n".join(chunks))
    return exit_code
