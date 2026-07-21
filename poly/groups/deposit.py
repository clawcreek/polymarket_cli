"""`poly deposit` — fund a Polymarket account from another chain.

Polymarket's Bridge API does the cross-chain and the swap; this group's job is
the near end: see where the signer holds money (`scan`), get the per-user
deposit addresses (`addresses`), sign the transfer onto the bridge (`send`), and
watch it land (`status`).

The gas question — the signer may hold a stablecoin on a chain where it has no
native token to pay with — is surfaced, not hidden. `scan` reports gas-native
balance and whether it covers a transfer for every funded chain, so the agent
can pick a chain it can actually send from rather than discovering the shortfall
mid-transfer.
"""
from decimal import Decimal

import typer

from .. import bridge_api, config, context as _context, onchain
from ..output import emit

app = typer.Typer(no_args_is_help=True, help="Fund Polymarket from another chain (cross-chain deposit).")


def _pk(ctx: typer.Context) -> str:
    key = config.resolve_private_key(config=None) or _context._ctx(ctx).private_key
    if not key:
        # load_settings applies the full flag > env > config resolution + normalisation.
        key = config.load_settings(private_key=_context._ctx(ctx).private_key).private_key
    if not key:
        raise typer.BadParameter("no signer private key configured")
    return config._normalize_key(key) if hasattr(config, "_normalize_key") else key


def _units(amount: float, decimals: int) -> int:
    # Decimal(str(...)) so 0.1 stays 0.1, never 0.1000000000001.
    return int((Decimal(str(amount)) * (Decimal(10) ** decimals)).to_integral_value())


@app.command()
def scan(ctx: typer.Context) -> None:
    """Show, per chain, what the signer holds and whether it can pay gas.

    A chain is `sendable` only when there is enough native token to cover a
    transfer. A stablecoin balance on a chain with zero native is real money the
    plain send path cannot move — that is the row the agent must notice.
    """
    pk = _pk(ctx)
    signer = onchain.signer_address(pk)
    assets = None
    try:
        assets = bridge_api.supported_assets()
    except Exception:  # noqa: BLE001 — minimums are advisory; scan still works without them
        assets = []

    rows = []
    for chain, cfg in onchain.CHAINS.items():
        try:
            native = onchain.native_balance(chain, signer)
            gas = onchain.estimate_gas(chain, is_token=True)
        except Exception as exc:  # noqa: BLE001 — one unreachable chain must not sink the scan
            rows.append({"chain": chain, "error": str(exc)[:60]})
            continue
        gas_ok = native >= gas.cost_wei
        min_usd = bridge_api.min_deposit_usd(chain.capitalize(), assets)
        for symbol, (addr, dec) in cfg["tokens"].items():
            try:
                bal = onchain.token_balance(chain, addr, signer)
            except Exception:  # noqa: BLE001
                continue
            if bal == 0:
                continue
            rows.append({
                "chain": chain,
                "token": symbol,
                "balance": str(Decimal(bal) / (Decimal(10) ** dec)),
                "gas_native": f"{native / 1e18:.6f} {cfg['native']}",
                "gas_needed": f"{gas.cost_native:.6f} {cfg['native']}",
                "sendable": gas_ok,
                "min_deposit_usd": min_usd,
            })
        native_h = native / 1e18
        if native_h > 0:
            rows.append({"chain": chain, "token": cfg["native"], "balance": f"{native_h:.6f}",
                         "gas_native": f"{native_h:.6f} {cfg['native']}", "sendable": gas_ok})
    emit(ctx.obj.output, {"signer": signer, "holdings": rows})


@app.command()
def addresses(ctx: typer.Context, wallet: str = typer.Option(None, help="Polymarket deposit wallet; defaults to the resolved one")) -> None:
    """Per-user bridge deposit addresses (evm / svm / tron / btc)."""
    pk = _pk(ctx)
    w = wallet or config.resolve_deposit_wallet(pk) or onchain.signer_address(pk)
    emit(ctx.obj.output, {"polymarket_wallet": w, "deposit_addresses": bridge_api.deposit_addresses(w)})


@app.command()
def send(
    ctx: typer.Context,
    chain: str = typer.Option(..., help="Source chain: ethereum, polygon, base, arbitrum, optimism, bsc"),
    token: str = typer.Option("USDC", help="Token to send (USDC, USDT, DAI) or the chain's native symbol"),
    amount: float = typer.Option(..., help="Amount in human units"),
    wallet: str = typer.Option(None, help="Polymarket deposit wallet; defaults to the resolved one"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation and broadcast"),
) -> None:
    """Sign and broadcast a transfer of the fund onto the bridge's deposit address.

    Refuses up front — before signing — when the chain has no native token to pay
    gas, when the balance is short, or when the amount is under the bridge's
    minimum. A rejected precheck is an honest 'can't from here', not a failed
    broadcast.
    """
    pk = _pk(ctx)
    chain = chain.lower()
    if chain not in onchain.CHAINS:
        raise typer.BadParameter(f"unknown chain '{chain}'; one of {sorted(onchain.CHAINS)}")
    cfg = onchain.CHAINS[chain]
    signer = onchain.signer_address(pk)

    is_native = token.upper() == cfg["native"]
    if not is_native and token.upper() not in cfg["tokens"]:
        raise typer.BadParameter(f"{token} not known on {chain}; have {list(cfg['tokens'])} + {cfg['native']}")

    # Gas precheck — the whole reason send can fail before it starts.
    gas = onchain.estimate_gas(chain, is_token=not is_native)
    native = onchain.native_balance(chain, signer)
    if native < gas.cost_wei:
        emit(ctx.obj.output, {
            "ok": False, "reason": "insufficient_gas",
            "message": (f"{chain} has {native/1e18:.6f} {cfg['native']} but a transfer needs "
                        f"~{gas.cost_native:.6f}. This chain holds a token with no native gas to move "
                        f"it — fund a little {cfg['native']} here, or bridge from a chain that has gas."),
        })
        raise typer.Exit(1)

    if is_native:
        token_addr, dec = None, 18
    else:
        token_addr, dec = cfg["tokens"][token.upper()]
    units = _units(amount, dec)
    if not is_native:
        have = onchain.token_balance(chain, token_addr, signer)
        if have < units:
            emit(ctx.obj.output, {"ok": False, "reason": "insufficient_balance",
                                  "message": f"have {Decimal(have)/(Decimal(10)**dec)} {token}, need {amount}"})
            raise typer.Exit(1)

    min_usd = bridge_api.min_deposit_usd(chain.capitalize())
    if min_usd is not None and amount < min_usd:
        emit(ctx.obj.output, {"ok": False, "reason": "below_minimum",
                              "message": f"{chain} minimum deposit is ${min_usd}; {amount} would sit pending"})
        raise typer.Exit(1)

    w = wallet or config.resolve_deposit_wallet(pk) or signer
    dep = bridge_api.deposit_addresses(w)
    to_addr = dep.get("evm")
    if not to_addr:
        emit(ctx.obj.output, {"ok": False, "reason": "no_evm_address", "message": f"bridge returned no evm address: {dep}"})
        raise typer.Exit(1)

    plan = {"chain": chain, "token": token.upper(), "amount": amount, "to": to_addr,
            "polymarket_wallet": w, "gas_native": f"{gas.cost_native:.6f} {cfg['native']}"}
    if not yes:
        emit(ctx.obj.output, {"ok": True, "dry_run": True, "plan": plan,
                              "note": "re-run with --yes to broadcast"})
        return

    tx_hash = onchain.send_transfer(chain, pk, to_addr, token_addr=token_addr, amount_base_units=units)
    emit(ctx.obj.output, {"ok": True, "submitted": True, "tx_hash": tx_hash, "plan": plan,
                          "next": f"poll: poly deposit status {to_addr}"})


@app.command()
def status(ctx: typer.Context, deposit_address: str = typer.Argument(..., help="The evm/svm/… deposit address funds were sent to")) -> None:
    """Bridge status for a deposit address (DEPOSIT_DETECTED → … → COMPLETED)."""
    emit(ctx.obj.output, bridge_api.status(deposit_address))
