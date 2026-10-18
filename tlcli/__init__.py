"""``tlcli`` -- a command-line front end over ``tlcore`` + ``tltransport``.

Run it from a checkout with ``python -m tlcli`` (the published wheel contains
only ``tlcore``; see docs/architecture.md section 6).
"""
from .cli import main

__all__ = ["main"]
