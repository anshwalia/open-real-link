"""Start the PySide6 app.

    python -m tlapp                                  # connect manually
    python -m tlapp --replay fixtures/spp-smoketest.jsonl
    python -m tlapp --address AA-BB-CC-DD-EE-FF --connect

``--replay`` runs the whole app against a recorded capture, so it can be
developed and demonstrated with no device and no radio.
"""
from __future__ import annotations

import argparse
import sys

from .controller import ConnectionOptions

__all__ = ["build_parser", "main", "options_from_args"]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tlapp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", help="device address, e.g. AA-BB-CC-DD-EE-FF")
    ap.add_argument("--channel", type=int, help="RFCOMM channel (default: discover SPP)")
    ap.add_argument("--transport", default="auto", choices=["auto", "macos", "replay"])
    ap.add_argument("--replay", metavar="FILE", help="run against a recorded fixture")
    ap.add_argument("--reset", action="store_true",
                    help="live only: drop and re-establish the ACL link first")
    ap.add_argument("--loose", action="store_true",
                    help="with --replay, do not require writes to match the recording")
    ap.add_argument("--timeout", type=float, default=1.5, help="read timeout in seconds")
    ap.add_argument("--connect", action="store_true",
                    help="connect as soon as the window opens")
    return ap


def options_from_args(args: argparse.Namespace) -> ConnectionOptions:
    return ConnectionOptions(
        address=args.address,
        channel=args.channel,
        transport=args.transport,
        replay=args.replay,
        reset=args.reset,
        loose=args.loose,
        timeout=args.timeout,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from PySide6.QtCore import QCoreApplication  # imported late: keeps tlapp Qt-free
    from PySide6.QtWidgets import QApplication

    from .instance import InstanceLock
    from .window import MainWindow

    app = QApplication(sys.argv if argv is None else ["tlapp"])
    # Deterministic identity: the single-instance lock and the on-disk cache path
    # are derived from these, so they must not depend on how the app was started
    # (``python -m tlapp`` would otherwise present itself as "Python").
    QCoreApplication.setApplicationName("open-real-link")
    QCoreApplication.setOrganizationName("open-real-link")
    app.setApplicationDisplayName("OpenRealLink")
    lock = InstanceLock()
    if not lock.acquired:
        # One RFCOMM slot: a second instance would fight the first for the
        # channel and fail with a confusing refusal. Report and exit -- on
        # stderr, never a modal dialog, so a headless or scripted launch cannot
        # hang waiting for a click.
        print(f"tlapp: {lock.explain()}", file=sys.stderr)
        return 1
    window = MainWindow(options_from_args(args))
    window.show()
    if args.connect or args.replay:
        window.connect_device()
    _install_signal_shutdown(app)
    try:
        return app.exec()
    finally:
        lock.release()


def _install_signal_shutdown(app) -> None:
    """Make SIGINT/SIGTERM close the window cleanly, releasing the lock.

    A Qt event loop does not run Python between events, so a signal handler is
    only reached once the interpreter gets a turn. A no-op timer gives it one a
    few times a second; without it ``Ctrl-C`` and ``kill`` would tear the process
    down with the lock file still on disk.
    """
    import signal

    from PySide6.QtCore import QTimer

    def _quit(_signum, _frame):
        app.quit()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, _quit)
    ticker = QTimer()
    ticker.start(200)
    ticker.timeout.connect(lambda: None)  # let Python process pending signals
    app._signal_ticker = ticker  # keep it alive for the app's lifetime
