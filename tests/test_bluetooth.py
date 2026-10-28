"""Tests for the Bluetooth radio probe and the "needs a radio" decision.

The private macOS preference API is faked, so this runs on every platform with
no radio and no display.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tlapp import ConnectionOptions  # noqa: E402
from tlapp import bluetooth as bt  # noqa: E402
from tlapp.app import _needs_live_bluetooth  # noqa: E402


def _fake_prefs(monkeypatch, state: int = 0, on_set=None):
    """Install fake macOS preference handles into the module's cache."""
    monkeypatch.setattr(bt, "_PREFS_LOADED", True)
    monkeypatch.setattr(bt, "_PREFS", (lambda: state, on_set or (lambda value: None)))


def test_state_is_always_one_of_the_three() -> None:
    assert bt.bluetooth_state() in (bt.ON, bt.OFF, bt.UNKNOWN)


def test_unknown_on_a_platform_without_a_probe(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(bt, "_PREFS_LOADED", False)
    monkeypatch.setattr(bt, "_PREFS", None)
    assert bt.bluetooth_state() == bt.UNKNOWN
    assert bt.set_bluetooth(True) is False
    assert bt.open_bluetooth_settings() is False


def test_macos_reads_off_and_on(monkeypatch) -> None:
    _fake_prefs(monkeypatch, state=0)
    assert bt.bluetooth_state() == bt.OFF
    _fake_prefs(monkeypatch, state=1)
    assert bt.bluetooth_state() == bt.ON


def test_macos_state_is_unknown_when_the_probe_raises(monkeypatch) -> None:
    def boom():
        raise RuntimeError("controller gone")

    monkeypatch.setattr(bt, "_PREFS_LOADED", True)
    monkeypatch.setattr(bt, "_PREFS", (boom, lambda value: None))
    assert bt.bluetooth_state() == bt.UNKNOWN


def test_set_bluetooth_fires_the_request(monkeypatch) -> None:
    calls: list[int] = []
    _fake_prefs(monkeypatch, on_set=calls.append)
    assert bt.set_bluetooth(True) is True
    assert calls == [1]


def test_set_bluetooth_without_the_api(monkeypatch) -> None:
    monkeypatch.setattr(bt, "_PREFS_LOADED", True)
    monkeypatch.setattr(bt, "_PREFS", None)
    assert bt.set_bluetooth(True) is False


def test_wait_for_bluetooth_returns_when_it_arrives(monkeypatch) -> None:
    states = iter([bt.OFF, bt.OFF, bt.ON])
    monkeypatch.setattr(bt, "bluetooth_state", lambda: next(states))
    monkeypatch.setattr(bt, "_POLL_SECONDS", 0)
    assert bt.wait_for_bluetooth(bt.ON, timeout=1.0) is True


def test_wait_for_bluetooth_times_out(monkeypatch) -> None:
    monkeypatch.setattr(bt, "bluetooth_state", lambda: bt.OFF)
    monkeypatch.setattr(bt, "_POLL_SECONDS", 0)
    assert bt.wait_for_bluetooth(bt.ON, timeout=0.0) is False


def test_settings_launcher_falls_back_to_the_second_command(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        if len(seen) == 1:
            raise OSError("unknown pane id")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    assert bt._run_first([["a"], ["b"]]) is True
    assert seen == [["a"], ["b"]]


def test_settings_launcher_reports_total_failure(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise OSError("no `open`")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    assert bt._run_first([["a"], ["b"]]) is False


@pytest.mark.parametrize("options, expected", [
    (ConnectionOptions(replay="spp-smoketest.jsonl"), False),
    (ConnectionOptions(transport="replay"), False),
    (ConnectionOptions(address="AA-BB-CC-DD-EE-FF"), True),
    (ConnectionOptions(address="AA-BB-CC-DD-EE-FF", transport="macos"), True),
])
def test_whether_a_run_needs_a_radio(options, expected) -> None:
    assert _needs_live_bluetooth(options) is expected
