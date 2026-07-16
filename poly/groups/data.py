# poly/groups/data.py
import typer

from .. import context as _context
from ..output import emit
from ..pagination import collect

app = typer.Typer(no_args_is_help=True, help="On-chain portfolio data.")

# Readable table columns; `-o json` still returns every field.
# `redeemable` marks a RESOLVED position whose winnings are still sitting unclaimed —
# surface it so `poly redeem` (and the agent) can act on it instead of the user having to
# notice on the website. It rides along in -o json regardless; this puts it in the table too.
POSITION_COLUMNS = ["title", "outcome", "size", "avg_price", "cur_price", "current_value",
                    "cash_pnl", "percent_pnl", "redeemable"]


def _resolve_user(ctx: typer.Context, address: str | None) -> str:
    """Default to your api_wallet — the SDK-derived account that holds your funds
    (the website's "API use only" address) — not the signer EOA (which is empty)."""
    if address:
        return address
    return str(_context.secure(ctx).wallet)


@app.command()
def positions(ctx: typer.Context, address: str = typer.Argument(None), limit: int = 20) -> None:
    """List positions for ADDRESS (default: your api_wallet)."""
    user = _resolve_user(ctx, address)
    emit(ctx.obj.output, collect(_context.public(ctx).list_positions(user=user, page_size=limit)),
         columns=POSITION_COLUMNS)


@app.command()
def value(ctx: typer.Context, address: str = typer.Argument(None)) -> None:
    """Portfolio value for ADDRESS (default: your api_wallet)."""
    user = _resolve_user(ctx, address)
    emit(ctx.obj.output, _context.public(ctx).get_portfolio_values(user=user))
