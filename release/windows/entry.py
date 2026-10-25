"""Frozen entry point for the OpenRealLink desktop app on Windows.

PyInstaller analyses this file. It is deliberately tiny: the real startup lives
in :func:`tlapp.app.main`, so the build and ``python -m tlapp`` share one path.
"""
from __future__ import annotations

import sys

from tlapp.app import main

if __name__ == "__main__":
    sys.exit(main())
