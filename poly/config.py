"""Config-file wallet model and client construction.

Key resolution order: --private-key flag > POLYMARKET_PRIVATE_KEY env >
~/.config/polymarket/config.json. The project .env is intentionally NOT read.

Note: --signature-type was intentionally removed. The SDK derives the deposit
wallet (type-3 / POLY_1271) deterministically from the private key; no other
signature type is supported via SecureClient.create().
"""

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
CONFIG_PATH = _CONFIG_HOME / "polymarket" / "config.json"


@dataclass(frozen=True)
class Settings:
    private_key: str = field(repr=False)
    wallet_address: str | None = None


def load_config(path: Path | None = None) -> dict:
    """Load config JSON from *path* (default: module-level CONFIG_PATH).

    Using None as the default lets callers and tests patch the module-level
    CONFIG_PATH and have the change take effect without passing path= explicitly.
    """
    p = path if path is not None else CONFIG_PATH
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file {p} is not valid JSON: {exc}")


def save_config(data: dict, path: Path | None = None) -> None:
    p = Path(path) if path is not None else Path(CONFIG_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    p.chmod(0o600)


def resolve_private_key(flag: str | None = None, env: str | None = None, config: str | None = None) -> str | None:
    return flag or env or config or None


def _normalize_key(key: str) -> str:
    key = key.strip()
    return key if key.startswith("0x") else "0x" + key


def load_settings(*, private_key: str | None = None, path: Path | None = None) -> Settings:
    """Load and validate settings.

    *path* defaults to the module-level CONFIG_PATH so that tests can patch it
    via ``monkeypatch.setattr(config, "CONFIG_PATH", ...)``.
    """
    cfg = load_config(path)
    key = resolve_private_key(
        flag=private_key,
        env=(os.environ.get("POLYMARKET_PRIVATE_KEY") or "").strip() or None,
        config=cfg.get("private_key"),
    )
    if not key:
        raise SystemExit(
            "No private key configured. Run `poly setup` or `poly wallet import 0x...`, "
            "or pass --private-key / set POLYMARKET_PRIVATE_KEY."
        )
    # Ignore any stale "signature_type" key — the SDK supports only the
    # deposit-wallet derivation and has no signature_type parameter.
    return Settings(private_key=_normalize_key(key), wallet_address=cfg.get("wallet_address"))


# Base-URL overrides. If a POLYMARKET_*_URL env var is set, that host is pointed
# at a custom endpoint (e.g. a regional 1:1 reverse proxy used to reach Polymarket
# from an allowed region) while EVERYTHING ELSE in the environment — chain id and
# all contract addresses — stays production. This is safe because orders are
# EIP-712 signed over the production contracts; only the transport URL changes.
# Unset => the official Polymarket endpoints (default behavior, unchanged).
_URL_ENV = {
    "clob_url": "POLYMARKET_CLOB_URL",
    "gamma_url": "POLYMARKET_GAMMA_URL",
    "data_url": "POLYMARKET_DATA_URL",
    "relayer_url": "POLYMARKET_RELAYER_URL",
    "rfq_url": "POLYMARKET_RFQ_URL",
    "rpc_url": "POLYMARKET_RPC_URL",
}


def resolve_environment():
    """Return the SDK Environment, with any per-host URL overrides from the
    POLYMARKET_*_URL env vars applied on top of PRODUCTION. No env vars set
    (the common case) returns PRODUCTION unchanged."""
    from polymarket.environments import PRODUCTION

    overrides = {field: os.environ[env] for field, env in _URL_ENV.items() if os.environ.get(env)}
    return dataclasses.replace(PRODUCTION, **overrides) if overrides else PRODUCTION


def resolve_relayer_api_key(private_key: str):
    """Build the SDK RelayerApiKey from env, or None.

    Polymarket's deposit-wallet (gasless) flow — required to SUBMIT live orders and to
    run gasless trading approvals — needs a Relayer/Builder API key. That key is a UUID
    minted at polymarket.com (Settings -> API Keys) and registered to your signer EOA;
    it is NOT derivable from the private key, so it must be supplied explicitly via
    POLYMARKET_RELAYER_API_KEY. POLYMARKET_RELAYER_ADDRESS overrides the on-key address
    (defaults to the signer's EOA, which is what the key is registered to).

    Unset (the default) returns None => the EOA flow: public reads and LOCAL signing
    work, but live order submission is rejected by CLOB ("maker address not allowed,
    use the deposit wallet flow")."""
    key = os.environ.get("POLYMARKET_RELAYER_API_KEY")
    if not key:
        return None
    from polymarket import RelayerApiKey

    address = os.environ.get("POLYMARKET_RELAYER_ADDRESS")
    if not address:
        from eth_account import Account

        address = Account.from_key(private_key).address
    return RelayerApiKey(key=key, address=address)


def build_public_client():
    from polymarket import PublicClient
    return PublicClient(resolve_environment())


def resolve_deposit_wallet(private_key: str):
    """Ask Polymarket which deposit wallet this signer actually owns, or None.

    One key derives SEVERAL addresses and the SDK's local derivation is not always the
    account Polymarket assigned/funded — trading or depositing against the wrong one reads
    as "balance 0 / order rejected". Polymarket's public `profiles` lookup is the
    authoritative source (it returns the server-side proxy/deposit wallet), so we ask it
    instead of guessing.

    Returns None when there is no profile (a fresh signer that never logged in to
    Polymarket) or the lookup fails — the caller then lets the SDK derive as before.
    Network call; best-effort by design, never fatal.
    """
    from eth_account import Account

    try:
        eoa = Account.from_key(private_key).address
        profile = build_public_client().get_public_profile(eoa)
    except Exception:  # noqa: BLE001 — a profile lookup must never break a command
        return None
    wallet = getattr(profile, "wallet", None) if profile is not None else None
    return str(wallet) if wallet else None


def build_secure_client(settings: Settings):
    from polymarket import SecureClient

    # Wallet resolution — NEVER fall back to the SDK's local derivation. One key derives
    # several addresses and the derived pick is not always the account Polymarket assigned
    # (upstream polymarket-cli#14); acting on the wrong one reads as "balance 0 / rejected"
    # and can strand a deposit. Only authoritative sources are accepted:
    #   1. wallet_address pinned in config.json (copied from polymarket.com/settings), or
    #   2. Polymarket's own profiles lookup — the source their docs call authoritative.
    # Neither available -> fail loudly and say what's missing.
    wallet = settings.wallet_address or resolve_deposit_wallet(settings.private_key)
    if not wallet:
        raise SystemExit(
            "No deposit wallet address: this signer has never signed in to polymarket.com, "
            "so Polymarket has no account (profile) for it yet — and the SDK's derived guess "
            "is not trusted (it is often the wrong account). Fix: sign in at polymarket.com "
            "once WITH THIS WALLET (that creates the account; the address then resolves "
            "automatically on the next run). Until then, market reads work but account "
            "reads/trading cannot. Advanced fallback: pin wallet_address in config.json."
        )
    return SecureClient.create(
        private_key=settings.private_key,
        wallet=wallet,
        environment=resolve_environment(),
        api_key=resolve_relayer_api_key(settings.private_key),
    )
