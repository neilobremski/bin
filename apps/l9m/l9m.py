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

CONTEXT_DIR = Path(os.environ.get("L9M_CONTEXT_DIR") or str(Path.home() / ".cache" / "l9m"))
CONTEXT_FILE = CONTEXT_DIR / "context.txt"
CONTEXT_LIMIT_OVERRIDE = os.environ.get("L9M_CONTEXT_LIMIT", "").strip()
CHARS_PER_TOKEN = 3
CONTEXT_FRACTION = 0.25


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


def _read_cache() -> dict[str, str]:
    result = {}
    if not CACHE_FILE.exists():
        return result
    try:
        for line in CACHE_FILE.read_text().splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    except OSError:
        pass
    return result


def _write_cache(model: str, num_ctx: int | None = None) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"MODEL={model}"]
    if num_ctx:
        lines.append(f"NUM_CTX={num_ctx}")
    CACHE_FILE.write_text("\n".join(lines) + "\n")


def _model_num_ctx(model: str) -> int | None:
    """Query ollama for the model's context window size (in tokens)."""
    body = json.dumps({"name": model}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/show",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        params = data.get("model_info", {})
        for key, val in params.items():
            if "context_length" in key:
                return int(val)
    except Exception:
        pass
    return None


def resolve_context_limit(model: str) -> int:
    """Derive rolling context char limit from model's context window."""
    if CONTEXT_LIMIT_OVERRIDE:
        try:
            return int(CONTEXT_LIMIT_OVERRIDE)
        except ValueError:
            pass

    cache = _read_cache()
    if cache.get("MODEL") == model and "NUM_CTX" in cache:
        try:
            num_ctx = int(cache["NUM_CTX"])
        except ValueError:
            num_ctx = _model_num_ctx(model) or 0
    else:
        num_ctx = _model_num_ctx(model) or 0

    if num_ctx:
        return int(num_ctx * CONTEXT_FRACTION * CHARS_PER_TOKEN)
    return 10000


def resolve_model() -> str:
    env = os.environ.get("MODEL", "").strip()
    if env:
        return env

    cache = _read_cache()
    if cache.get("MODEL"):
        return cache["MODEL"]

    if not _ollama_running():
        if not _start_ollama():
            raise L9mError("ollama not installed or won't start")

    qwen_models = _installed_qwen_models()
    if qwen_models:
        best = sorted(qwen_models, key=_version_key)[-1]
        num_ctx = _model_num_ctx(best)
        _write_cache(best, num_ctx)
        return best

    print(f"pulling {DEFAULT_MODEL}...", file=sys.stderr)
    subprocess.run(
        [shutil.which("ollama") or "ollama", "pull", DEFAULT_MODEL],
        stdout=sys.stderr, stderr=sys.stderr,
    )
    num_ctx = _model_num_ctx(DEFAULT_MODEL)
    _write_cache(DEFAULT_MODEL, num_ctx)
    return DEFAULT_MODEL


# ---------- prompt assembly ----------

def assemble_prompt(
    prompt: str,
    response_type: str,
    instruction: str,
    context: str,
) -> str:
    if response_type:
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

        framing = instruction if instruction else "Answer"
        return (
            f"INSTRUCTION: {prefix}{framing}:\n\n"
            f"{prompt}\n{context}\n"
            f"{framing}: <Prompt>{prompt}</Prompt>{suffix}"
        )

    if instruction:
        return (
            f"INSTRUCTION: {instruction}:\n\n"
            f"{prompt}\n{context}\n"
            f"{instruction}: <Prompt>{prompt}</Prompt>"
        )

    if context:
        return f"{prompt}\n{context}\n{prompt}"

    return prompt


# ---------- ollama streaming ----------

class L9mError(RuntimeError):
    pass


_STREAM_DEFAULT = object()


def generate(model: str, prompt: str, stream: object | None = _STREAM_DEFAULT) -> str:
    """Generate text from ollama. Streams to `stream` if provided, returns full text.
    Raises L9mError on failure (never calls sys.exit)."""
    if stream is _STREAM_DEFAULT:
        stream = sys.stdout
    if not _ollama_running():
        if not _start_ollama():
            raise L9mError("ollama not installed or won't start")

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
                    if stream:
                        try:
                            stream.write(text)
                            stream.flush()
                        except (BrokenPipeError, OSError):
                            stream = None
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as e:
        raise L9mError(f"ollama returned {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise L9mError(str(e)) from e

    output = "".join(output_parts)
    if stream and output and not output.endswith("\n"):
        try:
            stream.write("\n")
        except (BrokenPipeError, OSError):
            pass
    return output


# ---------- rolling context ----------

def read_context() -> str:
    try:
        return CONTEXT_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def append_context(prompt: str, response: str, limit: int = 0) -> None:
    ctx_limit = limit or 10000
    entry = f">>> {prompt}\n{response}\n"
    existing = read_context()
    combined = existing + entry
    if len(combined) > ctx_limit:
        combined = combined[-ctx_limit:]
        nl = combined.find("\n")
        if nl != -1 and nl < len(combined) - 1:
            combined = combined[nl + 1:]
        else:
            combined = entry[-ctx_limit:]
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    CONTEXT_FILE.write_text(combined, encoding="utf-8")


# ---------- chat REPL ----------

def _chat_loop(model: str, response_type: str, instruction: str, context_limit: int = 0) -> int:
    """Interactive REPL. Rolling context accumulates across turns."""
    try:
        import readline  # noqa: F401 — enables line editing in input()
    except ImportError:
        pass

    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        prompt = line.strip()
        if not prompt:
            continue
        if prompt in ("quit", "exit"):
            return 0

        context = read_context()
        context_wrapped = f"<Memories>\n{context}\n</Memories>" if context else ""
        full_prompt = assemble_prompt(prompt, response_type, instruction, context_wrapped)

        try:
            output = generate(model, full_prompt)
        except KeyboardInterrupt:
            print()
            continue
        except L9mError as e:
            print(f"error: {e}", file=sys.stderr)
            continue

        append_context(prompt, output.strip(), context_limit)


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print("""l9m — local LLM interface (auto-detects ollama model)

usage: l9m [options] [prompt]
       echo "text" | l9m [-p "question"]
       l9m --chat [-t bash] [-i "instruction"]

options:
  -p, --prompt <text>     Prompt text
  -t, --type <type>       Response type: bash, bool, list
  -i, --instruction <text> Instruction framing
  -c, --context <file>    Context from file (overrides rolling context)
  -e, --echo              Echo assembled prompt before generation
  -s, --silent            Suppress stderr
  --chat                  Interactive REPL (CTRL+D or "quit" to exit)
  --clear                 Clear rolling context and exit
  --model                 Print resolved model and exit
  --context-size          Print rolling context limit (chars) and exit

rolling context: prompt+response pairs are kept in ~/.cache/l9m/context.txt
  as a sliding window. Size is auto-derived from the model's context window
  (25% * ~3 chars/token). Override with L9M_CONTEXT_LIMIT env var.

env vars:
  L9M_CONTEXT_DIR     Directory for context storage (default: ~/.cache/l9m)
  L9M_CONTEXT_LIMIT   Override auto-derived context size (chars)

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
    show_context_size = False
    clear_context = False
    chat_mode = False

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
        elif arg == "--context-size":
            show_context_size = True
        elif arg == "--clear":
            clear_context = True
        elif arg == "--chat":
            chat_mode = True
        elif not arg.startswith("-") and not arg.startswith(".") and not arg.startswith("/"):
            if not prompt:
                prompt = arg
        i += 1

    if clear_context:
        try:
            CONTEXT_FILE.unlink()
        except OSError:
            pass
        return 0

    try:
        if show_model:
            print(resolve_model())
            return 0
        model = resolve_model()
    except L9mError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    context_limit = resolve_context_limit(model)

    if show_context_size:
        print(context_limit)
        return 0

    if chat_mode:
        return _chat_loop(model, response_type, instruction, context_limit)

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

    use_rolling_context = not context_file

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
    elif use_rolling_context:
        rolling = read_context()
        if rolling:
            if context_payload:
                context_payload = f"{rolling}\n{context_payload}"
            else:
                context_payload = rolling

    context = f"<Memories>\n{context_payload}\n</Memories>" if context_payload else ""

    full_prompt = assemble_prompt(prompt, response_type, instruction, context)

    if echo_prompt:
        print(full_prompt)

    if not full_prompt.strip():
        return 0

    try:
        output = generate(model, full_prompt)
    except L9mError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if use_rolling_context and prompt and (prompt_flag or not stdin_content):
        append_context(prompt, output.strip(), context_limit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
