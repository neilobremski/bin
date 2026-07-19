"""Shared hint/replacement helpers for n0b ai speak and transcribe."""
from __future__ import annotations

import sys
from pathlib import Path


def read_hints(hints_file: Path) -> list[str]:
    if not hints_file.is_file():
        return []
    hints: list[str] = []
    for line in hints_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hints.append(line)
    return hints


def split_cli_hints(cli_hints: list[str]) -> list[str]:
    return [p.strip() for h in cli_hints for p in h.split(",") if p.strip()]


def merged_hints(cli_hints: list[str], hints_file: Path) -> str:
    return ", ".join(read_hints(hints_file) + split_cli_hints(cli_hints))


def parse_replacement(line: str) -> tuple[str, str] | None:
    if "=>" not in line:
        return None
    pattern, correction = line.split("=>", 1)
    pattern, correction = pattern.strip(), correction.strip()
    if not pattern or not correction:
        return None
    return pattern, correction


def read_pair_file(pair_file: Path, label: str = "n0b") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not pair_file.is_file():
        return pairs
    for line in pair_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pair = parse_replacement(line)
        if pair is None:
            print(
                f"{label}: skipping bad line (want 'left => right'): {line!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


def parse_cli_pairs(cli_values: list[str], label: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in cli_values:
        pair = parse_replacement(raw)
        if pair is None:
            print(
                f"{label}: bad value (want 'left => right'): {raw!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


def save_pair_file(cli_values: list[str], pair_file: Path, label: str) -> int:
    new = parse_cli_pairs(cli_values, label)
    if not new:
        return 2
    known = {pattern for pattern, _ in read_pair_file(pair_file, label)}
    added = [(p, r) for p, r in new if p not in known]
    if added:
        pair_file.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if pair_file.is_file():
            text = pair_file.read_text()
            if text and not text.endswith("\n"):
                lead = "\n"
        with pair_file.open("a") as f:
            f.write(lead + "".join(f"{p} => {r}\n" for p, r in added))
    print(
        f"saved {len(added)} entry(ies) to {pair_file}"
        + (f" ({len(new) - len(added)} already there)" if len(added) < len(new) else ""),
        file=sys.stderr,
    )
    return 0


def save_hints(cli_hints: list[str], hints_file: Path) -> int:
    new = split_cli_hints(cli_hints)
    if not new:
        return 2
    existing = read_hints(hints_file)
    known = {h.lower() for h in existing}
    added = []
    for hint in new:
        if hint.lower() not in known:
            known.add(hint.lower())
            added.append(hint)
    if added:
        hints_file.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if hints_file.is_file():
            text = hints_file.read_text()
            if text and not text.endswith("\n"):
                lead = "\n"
        with hints_file.open("a") as f:
            f.write(lead + "".join(f"{h}\n" for h in added))
    print(
        f"saved {len(added)} hint(s) to {hints_file}"
        + (f" ({len(new) - len(added)} already there)" if len(added) < len(new) else ""),
        file=sys.stderr,
    )
    return 0
