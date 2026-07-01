import urllib.error

from typer.testing import CliRunner

from poly.cli import app
from poly.groups import report as report_mod

runner = CliRunner()

_PAYLOAD = {
    "generated_at": "2026-07-01T09:00:00+00:00",
    "age_seconds": 120,
    "stale": False,
    "report": {"universe": [{"slug": "a"}], "opportunities": [], "account": None},
}


def test_report_fetches_and_emits(monkeypatch):
    monkeypatch.setattr(report_mod, "_fetch", lambda url, timeout=10: _PAYLOAD)
    r = runner.invoke(app, ["-o", "json", "report", "--url", "http://gw/v1/market-report"])
    assert r.exit_code == 0
    assert "generated_at" in r.output and "universe" in r.output


def test_report_uses_env_url(monkeypatch):
    seen = {}

    def _fake(url, timeout=10):
        seen["url"] = url
        return _PAYLOAD

    monkeypatch.setattr(report_mod, "_fetch", _fake)
    monkeypatch.setenv("POLYMARKET_REPORT_URL", "http://env/v1/market-report")
    r = runner.invoke(app, ["-o", "json", "report"])
    assert r.exit_code == 0 and seen["url"] == "http://env/v1/market-report"


def test_report_missing_url_errors(monkeypatch):
    monkeypatch.delenv("POLYMARKET_REPORT_URL", raising=False)
    r = runner.invoke(app, ["report"])
    assert r.exit_code != 0


def test_report_unreachable_exits_1(monkeypatch):
    def _boom(url, timeout=10):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(report_mod, "_fetch", _boom)
    r = runner.invoke(app, ["-o", "json", "report", "--url", "http://gw"])
    assert r.exit_code == 1 and "unavailable" in r.output
