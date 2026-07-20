# poly/groups/clob_trade.py
"""CLOB trading and account read commands.

Note: update-balance was intentionally removed — it was a byte-for-byte duplicate
of balance. The SDK has no distinct "refresh balance" endpoint.
"""

from decimal import Decimal

import typer
from .. import context as _context
from ..output import emit
from ..pagination import collect
from ..orders import normalize_side, build_signed_limit_order, describe_response, decimal_str
from .. import trade

# USDC collateral and outcome-token shares are both quoted in 6-decimal base units.
BASE_UNIT_DECIMALS = 6

# api_wallet is resolved from an authoritative source only — the pinned wallet_address
# or Polymarket's own profiles lookup — and build_secure_client refuses the SDK's derived
# guess outright. So this note no longer tells the caller to hand-verify the address; that
# advice contradicted the resolution model and sent people chasing a wallet mismatch that
# can no longer happen. What is still true: a 0 balance is most likely genuinely unfunded,
# and a sub-minimum deposit sits pending instead of landing.
BALANCE_WALLET_NOTE = (
    "balance is for api_wallet, your Polymarket account (resolved from your pinned "
    "wallet_address or Polymarket's profile lookup — never a derived guess). A 0 balance "
    "usually means it is unfunded; check the Deposit screen for a pending transfer below "
    "the minimum before sending more."
)

# Readable table columns; `-o json` still returns every field.
ORDER_COLUMNS = ["id", "side", "price", "original_size", "size_matched", "outcome", "status"]
TRADE_COLUMNS = ["matched_at", "side", "outcome", "price", "size", "status"]

app = typer.Typer(no_args_is_help=True, help="CLOB trading and account reads.")


def _fmt(ctx: typer.Context) -> str:
    return ctx.obj.output


@app.command("create-order")
def create_order(
    ctx: typer.Context,
    token: str = typer.Option(None, "--token", "--token-id"),
    slug: str = typer.Option(None),
    url: str = typer.Option(None),
    outcome: str = typer.Option("yes"),
    side: str = typer.Option(..., "--side"),
    price: str = typer.Option(..., "--price"),
    size: str = typer.Option(None),
    usd: str = typer.Option(None),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Place a limit order."""
    pub = _context.public(ctx)
    target, plan = trade.build_plan(
        side=normalize_side(side), market_order=False, token_id=token,
        slug=slug, url=url, outcome=outcome, usd=usd, size=size, price=price, pub=pub,
    )
    raise typer.Exit(trade.run(
        ctx, pub=pub, secure_factory=lambda: _context.secure(ctx),
        target=target, plan=plan, dry_run=dry_run, yes=yes,
    ))


@app.command("market-order")
def market_order(
    ctx: typer.Context,
    token: str = typer.Option(None, "--token", "--token-id"),
    slug: str = typer.Option(None),
    url: str = typer.Option(None),
    outcome: str = typer.Option("yes"),
    side: str = typer.Option(..., "--side"),
    usd: str = typer.Option(None),
    size: str = typer.Option(None),
    max_spend: str = typer.Option(None, "--max-spend"),
    order_type: str = typer.Option(None, "--order-type"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Place a market order (FAK/FOK)."""
    pub = _context.public(ctx)
    target, plan = trade.build_plan(
        side=normalize_side(side), market_order=True, token_id=token,
        slug=slug, url=url, outcome=outcome, usd=usd, size=size,
        max_spend=max_spend, order_type=order_type, pub=pub,
    )
    raise typer.Exit(trade.run(
        ctx, pub=pub, secure_factory=lambda: _context.secure(ctx),
        target=target, plan=plan, dry_run=dry_run, yes=yes,
    ))


@app.command("post-orders")
def post_orders(
    ctx: typer.Context,
    tokens: str = typer.Option(..., "--tokens", help="Comma-separated token IDs."),
    side: str = typer.Option(..., "--side"),
    prices: str = typer.Option(..., "--prices", help="Comma-separated prices."),
    sizes: str = typer.Option(..., "--sizes", help="Comma-separated sizes."),
) -> None:
    """Build and post multiple limit orders in one call."""
    client = _context.secure(ctx)
    token_list = tokens.split(",")
    price_list = prices.split(",")
    size_list = sizes.split(",")
    s = normalize_side(side)
    signed_orders = [
        build_signed_limit_order(client, token_id=t.strip(), price=p.strip(), size=sz.strip(), side=s)
        for t, p, sz in zip(token_list, price_list, size_list)
    ]
    results = client.post_orders(signed_orders)
    emit(_fmt(ctx), [describe_response(r) for r in results])


@app.command("cancel")
def cancel(
    ctx: typer.Context,
    order_id: str = typer.Argument(...),
) -> None:
    """Cancel a single order by ID."""
    emit(_fmt(ctx), _context.secure(ctx).cancel_order(order_id=order_id))


@app.command("cancel-orders")
def cancel_orders(
    ctx: typer.Context,
    ids: str = typer.Argument(..., help="Comma-separated order IDs."),
) -> None:
    """Cancel multiple orders by ID."""
    emit(_fmt(ctx), _context.secure(ctx).cancel_orders(order_ids=ids.split(",")))


@app.command("cancel-market")
def cancel_market(
    ctx: typer.Context,
    market: str = typer.Option(..., "--market"),
) -> None:
    """Cancel all orders for a specific market."""
    emit(_fmt(ctx), _context.secure(ctx).cancel_market_orders(market=market))


@app.command("cancel-all")
def cancel_all(ctx: typer.Context, yes: bool = typer.Option(False, "--yes")) -> None:
    """Cancel ALL open orders (requires typed-YES confirmation)."""
    if not yes and not trade._confirm('This cancels ALL open orders. Type "YES" to confirm: '):
        emit(_fmt(ctx), {"aborted": True})
        raise typer.Exit(1)
    emit(_fmt(ctx), _context.secure(ctx).cancel_all())


@app.command("orders")
def orders(ctx: typer.Context, market: str = typer.Option(None)) -> None:
    """List your open orders."""
    client = _context.secure(ctx)
    paginator = client.list_open_orders(market=market) if market else client.list_open_orders()
    emit(_fmt(ctx), collect(paginator), columns=ORDER_COLUMNS)


@app.command("order")
def order(ctx: typer.Context, order_id: str = typer.Argument(...)) -> None:
    """Get details of a single order."""
    emit(_fmt(ctx), _context.secure(ctx).get_order(order_id=order_id))


@app.command("trades")
def trades(ctx: typer.Context) -> None:
    """List your account trades."""
    emit(_fmt(ctx), collect(_context.secure(ctx).list_account_trades()), columns=TRADE_COLUMNS)


@app.command("balance")
def balance(
    ctx: typer.Context,
    asset_type: str = typer.Option(..., "--asset-type", help="collateral or conditional"),
    token: str = typer.Option(None, "--token"),
) -> None:
    """Show your balance for an asset type, in human units (USDC / shares)."""
    result = _context.secure(ctx).get_balance_allowance(
        asset_type=asset_type.upper(), token_id=token,
    )
    raw = result["balance"] if isinstance(result, dict) else getattr(result, "balance", None)
    human = decimal_str(Decimal(str(raw)) / Decimal(10**BASE_UNIT_DECIMALS)) if raw is not None else None
    emit(_fmt(ctx), {
        "asset_type": asset_type.upper(),
        "balance": human,
        "raw": str(raw) if raw is not None else None,
        "note": BALANCE_WALLET_NOTE,
    })
