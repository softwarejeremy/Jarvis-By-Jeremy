"""Permite ejecutar `python -m jarvis`."""

import sys

from .main import run

if __name__ == "__main__":
    sys.exit(run())
