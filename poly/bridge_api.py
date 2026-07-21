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


def min_deposit_usd(chain_name: str, assets: list[dict] | None = None) -> float | None:
    """Smallest minCheckoutUsd advertised for a chain, or None if unlisted.

    Funds below the minimum sit pending instead of crediting, so the send path
    checks against this first.
    """
    assets = assets if assets is not None else supported_assets()
    mins = [a.get("minCheckoutUsd") for a in assets
            if str(a.get("chainName", "")).lower() == chain_name.lower()
            and a.get("minCheckoutUsd") is not None]
    return float(min(mins)) if mins else None
