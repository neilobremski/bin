"""Load .env file from CWD into os.environ."""
import os


def load_env(path=None):
    """Parse .env from CWD into os.environ. Existing vars take precedence."""
    env_path = path or os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key not in os.environ:
                os.environ[key] = val


def get(key):
    """Get env var or raise with helpful message."""
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"ERROR: {key} not set. Add it to .env or export it.")
    return val
