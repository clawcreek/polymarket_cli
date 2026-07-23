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
import os
from decimal import Decimal

import typer

from .. import bridge_api, config, context as _context, gasless, onchain
from ..output import emit

app = typer.Typer(no_args_is_help=True, help="Fund Polymarket from another chain (cross-chain deposit).")

# Where a gasless deposit lands: USDC.e on Polygon is Polymarket's tradable collateral.
_POLYGON_CHAIN_ID = 137
_POLYGON_USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


def _relay_key() -> str | None:
    # The skill injects the Relay key; accept the common names so setup can't miss.
    for name in ("POLYMARKET_RELAY_API_KEY", "RELAY_API_KEY", "RELAYER_API_KEY"):
        v = os.environ.get(name)
        if v:
            return v
    return None


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
        min_usd = bridge_api.min_deposit_usd(chain, assets)
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


# --- Relayer (activation) -----------------------------------------------------
# The relayer host is NOT region-blocked; default to calling it directly.
_RELAYER_DEFAULT = "https://relayer-v2.polymarket.com"


def _relayer_url() -> str:
    return (os.environ.get("POLYMARKET_RELAYER_URL") or _RELAYER_DEFAULT).rstrip("/")


def _relayer_request(path: str, *, method: str = "GET", body: dict | None = None,
                     api_key: str | None = None, signer: str | None = None) -> dict:
    import json as _json
    import urllib.request
    url = f"{_relayer_url()}{path}"
    data = _json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": "poly-cli/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        # These two headers ARE the auth for /submit — no signature field in the payload.
        headers["RELAYER_API_KEY"] = api_key
        headers["RELAYER_API_KEY_ADDRESS"] = signer or ""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json.load(resp)


def _wallet_create_payload(signer: str, factory: str) -> dict:
    """The official WALLET-CREATE submit body. Deliberately signature-free: the
    relayer authenticates the CALLER (the two RELAYER_API_KEY headers), deploys the
    deposit wallet for `from`, and pays the gas itself."""
    return {"type": "WALLET-CREATE", "from": signer, "to": factory}


@app.command()
def deploy(
    ctx: typer.Context,
    wait: int = typer.Option(90, help="Seconds to wait for on-chain confirmation"),
) -> None:
    """Deploy the Polymarket deposit wallet — the one-time account activation.

    A brand-new wallet's deposit wallet exists only as a derived address until this
    runs; until then the CLOB rejects the account with `invalid authorization`.
    Needs POLYMARKET_RELAYER_API_KEY (minted at polymarket.com → Settings → API
    Keys, registered to this signer). Idempotent — exits cleanly if already
    deployed. Follow with `poly approve` to grant the trading allowances.
    """
    import time as _time

    pk = _pk(ctx)
    signer = onchain.signer_address(pk)

    # /deployed keys by the DEPOSIT WALLET address (verified: EOA → false even when
    # deployed). Resolution needs a Polymarket profile — which exists iff the wallet
    # has signed in once, the same prerequisite minting the relayer key has.
    wallet = config.resolve_deposit_wallet(pk)
    if not wallet:
        emit(ctx.obj.output, {"ok": False, "reason": "no_profile",
                              "message": ("this wallet has never signed in to polymarket.com — sign in "
                                          "once (wallet connection) to create the account, then retry")})
        raise typer.Exit(1)

    dep = _relayer_request(f"/deployed?address={wallet}&type=WALLET")
    if dep.get("deployed"):
        emit(ctx.obj.output, {"ok": True, "already_deployed": True, "signer": signer, "wallet": wallet,
                              "next": "run `poly approve` if allowances are not set yet"})
        return

    api_key = os.environ.get("POLYMARKET_RELAYER_API_KEY")
    if not api_key:
        emit(ctx.obj.output, {"ok": False, "reason": "no_relayer_key",
                              "message": ("deploying needs POLYMARKET_RELAYER_API_KEY — create a Relayer "
                                          "API key at polymarket.com → Settings → API Keys (36-char UUID, "
                                          "registered to this wallet) and set it")})
        raise typer.Exit(1)

    # Factory address comes from the SDK environment (single source of truth).
    factory = config.resolve_environment().wallet_derivation.deposit_wallet_factory
    try:
        resp = _relayer_request("/submit", method="POST",
                                body=_wallet_create_payload(signer, str(factory)),
                                api_key=api_key, signer=signer)
    except Exception as exc:  # noqa: BLE001 — surface the relayer's rejection verbatim
        emit(ctx.obj.output, {"ok": False, "reason": "submit_failed", "message": str(exc)[:300]})
        raise typer.Exit(1)

    deadline = _time.time() + max(wait, 10)
    deployed = False
    while _time.time() < deadline:
        _time.sleep(8)
        try:
            if _relayer_request(f"/deployed?address={wallet}&type=WALLET").get("deployed"):
                deployed = True
                break
        except Exception:  # noqa: BLE001 — transient poll failures don't fail the deploy
            continue
    emit(ctx.obj.output, {
        "ok": True, "submitted": True, "deployed": deployed, "signer": signer, "wallet": wallet,
        "transaction_id": resp.get("transactionID"), "transaction_hash": resp.get("transactionHash"),
        "next": ("run `poly approve` to grant trading allowances" if deployed else
                 "still confirming — re-check with: poly deposit deploy (idempotent)"),
    })


@app.command()
def addresses(ctx: typer.Context, wallet: str = typer.Option(None, help="Polymarket deposit wallet; defaults to the resolved one")) -> None:
    """Per-user bridge deposit addresses (evm / svm / tron / btc)."""
    pk = _pk(ctx)
    # Same no-guess invariant as send(): a bridge address keyed to the signer EOA mints
    # pUSD to the EOA — money the account never sees. Authoritative wallet or fail.
    w = wallet or config.resolve_deposit_wallet(pk)
    if not w:
        emit(ctx.obj.output, {"ok": False, "reason": "no_deposit_wallet",
                              "message": ("could not resolve the Polymarket account wallet — refusing to "
                                          "mint a deposit address keyed to a guessed wallet. Retry, or "
                                          "pass --wallet with the known account address.")})
        raise typer.Exit(1)
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
                        f"it — use the gasless path instead: `poly deposit gasless --chain {chain} "
                        f"--token {token.upper()} --amount {amount}` (a sponsor pays the gas), or fund "
                        f"a little {cfg['native']} here."),
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

    # pUSD is already the exchange collateral (post the 2026-04-28 upgrade): the account
    # balance IS the api_wallet's pUSD balanceOf. It never goes through the bridge — the
    # bridge's job is wrapping OTHER assets into pUSD — so a pUSD "deposit" is a plain
    # ERC-20 transfer straight to the resolved api_wallet, with no bridge minimum.
    is_pusd = chain == "polygon" and token.upper() == "PUSD"

    if not is_pusd:
        min_usd = bridge_api.min_deposit_usd(chain)
        if min_usd is not None and amount < min_usd:
            emit(ctx.obj.output, {"ok": False, "reason": "below_minimum",
                                  "message": f"{chain} minimum deposit is ${min_usd}; {amount} would sit pending"})
            raise typer.Exit(1)

    # NEVER fall back to the signer EOA here. The bridge credits whatever address we
    # name as the Polymarket wallet — hand it the EOA (a transient profile-lookup
    # failure used to do exactly that) and it MINTS pUSD to the EOA instead of the
    # trading account: funds "arrive" but every balance read shows 0. Same invariant
    # as build_secure_client: authoritative address or fail loudly.
    w = wallet or config.resolve_deposit_wallet(pk)
    if not w:
        emit(ctx.obj.output, {"ok": False, "reason": "no_deposit_wallet",
                              "message": ("could not resolve the Polymarket account wallet (profile "
                                          "lookup failed or the signer never signed in at "
                                          "polymarket.com). Refusing to bridge to a guessed address — "
                                          "retry, or pass --wallet with the known account address.")})
        raise typer.Exit(1)
    if is_pusd:
        to_addr = w
    else:
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
                          "next": ("check: poly clob balance --asset-type collateral" if is_pusd
                                   else f"poll: poly deposit status {to_addr}")})


@app.command(name="gasless")
def gasless_cmd(
    ctx: typer.Context,
    chain: str = typer.Option(..., help="Source chain the funds sit on (e.g. bsc)"),
    token: str = typer.Option("USDC", help="Token to move (USDC, USDT, …)"),
    amount: float = typer.Option(..., help="Amount in human units"),
    wallet: str = typer.Option(None, help="Polymarket deposit wallet (recipient); defaults to the resolved one"),
    yes: bool = typer.Option(False, "--yes", help="Skip the preview and submit"),
) -> None:
    """Move a token to Polymarket with no native gas — a sponsor pays (EIP-7702 + Relay).

    For the exact case `scan` flags `sendable:false`: a stablecoin on a chain
    where the signer holds zero native token. Delegates the EOA to Calibur via an
    offline 7702 authorization, batches approve+deposit, and submits through
    Relay with fee subsidy. Relay swaps cross-chain and lands USDC.e on Polygon in
    the deposit wallet. No native token, no permit, any ERC-20.
    """
    pk = _pk(ctx)
    chain = chain.lower()
    if chain not in onchain.CHAINS:
        raise typer.BadParameter(f"unknown chain '{chain}'; one of {sorted(onchain.CHAINS)}")
    cfg = onchain.CHAINS[chain]
    if token.upper() not in cfg["tokens"]:
        raise typer.BadParameter(f"{token} not known on {chain}; have {list(cfg['tokens'])}")

    api_key = _relay_key()
    if not api_key:
        emit(ctx.obj.output, {"ok": False, "reason": "no_relay_key",
                              "message": "gasless needs a Relay API key: set RELAY_API_KEY (or POLYMARKET_RELAY_API_KEY)"})
        raise typer.Exit(1)

    signer = onchain.signer_address(pk)
    token_addr, dec = cfg["tokens"][token.upper()]
    units = _units(amount, dec)
    have = onchain.token_balance(chain, token_addr, signer)
    if have < units:
        emit(ctx.obj.output, {"ok": False, "reason": "insufficient_balance",
                              "message": f"have {Decimal(have)/(Decimal(10)**dec)} {token}, need {amount}"})
        raise typer.Exit(1)

    recipient = wallet or config.resolve_deposit_wallet(pk) or signer

    try:
        q = gasless.quote(
            user=signer, origin_chain_id=cfg["chain_id"], dest_chain_id=_POLYGON_CHAIN_ID,
            origin_currency=token_addr, dest_currency=_POLYGON_USDC_E,
            amount_base_units=units, recipient=recipient, api_key=api_key)
    except gasless.GaslessError as exc:
        emit(ctx.obj.output, {"ok": False, "reason": "quote_failed", "message": str(exc)})
        raise typer.Exit(1)

    out = q.get("details", {}).get("currencyOut", {})
    receive = out.get("amountFormatted")
    exe = gasless.build_execution(private_key=pk, chain=chain, quote_resp=q)

    plan = {"chain": chain, "token": token.upper(), "amount": amount, "recipient": recipient,
            "receive": f"{receive} USDC.e on Polygon", "delegated": exe["delegated"],
            "sponsor_pays_gas": True, "request_id": exe["request_id"]}
    if not yes:
        emit(ctx.obj.output, {"ok": True, "dry_run": True, "plan": plan,
                              "note": "re-run with --yes to submit (sponsor pays the gas)"})
        return

    try:
        res = gasless.submit(exe["execute_body"], api_key)
    except gasless.GaslessError as exc:
        emit(ctx.obj.output, {"ok": False, "reason": "execute_failed", "message": str(exc), "plan": plan})
        raise typer.Exit(1)
    req = res.get("requestId") or exe["request_id"]
    emit(ctx.obj.output, {"ok": True, "submitted": True, "request_id": req, "plan": plan,
                          "next": f"poll: poly deposit gasless-status {req}"})


@app.command(name="gasless-status")
def gasless_status(ctx: typer.Context, request_id: str = typer.Argument(..., help="Relay requestId from `gasless`")) -> None:
    """Relay intent status for a gasless deposit (pending → success | failure | refund)."""
    api_key = _relay_key()
    if not api_key:
        raise typer.BadParameter("gasless-status needs a Relay API key (RELAY_API_KEY)")
    emit(ctx.obj.output, gasless.status(request_id, api_key))


@app.command()
def status(ctx: typer.Context, deposit_address: str = typer.Argument(..., help="The evm/svm/… deposit address funds were sent to")) -> None:
    """Bridge status for a deposit address (DEPOSIT_DETECTED → … → COMPLETED)."""
    emit(ctx.obj.output, bridge_api.status(deposit_address))
