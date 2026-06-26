"""Tests for the `poly markets` group (public client mocked)."""

from decimal import Decimal
from types import SimpleNamespace

from typer.testing import CliRunner

from poly.cli import app
from poly import context

runner = CliRunner()


def _market(question="Will USA win?", slug="usa-win", yes="111", no="222"):
    return SimpleNamespace(
        question=question,
        slug=slug,
        condition_id="0xc",
        outcomes=SimpleNamespace(
            yes=SimpleNamespace(token_id=yes, label="Yes", price=Decimal("0.52")),
            no=SimpleNamespace(token_id=no, label="No", price=Decimal("0.48")),
        ),
    )


def _page(items):
    return SimpleNamespace(first_page=lambda: SimpleNamespace(items=items, has_next=False))


class FakePub:
    def search(self, q=None, page_size=10):
        event = SimpleNamespace(markets=[_market(question=f"match for {q}")])
        return _page([SimpleNamespace(events=[event], tags=[], profiles=[])])

    def get_market(self, id=None, slug=None, url=None):
        return _market(slug=slug or "by-id")

    def list_markets(self, closed=False, page_size=20):
        return _page([_market(), _market(question="Other", slug="other")])


def _use_fake(monkeypatch):
    monkeypatch.setattr(context, "public", lambda ctx: FakePub())


def test_search_flattens_events_to_market_rows(monkeypatch):
    _use_fake(monkeypatch)
    result = runner.invoke(app, ["-o", "json", "markets", "search", "world cup"])
    assert result.exit_code == 0
    assert "match for world cup" in result.output
    assert "111" in result.output  # yes token id is surfaced for trading


def test_get_by_slug_returns_row(monkeypatch):
    _use_fake(monkeypatch)
    result = runner.invoke(app, ["-o", "json", "markets", "get", "usa-win"])
    assert result.exit_code == 0
    assert "usa-win" in result.output


def test_list_returns_rows(monkeypatch):
    _use_fake(monkeypatch)
    result = runner.invoke(app, ["-o", "json", "markets", "list", "--limit", "2"])
    assert result.exit_code == 0
    assert "Other" in result.output
