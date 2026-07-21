"""EIP-7702 gasless cross-chain deposit via Relay + Calibur.

The signer holds a stablecoin on a chain where it has no native token for gas —
the exact BSC-USDC-with-0-BNB case `deposit scan` flags as `sendable:false`.
This path moves it anyway. It delegates the EOA to Calibur (Uniswap's minimal
7702 batch executor) with an *offline* authorization signature, batches
approve+deposit into one atomic call, and hands the whole thing to Relay's
`/execute` with fee subsidy so a sponsor pays the gas. No native token, no
permit, any ERC-20.

Relay does the cross-chain swap itself and delivers USDC.e on Polygon to the
recipient (the Polymarket deposit wallet), where it is tradable collateral.

Why the signed path (not a plain `execute`): Relay's relayer submits the tx, so
`msg.sender` is the relayer, not the EOA. Calibur's direct `execute` checks the
owner is `msg.sender`; the *signed* `execute(SignedBatchedCall, wrappedSignature)`
verifies an EIP-712 signature instead and lets `executor = address(0)` mean any
address may submit. That is what makes sponsor-submitted, gasless execution work.

Everything here is stdlib + eth-account/eth-abi (already project deps) — no
web3.py, matching the rest of `onchain.py`.
"""
from __future__ import annotations

import json
import os
import urllib.request

from eth_abi import encode as abi_encode
from eth_utils import keccak, to_bytes, to_checksum_address, to_hex

from . import onchain

RELAY_API = os.environ.get("RELAY_API_URL", "https://api.relay.link")

# Calibur — Uniswap's minimal 7702 batch executor, same address on every chain.
CALIBUR_ADDRESS = "0x000000009B1D0aF20D8C6d0A44e162d11F9b8f00"
# bytes32(0): the root key hash, i.e. the EOA owner's own key.
ROOT_KEY_HASH = b"\x00" * 32
# salt = left-pad(caliburAddress, 32): upper 96 bits zero (saltPrefix=0), lower
# 160 bits the implementation address.
CALIBUR_SALT = b"\x00" * 12 + to_bytes(hexstr=CALIBUR_ADDRESS)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# execute((((address,uint256,bytes)[],bool),uint256,bytes32,address,uint256),bytes)
_EXECUTE_SIG = "execute((((address,uint256,bytes)[],bool),uint256,bytes32,address,uint256),bytes)"
_EXECUTE_SELECTOR = keccak(text=_EXECUTE_SIG)[:4]
_SIGNED_BATCHED_CALL_ABI = "(((address,uint256,bytes)[],bool),uint256,bytes32,address,uint256)"

# getSeq(uint256) -> uint256
_GET_SEQ_SELECTOR = "0x" + keccak(text="getSeq(uint256)")[:4].hex()

_EIP712_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
        {"name": "salt", "type": "bytes32"},
    ],
    "SignedBatchedCall": [
        {"name": "batchedCall", "type": "BatchedCall"},
        {"name": "nonce", "type": "uint256"},
        {"name": "keyHash", "type": "bytes32"},
        {"name": "executor", "type": "address"},
        {"name": "deadline", "type": "uint256"},
    ],
    "BatchedCall": [
        {"name": "calls", "type": "Call[]"},
        {"name": "revertOnFailure", "type": "bool"},
    ],
    "Call": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
}


class GaslessError(RuntimeError):
    pass


# --- Relay HTTP -------------------------------------------------------------

def _relay(path: str, body: dict, api_key: str) -> dict:
    req = urllib.request.Request(
        f"{RELAY_API}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:  # surface Relay's error body, not a bare 400
        raise GaslessError(f"{path} failed ({exc.code}): {exc.read().decode()[:400]}") from exc


def status(request_id: str, api_key: str) -> dict:
    """Relay intent status: pending → success | failure | refund."""
    req = urllib.request.Request(
        f"{RELAY_API}/intents/status/v3?requestId={request_id}",
        headers={"x-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def quote(*, user: str, origin_chain_id: int, dest_chain_id: int, origin_currency: str,
          dest_currency: str, amount_base_units: int, recipient: str, api_key: str,
          subsidize: bool = False) -> dict:
    """Cross-chain swap quote. The steps carry the approve + deposit calls we batch."""
    return _relay("/quote", {
        "user": user,
        "originChainId": origin_chain_id,
        "destinationChainId": dest_chain_id,
        "originCurrency": origin_currency,
        "destinationCurrency": dest_currency,
        "amount": str(amount_base_units),
        "tradeType": "EXACT_INPUT",
        "recipient": recipient,
        # Cover Calibur's execute() wrapper overhead (EIP-712 verify + dispatch +
        # optional 7702 authorization) on top of Relay's built-in buffers.
        "originGasOverhead": "80000",
        "subsidizeFees": subsidize,
    }, api_key)


# --- Chain reads ------------------------------------------------------------

def _get_code(chain: str, address: str) -> str:
    return onchain._rpc(chain, "eth_getCode", [address, "latest"]) or "0x"


def is_delegated(code: str) -> bool:
    """True if the EOA already delegates to Calibur (EIP-7702 designator 0xef0100)."""
    c = (code or "").lower()
    return c.startswith("0xef0100") and c[8:] == CALIBUR_ADDRESS[2:].lower()


def _calibur_seq(chain: str, user: str) -> int:
    """Calibur nonce for key 0. Zero before the first delegated call."""
    data = _GET_SEQ_SELECTOR + "0" * 64
    try:
        r = onchain._rpc(chain, "eth_call", [{"to": user, "data": data}, "latest"])
        return int(r, 16) if r and r != "0x" else 0
    except Exception:  # noqa: BLE001 — no code yet / reverts → sequence is 0
        return 0


# --- Signing ----------------------------------------------------------------

def _extract_calls(quote_resp: dict) -> tuple[list[dict], str | None]:
    """Flatten the quote's transaction steps into a batch and hoist the requestId."""
    calls: list[dict] = []
    request_id = None
    for step in quote_resp.get("steps", []):
        if step.get("kind") != "transaction":
            continue
        for item in step.get("items", []):
            d = item.get("data", {})
            calls.append({
                "to": d["to"],
                "value": int(str(d.get("value") or "0")),
                "data": to_bytes(hexstr=d["data"]),
            })
        if step.get("requestId"):
            request_id = step["requestId"]
    if not calls:
        raise GaslessError("quote returned no transaction steps to batch")
    return calls, request_id


def _sign_authorization(private_key: str, chain_id: int, nonce: int) -> dict:
    """Offline EIP-7702 authorization delegating the EOA to Calibur."""
    from eth_account import Account

    auth = Account.from_key(private_key).sign_authorization(
        {"chainId": chain_id, "address": CALIBUR_ADDRESS, "nonce": nonce})
    return {
        "chainId": chain_id,
        "address": to_checksum_address(auth.address),
        "nonce": nonce,
        "yParity": auth.y_parity,
        "r": to_hex(auth.r),
        "s": to_hex(auth.s),
    }


def _sign_batch(private_key: str, chain_id: int, user: str, calls: list[dict],
                calibur_nonce: int) -> bytes:
    """EIP-712 sign the SignedBatchedCall and return execute() calldata.

    executor=0 + a valid owner signature is what lets Relay's relayer submit on
    the EOA's behalf.
    """
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    message = {
        "batchedCall": {
            "calls": [{"to": c["to"], "value": c["value"], "data": c["data"]} for c in calls],
            "revertOnFailure": True,
        },
        "nonce": calibur_nonce,
        "keyHash": ROOT_KEY_HASH,
        "executor": ZERO_ADDRESS,
        "deadline": 0,
    }
    typed = {
        "types": _EIP712_TYPES,
        "primaryType": "SignedBatchedCall",
        "domain": {
            "name": "Calibur", "version": "1.0.0", "chainId": chain_id,
            "verifyingContract": user, "salt": CALIBUR_SALT,
        },
        "message": message,
    }
    signature = Account.sign_message(encode_typed_data(full_message=typed), private_key).signature
    # wrappedSignature = abi.encode(bytes signature, bytes hookData); hookData empty for the root key.
    wrapped = abi_encode(["bytes", "bytes"], [bytes(signature), b""])

    signed_batched_call = (
        ([(c["to"], c["value"], c["data"]) for c in calls], True),
        calibur_nonce,
        ROOT_KEY_HASH,
        ZERO_ADDRESS,
        0,
    )
    payload = abi_encode([_SIGNED_BATCHED_CALL_ABI, "bytes"], [signed_batched_call, wrapped])
    return _EXECUTE_SELECTOR + payload


# --- Orchestrator -----------------------------------------------------------

def build_execution(*, private_key: str, chain: str, quote_resp: dict) -> dict:
    """Assemble the Relay /execute payload from a quote — sign, batch, delegate.

    Returned as a plain dict so callers can preview it (dry-run) before submitting.
    """
    from eth_account import Account

    user = Account.from_key(private_key).address
    chain_id = onchain.CHAINS[chain]["chain_id"]

    calls, request_id = _extract_calls(quote_resp)

    delegated = is_delegated(_get_code(chain, user))
    authorization = None
    if not delegated:
        tx_nonce = onchain._nonce(chain, user)
        authorization = _sign_authorization(private_key, chain_id, tx_nonce)

    calibur_nonce = _calibur_seq(chain, user) if delegated else 0
    call_data = _sign_batch(private_key, chain_id, user, calls, calibur_nonce)

    data = {"chainId": chain_id, "to": user, "data": to_hex(call_data), "value": "0"}
    if authorization:
        data["authorizationList"] = [authorization]

    body = {
        "executionKind": "rawCalls",
        "data": data,
        # false: the relayer fronts origin gas and recoups it from the swap output
        # (standard Relay gasless). true would need an app-funded sponsor deposit.
        # referrer is required by Relay (attribution); self-attribute if unset.
        "executionOptions": {
            "subsidizeFees": False,
            "referrer": os.environ.get("POLYMARKET_RELAY_REFERRER") or user,
        },
    }
    if request_id:
        body["requestId"] = request_id
    return {"delegated": delegated, "request_id": request_id, "execute_body": body}


def submit(execute_body: dict, api_key: str) -> dict:
    """POST the assembled payload to Relay /execute. Returns {requestId, ...}."""
    return _relay("/execute", execute_body, api_key)
