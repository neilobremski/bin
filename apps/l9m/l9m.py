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


def _in_fenced_code_block(text: str) -> bool:
    return text.count("```") % 2 == 1


def _paragraph_flush_end(text: str) -> int:
    best = 0
    start = 0
    while True:
        idx = text.find("\n\n", start)
        if idx == -1:
            break
        best = idx + 2
        start = idx + 2
    return best


def safe_markdown_flush_end(text: str) -> int:
    """Bytes safe to render without waiting for more input."""
    if not text:
        return 0

    if _in_fenced_code_block(text):
        fence_start = text.rfind("```")
        before = text[:fence_start]
        if before.count("```") % 2 == 0:
            prose_flush = _paragraph_flush_end(before)
            if prose_flush:
                return prose_flush
        return 0

    best = _paragraph_flush_end(text)
    if best < len(text):
        tail = text[best:]
        if tail.rstrip().endswith("```") and tail.count("```") >= 2:
            return len(text)
    return best


def resolve_glow_style(theme: str = "auto") -> str:
    if theme != "auto":
        return theme

    cli_theme = os.environ.get("CLITHEME", "").strip().split(":")[0]
    if cli_theme in ("dark", "light"):
        return cli_theme

    lum = _query_terminal_background_luminance()
    if lum is not None:
        return "light" if lum >= 32768 else "dark"

    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        bg = colorfgbg.rsplit(";", 1)[-1]
        if bg.isdigit():
            n = int(bg)
            if n in (0, 1, 2, 3, 4, 5, 6, 8):
                return "dark"
            if n in (7, 9, 10, 11, 12, 13, 14, 15):
                return "light"

    return "dark"


def _parse_osc11_rgb(data: bytes) -> tuple[int, int, int] | None:
    text = data.decode("latin-1", errors="replace")
    match = re.search(r"11;(?:rgb:)?([^\\a\x1b]+)", text)
    if not match:
        return None
    spec = match.group(1).strip()
    if spec.startswith("#"):
        hex_rgb = spec[1:]
        if len(hex_rgb) < 6:
            return None
        parts = [hex_rgb[i:i + 2] for i in (0, 2, 4)]
    else:
        parts = spec.split("/")
        if len(parts) != 3:
            return None

    try:
        rgb = tuple(_osc_color_component(part) for part in parts)
    except ValueError:
        return None
    return rgb


def _osc_color_component(part: str) -> int:
    part = part.strip().lower()
    if not part:
        return 0
    if len(part) == 1:
        part = part * 4
    elif len(part) == 2:
        part = part * 2
    elif len(part) == 3:
        part = f"{part[0]}{part[0]}{part[1]}{part[1]}{part[2]}{part[2]}"
    return int(part[:4], 16)


def _query_terminal_background_luminance() -> int | None:
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return None
    try:
        import select
        import termios
        import tty as tty_mod
    except ImportError:
        return None

    fd = sys.stdin.fileno()
    old = None
    reply = b""
    try:
        old = termios.tcgetattr(fd)
        tty_mod.setraw(fd, termios.TCSANOW)
        os.write(sys.stdout.fileno(), b"\033]11;?\007")
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            reply += chunk
            if b"\a" in reply or b"\x1b\\" in reply:
                break
    except OSError:
        return None
    finally:
        if old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except OSError:
                pass

    rgb = _parse_osc11_rgb(reply)
    if rgb is None:
        return None
    r, g, b = rgb
    return (299 * r + 587 * g + 114 * b) // 1000


def _glow_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CLICOLOR_FORCE", "1")
    env.setdefault("COLORTERM", "truecolor")
    return env


def _glow_width() -> int | None:
    if not sys.stdout.isatty():
        return None
    try:
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        return None
    if cols <= 0:
        return None
    return min(cols, 120)


class GlowStream:
    """Render markdown incrementally via glow at safe chunk boundaries."""

    def __init__(self, stream, glow_bin: str, theme: str = "auto"):
        self._stream = stream
        self._glow = glow_bin
        self._style = resolve_glow_style(theme)
        self._width = _glow_width()
        self._buffer = ""
        self._rendered = 0
        self._closed = False

    def write(self, text: str) -> int:
        if not text or self._closed:
            return 0
        self._buffer += text
        self._flush_safe()
        return len(text)

    def flush(self) -> None:
        self._flush_safe(final=False)

    def finalize(self) -> None:
        self._flush_safe(final=True)

    def _flush_safe(self, final: bool = False) -> None:
        while True:
            pending = self._buffer[self._rendered:]
            end = len(pending) if final else safe_markdown_flush_end(pending)
            if not end:
                break
            chunk = pending[:end]
            self._rendered += end
            rendered = self._run_glow(chunk)
            if rendered and self._stream:
                try:
                    self._stream.write(rendered)
                    if not rendered.endswith("\n"):
                        self._stream.write("\n")
                    self._stream.flush()
                except (BrokenPipeError, OSError):
                    self._stream = None
                    self._closed = True
                    break

    def _run_glow(self, markdown: str) -> str:
        cmd = [self._glow, "--style", self._style]
        if self._width:
            cmd.extend(["-w", str(self._width)])
        cmd.append("-")
        try:
            proc = subprocess.run(
                cmd,
                input=markdown,
                capture_output=True,
                text=True,
                check=False,
                env=_glow_subprocess_env(),
            )
        except OSError:
            return markdown
        if proc.returncode != 0 or not proc.stdout:
            return markdown
        return proc.stdout

    def close(self) -> None:
        if self._closed:
            return
        self.finalize()
        self._closed = True


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
    if stream:
        finalize = getattr(stream, "finalize", None)
        if callable(finalize):
            try:
                finalize()
            except (BrokenPipeError, OSError):
                pass
        else:
            flush = getattr(stream, "flush", None)
            if callable(flush):
                try:
                    flush()
                except (BrokenPipeError, OSError):
                    pass
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

def _output_stream(glow_theme: str | None):
    if not glow_theme:
        return sys.stdout
    glow_bin = shutil.which("glow")
    if not glow_bin:
        raise L9mError("glow not found on PATH")
    return GlowStream(sys.stdout, glow_bin, glow_theme)


def _chat_loop(
    model: str,
    response_type: str,
    instruction: str,
    context_limit: int = 0,
    glow_theme: str | None = None,
) -> int:
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

        stream = None
        try:
            stream = _output_stream(glow_theme if not response_type else None)
            output = generate(model, full_prompt, stream=stream)
        except KeyboardInterrupt:
            print()
            continue
        except L9mError as e:
            print(f"error: {e}", file=sys.stderr)
            continue
        finally:
            if isinstance(stream, GlowStream):
                stream.close()

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
  --glow <theme>          Render markdown via glow (theme: auto, dark, light, dracula, …)
  --clear                 Clear rolling context and exit
  --model                 Print resolved model and exit
  --context-size          Print rolling context limit (chars) and exit

rolling context: prompt+response pairs are kept in ~/.cache/l9m/context.txt
  as a sliding window. Size is auto-derived from the model's context window
  (25% * ~3 chars/token). Override with L9M_CONTEXT_LIMIT env var.

env vars:
  L9M_CONTEXT_DIR     Directory for context storage (default: ~/.cache/l9m)
  L9M_CONTEXT_LIMIT   Override auto-derived context size (chars)
  L9M_GLOW=<theme>    Enable glow rendering with the given theme (auto detects light/dark)

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
    glow_theme = os.environ.get("L9M_GLOW", "").strip()

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
        elif arg == "--glow":
            i += 1
            glow_theme = argv[i] if i < len(argv) else "auto"
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
        return _chat_loop(model, response_type, instruction, context_limit, glow_theme or None)

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

    stream = None
    try:
        stream = _output_stream(glow_theme if not response_type else None)
        output = generate(model, full_prompt, stream=stream)
    except L9mError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if isinstance(stream, GlowStream):
            stream.close()

    if use_rolling_context and prompt and (prompt_flag or not stdin_content):
        append_context(prompt, output.strip(), context_limit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
