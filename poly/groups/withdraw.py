# poly/groups/withdraw.py
"""Withdraw collateral OUT of the Polymarket account wallet.

The account (deposit) wallet is a contract wallet controlled by the signer via
Polymarket's relayer, so a withdrawal is a relayer-dispatched (gasless) ERC-20
transfer from that wallet to any address — the same primitive the website's
Withdraw screen uses. Requires the Relayer API key (like `setup`): without it the
SDK refuses the gasless dispatch.

Deliberately narrow: collateral tokens only (default PUSD), amount in human
units, preview unless --yes. Cross-chain bridging OUT is not offered here —
withdraw to a Polygon address you control, bridge from there if needed.
"""
from decimal import Decimal

import typer

from .. import context as _context
from ..output import emit

# Polygon collateral tokens the account wallet realistically holds.
# (address, decimals) — PUSD is the exchange collateral post 2026-04-28;
# USDC.e kept for pre-upgrade residue.
_TOKENS = {
    "PUSD": ("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", 6),
    "USDC.E": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
}


def withdraw_cmd(
    ctx: typer.Context,
    to: str = typer.Option(..., help="Recipient address on Polygon (yours — double-check it)"),
    amount: float = typer.Option(..., help="Amount in human units (e.g. 2.5)"),
    token: str = typer.Option("PUSD", help=f"One of {sorted(_TOKENS)}"),
    yes: bool = typer.Option(False, "--yes", help="Skip the preview and broadcast"),
) -> None:
    """Withdraw collateral from the Polymarket account wallet to `--to` (relayer-gasless)."""
    tok = token.upper()
    if tok not in _TOKENS:
        raise typer.BadParameter(f"token must be one of {sorted(_TOKENS)}")
    token_addr, dec = _TOKENS[tok]
    if amount <= 0:
        raise typer.BadParameter("amount must be > 0")
    units = int((Decimal(str(amount)) * (Decimal(10) ** dec)).to_integral_value())

    c = _context.secure(ctx)
    plan = {"from_account_wallet": str(c.wallet), "to": to, "token": tok,
            "token_address": token_addr, "amount": amount, "base_units": units}
    if not yes:
        emit(ctx.obj.output, {"ok": True, "dry_run": True, "plan": plan,
                              "note": "re-run with --yes to broadcast"})
        return

    handle = c.transfer_erc20(token_address=token_addr, recipient_address=to, amount=units)
    outcome = handle.wait()
    tx_hash = getattr(outcome, "transaction_hash", None)
    emit(ctx.obj.output, {"ok": True, "submitted": True, "tx_hash": tx_hash, "plan": plan,
                          "next": "check: poly clob balance"})
