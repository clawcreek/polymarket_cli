# tests/test_redeem.py
from types import SimpleNamespace

from typer.testing import CliRunner

from poly import context
from poly.cli import app

runner = CliRunner()


class FakeHandle:
    def wait(self):
        return {"status": "confirmed"}


class FakeSecure:
    def __init__(self):
        self.calls = []

    def redeem_positions(self, *, condition_id=None, market_id=None, position_id=None):
        self.calls.append(condition_id)
        return FakeHandle()


def test_redeem_by_condition_id(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    result = runner.invoke(app, ["-o", "json", "redeem", "--condition-id", "0xCOND"])
    assert result.exit_code == 0
    assert fake.calls == ["0xCOND"]
    assert "redeemed" in result.output


def test_redeem_by_slug_resolves_condition_id(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    monkeypatch.setattr(context, "public", lambda ctx: object())
    monkeypatch.setattr(
        "poly.groups.redeem.resolve_target",
        lambda pub, slug=None: SimpleNamespace(condition_id="0xFROMSLUG"),
    )
    result = runner.invoke(app, ["-o", "json", "redeem", "--slug", "some-market"])
    assert result.exit_code == 0
    assert fake.calls == ["0xFROMSLUG"]


def test_redeem_needs_exactly_one_target():
    assert runner.invoke(app, ["redeem"]).exit_code != 0
    assert runner.invoke(app, ["redeem", "--condition-id", "0x1", "--slug", "s"]).exit_code != 0


def test_redeem_surfaces_the_real_error(monkeypatch):
    class Boom:
        def redeem_positions(self, **kwargs):
            raise RuntimeError("market is not resolved yet")

    monkeypatch.setattr(context, "secure", lambda ctx: Boom())
    result = runner.invoke(app, ["-o", "json", "redeem", "--condition-id", "0x1"])
    assert result.exit_code == 1
    assert "market is not resolved yet" in result.output


def test_redeem_slug_without_condition_id_fails(monkeypatch):
    monkeypatch.setattr(context, "public", lambda ctx: object())
    monkeypatch.setattr(
        "poly.groups.redeem.resolve_target",
        lambda pub, slug=None: SimpleNamespace(condition_id=None),
    )
    result = runner.invoke(app, ["redeem", "--slug", "no-condition"])
    assert result.exit_code != 0
