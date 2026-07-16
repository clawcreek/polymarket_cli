# poly/groups/redeem.py
"""Redeem winnings from a resolved market.

Without this, a user who WON has to go back to polymarket.com to claim — the CLI could
place the bet but not collect it. `poly data positions` marks claimable rows with
`redeemable: true`; this turns them into USDC.

Gasless when the relayer flow is configured (POLYMARKET_RELAYER_API_KEY) — the same flow
live orders use. Without it the SDK falls back to broadcasting from the signer EOA, which
needs MATIC for gas.
"""
import typer

from .. import context as _context
from ..market import resolve_target
from ..output import emit, print_error


def redeem_cmd(
    ctx: typer.Context,
    condition_id: str = typer.Option(None, "--condition-id", help="Market condition id to redeem."),
    slug: str = typer.Option(None, "--slug", help="Market slug; resolved to its condition id."),
) -> None:
    """Redeem your winnings from a RESOLVED market (gasless with a relayer key)."""
    fmt = getattr(ctx.obj, "output", "table")
    if bool(condition_id) == bool(slug):
        raise SystemExit("Specify exactly one of --condition-id or --slug.")

    if slug:
        target = resolve_target(_context.public(ctx), slug=slug)
        condition_id = target.condition_id
        if not condition_id:
            raise SystemExit(f"Could not resolve a condition id for slug={slug!r}.")

    client = _context.secure(ctx)
    try:
        result = client.redeem_positions(condition_id=condition_id).wait()
    except Exception as exc:  # noqa: BLE001 — surface the real reason, never guess
        print_error(fmt, f"redeem failed: {exc}")
        raise typer.Exit(1)

    emit(fmt, {"redeemed": True, "condition_id": condition_id, "result": str(result)})
