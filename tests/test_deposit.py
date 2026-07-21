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
