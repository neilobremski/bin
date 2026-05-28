"""l9m — Local LLM interface. Auto-detects ollama model, streams responses.

Model resolution (precedence):
  1. MODEL env var
  2. Cached default (~/.cache/l9m.env)
  3. Best installed qwen model (ollama list, version-sorted)
  4. Fallback: pull qwen3:0.6b
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CACHE_FILE = Path.home() / ".cache" / "l9m.env"
DEFAULT_MODEL = "qwen3:0.6b"


# ---------- model resolution ----------

def _ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _start_ollama() -> bool:
    ollama = shutil.which("ollama")
    if not ollama:
        return False
    subprocess.Popen(
        [ollama, "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(10):
        time.sleep(1)
        if _ollama_running():
            return True
    return False


def _installed_qwen_models() -> list[str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        return [m for m in models if "qwen" in m.lower()]
    except Exception:
        return []


def _version_key(name: str) -> tuple:
    numbers = re.findall(r"[\d.]+", name)
    parts = []
    for n in numbers:
        for seg in n.split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                parts.append(0)
    size = re.search(r"(\d+)b", name.lower())
    parts.append(int(size.group(1)) if size else 0)
    return tuple(parts)


def _read_cache() -> str | None:
    if not CACHE_FILE.exists():
        return None
    try:
        for line in CACHE_FILE.read_text().splitlines():
            if line.startswith("MODEL="):
                return line[6:].strip()
    except OSError:
        pass
    return None


def _write_cache(model: str) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(f"MODEL={model}\n")


def resolve_model() -> str:
    env = os.environ.get("MODEL", "").strip()
    if env:
        return env

    cached = _read_cache()
    if cached:
        return cached

    if not _ollama_running():
        if not _start_ollama():
            print("error: ollama not installed or won't start", file=sys.stderr)
            sys.exit(1)

    qwen_models = _installed_qwen_models()
    if qwen_models:
        best = sorted(qwen_models, key=_version_key)[-1]
        _write_cache(best)
        return best

    print(f"pulling {DEFAULT_MODEL}...", file=sys.stderr)
    subprocess.run(
        [shutil.which("ollama") or "ollama", "pull", DEFAULT_MODEL],
        stdout=sys.stderr, stderr=sys.stderr,
    )
    _write_cache(DEFAULT_MODEL)
    return DEFAULT_MODEL


# ---------- prompt assembly ----------

def assemble_prompt(
    prompt: str,
    response_type: str,
    instruction: str,
    context: str,
) -> str:
    if response_type and instruction:
        prefix, suffix = "", ""
        if response_type == "bash":
            prefix = "Answer ONLY with the bash command, no explanation. "
            suffix = ". Answer ONLY with the bash command, no explanation"
        elif response_type == "bool":
            prefix = "Answer ONLY YES or NO. "
            suffix = "? Answer ONLY YES or NO"
        elif response_type == "list":
            prefix = "Answer ONLY with a list of items, one per line. "
            suffix = ". Answer ONLY with a list of items, one per line"
        else:
            print(f"invalid type: {response_type}", file=sys.stderr)
            sys.exit(2)

        return (
            f"INSTRUCTION: {prefix}{instruction}:\n\n"
            f"{prompt}\n{context}\n"
            f"{instruction}: <Prompt>{prompt}</Prompt>{suffix}"
        )

    if context:
        return f"{prompt}\n{context}\n{prompt}"

    return prompt


# ---------- ollama streaming ----------

def generate(model: str, prompt: str, silent: bool = False) -> str:
    if not _ollama_running():
        if not _start_ollama():
            print("error: ollama not running", file=sys.stderr)
            sys.exit(1)

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"num_predict": -1},
        "think": False,
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    output_parts = []
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                text = chunk.get("response", "")
                if text:
                    output_parts.append(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as e:
        if not silent:
            print(f"error: ollama returned {e.code}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if not silent:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    output = "".join(output_parts)
    if output and not output.endswith("\n"):
        sys.stdout.write("\n")
    return output


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print("""l9m — local LLM interface (auto-detects ollama model)

usage: l9m [options] [prompt]
       echo "text" | l9m [-p "question"]

options:
  -p, --prompt <text>     Prompt text
  -t, --type <type>       Response type: bash, bool, list
  -i, --instruction <text> Instruction framing
  -c, --context <file>    Context from file
  -e, --echo              Echo assembled prompt before generation
  -s, --silent            Suppress stderr
  --model                 Print resolved model and exit

model resolution: MODEL env > ~/.cache/l9m.env > best installed qwen > pull qwen3:0.6b""")
        return 0

    prompt = ""
    prompt_flag = False
    response_type = ""
    instruction = ""
    context_file = ""
    echo_prompt = False
    silent = False
    show_model = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-p", "--prompt"):
            i += 1
            prompt = argv[i] if i < len(argv) else ""
            prompt_flag = True
        elif arg in ("-t", "--type", "-type"):
            i += 1
            response_type = argv[i] if i < len(argv) else ""
        elif arg in ("-i", "--instruction", "-instruction"):
            i += 1
            instruction = argv[i] if i < len(argv) else ""
        elif arg in ("-c", "--context", "-context"):
            i += 1
            context_file = argv[i] if i < len(argv) else ""
        elif arg in ("-e", "--echo", "-echo"):
            echo_prompt = True
        elif arg in ("-s", "--silent"):
            silent = True
        elif arg == "--model":
            show_model = True
        elif not arg.startswith("-") and not arg.startswith(".") and not arg.startswith("/"):
            if not prompt:
                prompt = arg
        i += 1

    if show_model:
        print(resolve_model())
        return 0

    model = resolve_model()

    # stdin handling
    stdin_content = ""
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read()

    if prompt_flag and stdin_content:
        context_payload = stdin_content
    elif not prompt:
        prompt = stdin_content
        context_payload = ""
    else:
        context_payload = ""

    if context_file:
        path = Path(context_file)
        if not path.is_file():
            print(f"context file not found: {context_file}", file=sys.stderr)
            return 2
        file_content = path.read_text(encoding="utf-8")
        if context_payload:
            context_payload = f"{context_payload}\n{file_content}"
        else:
            context_payload = file_content

    context = f"<Memories>\n{context_payload}\n</Memories>" if context_payload else ""

    full_prompt = assemble_prompt(prompt, response_type, instruction, context)

    if echo_prompt:
        print(full_prompt)

    if not full_prompt.strip():
        return 0

    generate(model, full_prompt, silent=silent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
