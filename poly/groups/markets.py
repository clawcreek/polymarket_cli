"""Find markets and events (public, read-only)."""

import typer

from .. import context as _context
from ..output import emit
from ..pagination import collect

app = typer.Typer(no_args_is_help=True, help="Find markets by keyword, slug, or id.")

# Readable table columns; `-o json` still returns token ids + condition id.
MARKET_COLUMNS = ["question", "yes_price", "slug"]


def _market_row(market) -> dict:
    """Flatten a market to the fields you need to read or trade it."""
    outcomes = getattr(market, "outcomes", None)
    yes = getattr(outcomes, "yes", None) if outcomes else None
    no = getattr(outcomes, "no", None) if outcomes else None
    yes_price = getattr(yes, "price", None) if yes else None
    return {
        "question": getattr(market, "question", None),
        "slug": getattr(market, "slug", None),
        "condition_id": getattr(market, "condition_id", None),
        "yes_price": str(yes_price) if yes_price is not None else None,
        "yes_token_id": getattr(yes, "token_id", None) if yes else None,
        "no_token_id": getattr(no, "token_id", None) if no else None,
    }


@app.command()
def search(ctx: typer.Context, query: str = typer.Argument(...), limit: int = 10) -> None:
    """Find markets by keyword (searches events and their markets)."""
    rows = []
    for result in collect(_context.public(ctx).search(q=query, page_size=limit)):
        for event in getattr(result, "events", None) or []:
            for market in getattr(event, "markets", None) or []:
                rows.append(_market_row(market))
    emit(ctx.obj.output, rows, columns=MARKET_COLUMNS)


@app.command()
def get(ctx: typer.Context, ref: str = typer.Argument(..., help="Market id, slug, or URL.")) -> None:
    """Show a single market by id, slug, or URL."""
    client = _context.public(ctx)
    if ref.startswith("http"):
        market = client.get_market(url=ref)
    elif ref.isdigit():
        market = client.get_market(id=ref)
    else:
        market = client.get_market(slug=ref)
    emit(ctx.obj.output, _market_row(market))


@app.command("list")
def list_markets(ctx: typer.Context, limit: int = 20, closed: bool = typer.Option(False, "--closed/--active")) -> None:
    """List markets (open by default; --closed for resolved markets)."""
    page = _context.public(ctx).list_markets(closed=closed, page_size=limit)
    emit(ctx.obj.output, [_market_row(m) for m in collect(page)], columns=MARKET_COLUMNS)
