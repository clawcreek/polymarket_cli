"""Deposit precheck logic — the branches that must reject before signing."""
from decimal import Decimal
import pytest
from poly import onchain, bridge_api


def test_units_conversion_is_exact():
    from poly.groups.deposit import _units
    assert _units(10, 6) == 10_000_000
    assert _units(0.1, 6) == 100_000          # not 99999
    assert _units(52, 18) == 52 * 10**18


def test_chains_carry_required_shape():
    for name, cfg in onchain.CHAINS.items():
        assert "chain_id" in cfg and "native" in cfg and cfg["rpcs"]
        for sym, (addr, dec) in cfg["tokens"].items():
            assert addr.startswith("0x") and isinstance(dec, int)


def test_erc20_transfer_calldata_shape():
    # transfer(to, amount) — selector + 32-byte to + 32-byte amount
    to = "0x" + "33" * 20
    data = onchain._ERC20_TRANSFER + onchain._pad(to) + hex(10_000_000)[2:].rjust(64, "0")
    assert data.startswith("0xa9059cbb")
    assert len(data) == 2 + 8 + 64 + 64


def test_min_deposit_reads_lowest(monkeypatch):
    assets = [
        {"chainName": "Polygon", "minCheckoutUsd": 2},
        {"chainName": "Polygon", "minCheckoutUsd": 5},
        {"chainName": "Ethereum", "minCheckoutUsd": 7},
    ]
    assert bridge_api.min_deposit_usd("polygon", assets) == 2.0
    assert bridge_api.min_deposit_usd("ethereum", assets) == 7.0
    assert bridge_api.min_deposit_usd("solana", assets) is None


def test_gas_estimate_cost():
    g = onchain.GasEstimate(price_wei=10**9, limit=21000, native_symbol="ETH")
    assert g.cost_wei == 21000 * 10**9
    assert abs(g.cost_native - 0.000021) < 1e-9


def test_min_deposit_maps_cli_chain_keys():
    # The bridge lists "BNB Smart Chain"; the CLI says "bsc". The mapping is what
    # stops a sub-minimum deposit from sailing through (the 1-USDC-stuck bug).
    assets = [
        {"chainName": "BNB Smart Chain", "minCheckoutUsd": 2},
        {"chainName": "Polygon", "minCheckoutUsd": 2},
    ]
    assert bridge_api.min_deposit_usd("bsc", assets) == 2.0
    assert bridge_api.min_deposit_usd("BNB Smart Chain", assets) == 2.0
    assert bridge_api.min_deposit_usd("polygon", assets) == 2.0


def test_wallet_create_payload_is_signature_free():
    from poly.groups.deposit import _wallet_create_payload
    p = _wallet_create_payload("0x" + "aa" * 20, "0x" + "bb" * 20)
    assert p == {"type": "WALLET-CREATE", "from": "0x" + "aa" * 20, "to": "0x" + "bb" * 20}
    # No signature/nonce/calldata fields — the relayer authenticates the caller instead.
    assert not ({"signature", "nonce", "data"} & set(p))


def test_relayer_url_default_and_override(monkeypatch):
    from poly.groups import deposit as d
    monkeypatch.delenv("POLYMARKET_RELAYER_URL", raising=False)
    assert d._relayer_url() == "https://relayer-v2.polymarket.com"
    monkeypatch.setenv("POLYMARKET_RELAYER_URL", "https://x.example/api/")
    assert d._relayer_url() == "https://x.example/api"


# ---- pUSD routing + the no-guess wallet invariant in send/addresses ----

from types import SimpleNamespace
import json as _json
from typer.testing import CliRunner
from poly.cli import app as _app
from poly import config as _config
import poly.groups.deposit as _dep

_runner = CliRunner()


def test_polygon_registry_knows_pusd_and_usdce():
    tokens = onchain.CHAINS["polygon"]["tokens"]
    assert tokens["PUSD"] == ("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", 6)
    assert tokens["USDC.E"][0] == "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


def _wire_send(monkeypatch, resolved_wallet="0xAPIWALLET"):
    monkeypatch.setattr(_dep, "_pk", lambda ctx: "0x" + "a" * 64)
    monkeypatch.setattr(onchain, "signer_address", lambda pk: "0xSIGNER")
    monkeypatch.setattr(onchain, "estimate_gas",
                        lambda chain, is_token: SimpleNamespace(cost_wei=1, cost_native=0.0001))
    monkeypatch.setattr(onchain, "native_balance", lambda chain, addr: 10**18)
    monkeypatch.setattr(onchain, "token_balance", lambda chain, token, addr: 10**24)
    monkeypatch.setattr(_config, "resolve_deposit_wallet", lambda pk: resolved_wallet)
    monkeypatch.setattr(bridge_api, "min_deposit_usd", lambda chain: 2)
    monkeypatch.setattr(bridge_api, "deposit_addresses", lambda w: {"evm": "0xBRIDGE"})


def test_send_pusd_goes_straight_to_api_wallet(monkeypatch):
    """pUSD IS the collateral: no bridge hop, no bridge minimum — direct to api_wallet."""
    _wire_send(monkeypatch)
    monkeypatch.setattr(bridge_api, "deposit_addresses",
                        lambda w: (_ for _ in ()).throw(AssertionError("bridge must not be called for pUSD")))
    monkeypatch.setattr(bridge_api, "min_deposit_usd",
                        lambda chain: (_ for _ in ()).throw(AssertionError("no bridge minimum for pUSD")))
    r = _runner.invoke(_app, ["-o", "json", "deposit", "send", "--chain", "polygon",
                              "--token", "pUSD", "--amount", "2.5"])
    assert r.exit_code == 0, r.output
    plan = _json.loads(r.output)["plan"]
    assert plan["to"] == "0xAPIWALLET"
    assert plan["polymarket_wallet"] == "0xAPIWALLET"


def test_send_usdc_still_routes_via_bridge(monkeypatch):
    _wire_send(monkeypatch)
    r = _runner.invoke(_app, ["-o", "json", "deposit", "send", "--chain", "polygon",
                              "--token", "USDC", "--amount", "2.5"])
    assert r.exit_code == 0, r.output
    assert _json.loads(r.output)["plan"]["to"] == "0xBRIDGE"


def test_send_refuses_to_guess_wallet(monkeypatch):
    """resolve fails -> hard stop. The old `or signer` fallback bridged funds to the
    EOA: the bridge minted pUSD to the signer and every balance read showed 0."""
    _wire_send(monkeypatch, resolved_wallet=None)
    r = _runner.invoke(_app, ["-o", "json", "deposit", "send", "--chain", "polygon",
                              "--token", "USDC", "--amount", "2.5"])
    assert r.exit_code == 1
    assert "no_deposit_wallet" in r.output
    assert "0xSIGNER" not in r.output


def test_addresses_refuses_to_guess_wallet(monkeypatch):
    monkeypatch.setattr(_dep, "_pk", lambda ctx: "0x" + "a" * 64)
    monkeypatch.setattr(_config, "resolve_deposit_wallet", lambda pk: None)
    r = _runner.invoke(_app, ["-o", "json", "deposit", "addresses"])
    assert r.exit_code == 1
    assert "no_deposit_wallet" in r.output
