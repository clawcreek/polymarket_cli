# tests/test_clob_trade.py
from decimal import Decimal
from types import SimpleNamespace
from typer.testing import CliRunner
from poly.cli import app
from poly import context

runner = CliRunner()


class FakePub:
    def list_markets(self, clob_token_ids=None):
        return SimpleNamespace(first_page=lambda: SimpleNamespace(items=[]))
    def get_price(self, token_id=None, side=None): return Decimal("0.5")


class FakeSecure:
    wallet = "0xWALLET"

    def __init__(self):
        self.posted = []
        self.cancelled = []
        self.cancel_all_called = False

    def create_limit_order(self, **k):
        return SimpleNamespace(
            maker=self.wallet, signer=self.wallet, token_id=k["token_id"],
            side=k["side"], maker_amount="1", taker_amount="2", order_type="GTC",
        )

    def post_order(self, s):
        self.posted.append(s)
        return SimpleNamespace(ok=True, order_id="o1", status="MATCHED")

    def list_open_orders(self, **k):
        return SimpleNamespace(
            first_page=lambda: SimpleNamespace(
                items=[{"id": "o1", "price": "0.5"}], has_next=False,
            )
        )

    def cancel_order(self, *, order_id):
        self.cancelled.append(order_id)
        return {"cancelled": order_id}

    def get_order(self, *, order_id):
        return {"id": order_id, "price": "0.5", "size": "10"}

    def list_account_trades(self):
        return SimpleNamespace(
            first_page=lambda: SimpleNamespace(
                items=[{"trade_id": "t1", "price": "0.5"}], has_next=False,
            )
        )

    def get_balance_allowance(self, *, asset_type, token_id=None):
        return {"asset_type": asset_type, "balance": "47514085"}  # raw 6-decimal base units

    def cancel_all(self):
        self.cancel_all_called = True
        return {"cancelled": 5}


def test_create_order_dry_run_does_not_post(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "public", lambda ctx: FakePub())
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    result = runner.invoke(app, ["clob", "create-order", "--token", "111", "--side", "buy",
                                 "--size", "5", "--price", "0.5", "--dry-run"])
    assert result.exit_code == 0
    assert fake.posted == []


def test_orders_read_json(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    result = runner.invoke(app, ["-o", "json", "clob", "orders"])
    assert result.exit_code == 0 and "o1" in result.output


def test_cancel_calls_cancel_order(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    result = runner.invoke(app, ["clob", "cancel", "order-abc"])
    assert result.exit_code == 0
    assert "order-abc" in fake.cancelled


def test_order_emits_order_details(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    result = runner.invoke(app, ["-o", "json", "clob", "order", "order-xyz"])
    assert result.exit_code == 0
    assert "order-xyz" in result.output


def test_trades_lists_account_trades(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    result = runner.invoke(app, ["-o", "json", "clob", "trades"])
    assert result.exit_code == 0
    assert "t1" in result.output


def test_balance_converts_raw_to_human_units(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    result = runner.invoke(app, ["-o", "json", "clob", "balance", "--asset-type", "collateral"])
    assert result.exit_code == 0
    assert "COLLATERAL" in result.output
    assert "47.514085" in result.output  # raw 47514085 -> human USDC


def test_cancel_all_with_yes_flag(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    result = runner.invoke(app, ["clob", "cancel-all", "--yes"])
    assert result.exit_code == 0
    assert fake.cancel_all_called


def test_cancel_all_aborts_without_yes(monkeypatch):
    """cancel-all without --yes and non-interactive stdin aborts cleanly."""
    fake = FakeSecure()
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    # Runner by default has no stdin, so EOFError fires → aborted
    result = runner.invoke(app, ["clob", "cancel-all"])
    assert result.exit_code != 0 or "aborted" in result.output
    assert not fake.cancel_all_called
