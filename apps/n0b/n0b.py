"""n0b — Neil's Bin kitchen-sink CLI."""
from __future__ import annotations

import sys

from cli import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
