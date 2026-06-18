#!/usr/bin/env python3
"""b3t - Bear Tracks newsletter automation CLI."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import main

if __name__ == "__main__":
    sys.exit(main())
