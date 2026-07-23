"""Minimal EVM JSON-RPC + transfer signing, dependency-light.

The project deliberately carries only polymarket-client, typer and eth-account —
no web3.py. Funding a Polymarket account from another chain needs two things
neither the SDK nor eth-account provides on its own: read a chain (balances,
gas, nonce) and broadcast a signed transfer. Both are plain JSON-RPC, so they
live here on top of urllib, and eth-account signs the transaction.

Nothing here is Polymarket-specific; it is the on-chain plumbing the deposit
flow stands on.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

# Chains the deposit flow can send from. Each carries a couple of public RPCs
# (tried in order, so one rate-limited node doesn't sink a command) and the
# stablecoins users are most likely to hold. Native-token decimals are 18
# everywhere here; stablecoin decimals vary and are per-token.
CHAINS: dict[str, dict] = {
    "ethereum": {
        "chain_id": 1,
        "native": "ETH",
        "rpcs": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com",
                 "https://rpc.ankr.com/eth"],
        "tokens": {
            "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
            "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
            "DAI": ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
        },
    },
    "polygon": {
        "chain_id": 137,
        "native": "POL",
        "rpcs": ["https://polygon-bor-rpc.publicnode.com", "https://polygon.llamarpc.com",
                 "https://rpc.ankr.com/polygon"],
        "tokens": {
            "USDC": ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
            "USDT": ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
            # USDC.e — Polymarket's pre-2026-04 collateral and still the bridge's
            # wrap-source on Polygon. Uppercase key: send() looks up token.upper().
            "USDC.E": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
            # pUSD — Polymarket USD, the exchange collateral since the 2026-04-28
            # upgrade. Bridged/onramp deposits can MINT this straight to an EOA, so
            # scan must see it or funded wallets read as empty (the 乙一 case).
            "PUSD": ("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", 6),
        },
    },
    "base": {
        "chain_id": 8453,
        "native": "ETH",
        "rpcs": ["https://mainnet.base.org", "https://base-rpc.publicnode.com"],
        "tokens": {
            "USDC": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        },
    },
    "arbitrum": {
        "chain_id": 42161,
        "native": "ETH",
        "rpcs": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
        "tokens": {
            "USDC": ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
            "USDT": ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
        },
    },
    "optimism": {
        "chain_id": 10,
        "native": "ETH",
        "rpcs": ["https://mainnet.optimism.io", "https://optimism-rpc.publicnode.com"],
        "tokens": {
            "USDC": ("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6),
            "USDT": ("0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6),
        },
    },
    "bsc": {
        "chain_id": 56,
        "native": "BNB",
        "rpcs": ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.binance.org",
                 "https://binance.llamarpc.com"],
        "tokens": {
            "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
            "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
        },
    },
}

_ERC20_BALANCE_OF = "0x70a08231"   # balanceOf(address)
_ERC20_TRANSFER = "0xa9059cbb"     # transfer(address,uint256)
_ERC20_GAS_LIMIT = 100_000         # generous ceiling for a token transfer
_NATIVE_GAS_LIMIT = 21_000


class RpcError(RuntimeError):
    pass


def _rpc(chain: str, method: str, params: list):
    """Call a chain's JSON-RPC, trying each node until one answers."""
    cfg = CHAINS[chain]
    last = None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in cfg["rpcs"]:
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json", "User-Agent": "poly-cli/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                out = json.load(resp)
            if "error" in out:
                last = RpcError(str(out["error"])[:120])
                continue
            return out.get("result")
        except Exception as exc:  # noqa: BLE001 — try the next node
            last = exc
            continue
    raise RpcError(f"{chain} RPC unreachable: {str(last)[:100]}")


def _pad(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


def native_balance(chain: str, address: str) -> int:
    """Native-token balance in wei."""
    r = _rpc(chain, "eth_getBalance", [address, "latest"])
    return int(r, 16) if r else 0


def token_balance(chain: str, token_addr: str, address: str) -> int:
    """ERC20 balance in base units."""
    data = _ERC20_BALANCE_OF + _pad(address)
    r = _rpc(chain, "eth_call", [{"to": token_addr, "data": data}, "latest"])
    return int(r, 16) if r and r != "0x" else 0


def gas_price(chain: str) -> int:
    r = _rpc(chain, "eth_gasPrice", [])
    return int(r, 16) if r else 0


def _nonce(chain: str, address: str) -> int:
    r = _rpc(chain, "eth_getTransactionCount", [address, "pending"])
    return int(r, 16) if r else 0


@dataclass
class GasEstimate:
    price_wei: int
    limit: int
    native_symbol: str

    @property
    def cost_wei(self) -> int:
        return self.price_wei * self.limit

    @property
    def cost_native(self) -> float:
        return self.cost_wei / 1e18


def estimate_gas(chain: str, is_token: bool) -> GasEstimate:
    limit = _ERC20_GAS_LIMIT if is_token else _NATIVE_GAS_LIMIT
    return GasEstimate(gas_price(chain), limit, CHAINS[chain]["native"])


def send_transfer(chain: str, private_key: str, to_address: str, *,
                  token_addr: str | None = None, amount_base_units: int) -> str:
    """Sign and broadcast a transfer, returning the tx hash.

    token_addr None → native transfer; otherwise an ERC20 transfer of
    amount_base_units. Legacy (type-0) gas is used because it works on every
    chain here, including BSC, without needing EIP-1559 fee history.
    """
    from eth_account import Account

    acct = Account.from_key(private_key)
    cfg = CHAINS[chain]
    price = gas_price(chain)
    nonce = _nonce(chain, acct.address)

    if token_addr is None:
        tx = {"to": to_address, "value": amount_base_units, "gas": _NATIVE_GAS_LIMIT}
    else:
        data = _ERC20_TRANSFER + _pad(to_address) + hex(amount_base_units)[2:].rjust(64, "0")
        tx = {"to": token_addr, "value": 0, "data": data, "gas": _ERC20_GAS_LIMIT}
    tx.update({"nonce": nonce, "gasPrice": price, "chainId": cfg["chain_id"]})

    signed = acct.sign_transaction(tx)
    raw = signed.raw_transaction.hex()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    return _rpc(chain, "eth_sendRawTransaction", [raw])


def signer_address(private_key: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key).address
