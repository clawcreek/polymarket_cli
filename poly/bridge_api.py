"""Client for Polymarket's official Bridge API (bridge.polymarket.com).

The bridge takes a deposit on almost any chain and lands USDC.e on Polygon in
the caller's Polymarket account — it does the cross-chain and the swap itself.
We only need to POST for the per-user deposit addresses, send funds to the right
one, and poll for completion. No API key; an optional builder code attributes
traffic and buys priority on stuck deposits.

Docs: https://docs.polymarket.com/trading/bridge/deposit
"""
from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.environ.get("POLYMARKET_BRIDGE_URL", "https://bridge.polymarket.com")


def _request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": "poly-cli/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    code = os.environ.get("POLYMARKET_BUILDER_CODE")
    if code:
        headers["X-Builder-Code"] = code
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def deposit_addresses(wallet_address: str) -> dict:
    """Per-user deposit addresses keyed by VM type: evm / svm / tron / btc.

    Every EVM chain (Ethereum, Base, Arbitrum, BSC, Polygon, …) shares the one
    `evm` address; funds are routed by which chain they arrive on.
    """
    resp = _request("/deposit", "POST", {"address": wallet_address})
    return resp.get("address", resp)


def supported_assets() -> list[dict]:
    resp = _request("/supported-assets")
    return resp.get("supportedAssets", resp if isinstance(resp, list) else [])


def status(deposit_address: str) -> dict:
    """DEPOSIT_DETECTED → PROCESSING → ORIGIN_TX_CONFIRMED → SUBMITTED → COMPLETED/FAILED."""
    return _request(f"/status/{deposit_address}")


# The bridge names chains in display form; the CLI keys them short. Without this
# mapping "bsc" never matches "BNB Smart Chain", the minimum comes back None, and
# a sub-minimum deposit sails through — to sit pending at the bridge.
CHAIN_DISPLAY_NAMES = {
    "ethereum": "Ethereum",
    "polygon": "Polygon",
    "base": "Base",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "bsc": "BNB Smart Chain",
}


def min_deposit_usd(chain_name: str, assets: list[dict] | None = None) -> float | None:
    """Smallest minCheckoutUsd advertised for a chain, or None if unlisted.

    Accepts either the CLI's short chain key ("bsc") or the bridge's display name
    ("BNB Smart Chain"). Funds below the minimum sit pending instead of crediting,
    so the send path checks against this first.
    """
    assets = assets if assets is not None else supported_assets()
    display = CHAIN_DISPLAY_NAMES.get(chain_name.lower(), chain_name)
    mins = [a.get("minCheckoutUsd") for a in assets
            if str(a.get("chainName", "")).lower() == display.lower()
            and a.get("minCheckoutUsd") is not None]
    return float(min(mins)) if mins else None
