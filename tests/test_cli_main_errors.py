# tests/test_cli_main_errors.py
"""main() is the CLI's single error exit: every failure must come out as a clean
message (or JSON error envelope), never a Python traceback — whatever typer/click
version the sandbox happens to resolve (typer>=0.27 vendors click as typer._click
and moved Abort to typer.exceptions)."""
import sys

import pytest

from poly import cli
from poly.groups import data as data_mod


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["poly", *argv])
    return cli.main()


def test_string_system_exit_becomes_json_error_envelope(monkeypatch, capsys):
    def _no_account(ctx, address):
        raise SystemExit("No deposit wallet address: sign in at polymarket.com once")

    monkeypatch.setattr(data_mod, "_resolve_user", _no_account)
    code = _run_main(monkeypatch, ["-o", "json", "data", "value"])

    out = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in out.out + out.err
    assert "No deposit wallet address" in out.out + out.err


def test_keyboard_interrupt_does_not_crash_main(monkeypatch, capsys):
    def _interrupted(ctx, address):
        raise KeyboardInterrupt

    monkeypatch.setattr(data_mod, "_resolve_user", _interrupted)
    _run_main(monkeypatch, ["data", "value"])

    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err


def test_usage_error_returns_click_exit_code(monkeypatch, capsys):
    code = _run_main(monkeypatch, ["definitely-not-a-command"])

    out = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in out.out + out.err


def test_successful_command_returns_zero(monkeypatch, capsys):
    code = _run_main(monkeypatch, ["--help"])
    assert code == 0
