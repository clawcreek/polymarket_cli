# poly/groups/wallet.py
"""Local wallet/key management backed by config.json."""

import typer
from eth_account import Account

from .. import context as _context
from ..config import CONFIG_PATH, load_config, save_config
from ..context import CliContext
from ..output import emit

app = typer.Typer(no_args_is_help=True, help="Manage the signer key (config.json).")

# The active wallet is the address Polymarket's website labels "Address — for API
# use only. Do not send funds." It IS your account/maker (it holds funds and
# trades), but you must not transfer to it directly — deposit via the website.
API_WALLET_NOTE = (
    "api_wallet is a DERIVED address and may NOT be your real funded account: one key "
    "derives several wallets and the SDK's default pick can be wrong. Do NOT send funds "
    "to it. Deposit ONLY via the Polymarket website, and before trading verify this "
    "equals the deposit address in polymarket.com settings; if it differs, set "
    "wallet_address in config.json."
)


def _fmt(ctx: typer.Context) -> str:
    return ctx.obj.output if isinstance(ctx.obj, CliContext) else "table"


def _store_key(key: str) -> str:
    key = key if key.startswith("0x") else "0x" + key
    save_config({**load_config(path=CONFIG_PATH), "private_key": key}, path=CONFIG_PATH)
    return Account.from_key(key).address


def _api_wallet(ctx: typer.Context) -> str:
    """The SDK-derived API wallet (Polymarket's "API use only" address) that
    holds funds and trades (network call)."""
    try:
        return str(_context.secure(ctx).wallet)
    except Exception as exc:  # never let a network/auth hiccup break `show`
        return f"(unavailable: {type(exc).__name__})"


@app.command()
def create(ctx: typer.Context, force: bool = typer.Option(False, "--force")) -> None:
    """Generate a new random wallet and save it."""
    if load_config(path=CONFIG_PATH).get("private_key") and not force:
        raise SystemExit("A key already exists. Use --force to overwrite.")
    eoa = _store_key(Account.create().key.hex())
    emit(_fmt(ctx), {"signer_eoa": eoa, "config": str(CONFIG_PATH),
                     "note": "run `poly wallet show` to see your api_wallet"})


@app.command("import")
def import_key(ctx: typer.Context, private_key: str = typer.Argument(...)) -> None:
    """Import an existing private key."""
    eoa = _store_key(private_key)
    emit(_fmt(ctx), {"signer_eoa": eoa, "config": str(CONFIG_PATH),
                     "note": "run `poly wallet show` to see your api_wallet"})


@app.command()
def show(ctx: typer.Context) -> None:
    """Show your signer EOA and api_wallet (never prints the key).

    api_wallet is the address Polymarket's website labels "for API use only — do
    not send funds"; it holds your funds and trades. Deposit via the website.
    """
    cfg = load_config(path=CONFIG_PATH)
    key = cfg.get("private_key")
    eoa = Account.from_key(key).address if key else None
    api_wallet = _api_wallet(ctx) if key else None
    fields = {"signer_eoa": eoa, "api_wallet": api_wallet}
    if api_wallet:
        fields = {**fields, "note": API_WALLET_NOTE}
    emit(_fmt(ctx), {**fields, "config": str(CONFIG_PATH)})


@app.command()
def address(ctx: typer.Context) -> None:
    """Print your api_wallet address (holds funds and trades; do NOT send funds to it)."""
    emit(_fmt(ctx), {"address": str(_context.secure(ctx).wallet), "note": API_WALLET_NOTE})


@app.command()
def reset(ctx: typer.Context, force: bool = typer.Option(False, "--force")) -> None:
    """Delete the saved config."""
    if not force:
        raise SystemExit("This deletes your saved key. Re-run with --force to confirm.")
    CONFIG_PATH.unlink(missing_ok=True)
    emit(_fmt(ctx), {"deleted": str(CONFIG_PATH)})
