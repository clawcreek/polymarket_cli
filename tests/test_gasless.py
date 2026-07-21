"""Gasless 7702 deposit — the offline encoding that must be byte-exact.

Network-free: the signing, ABI encoding, and payload assembly are pure and are
verified against fixed test vectors. No Relay or RPC calls.
"""
from poly import gasless


def test_execute_selector_is_stable():
    # keccak("execute((((address,uint256,bytes)[],bool),uint256,bytes32,address,uint256),bytes)")[:4]
    assert gasless._EXECUTE_SELECTOR.hex() == "c3c16ee4"


def test_calibur_salt_is_left_padded_address():
    # 12 zero bytes then the 20-byte Calibur address.
    assert len(gasless.CALIBUR_SALT) == 32
    assert gasless.CALIBUR_SALT[:12] == b"\x00" * 12
    assert gasless.CALIBUR_SALT[12:].hex() == gasless.CALIBUR_ADDRESS[2:].lower()


def test_is_delegated_matches_7702_designator():
    code = "0xef0100" + gasless.CALIBUR_ADDRESS[2:].lower()
    assert gasless.is_delegated(code)
    assert not gasless.is_delegated("0x")
    assert not gasless.is_delegated("0xef0100" + "11" * 20)  # delegated elsewhere


def test_extract_calls_flattens_steps_and_hoists_request_id():
    quote = {"steps": [
        {"kind": "transaction", "requestId": "0xreq",
         "items": [{"data": {"to": "0x" + "aa" * 20, "value": "0", "data": "0x095ea7b3"}}]},
        {"kind": "transaction", "requestId": "0xreq",
         "items": [{"data": {"to": "0x" + "bb" * 20, "value": "7", "data": "0xe8017952"}}]},
        {"kind": "signature", "items": []},  # non-transaction steps are skipped
    ]}
    calls, req = gasless._extract_calls(quote)
    assert req == "0xreq"
    assert [c["value"] for c in calls] == [0, 7]
    assert calls[0]["data"] == bytes.fromhex("095ea7b3")
    assert all(isinstance(c["data"], bytes) for c in calls)


def test_extract_calls_rejects_empty():
    import pytest
    with pytest.raises(gasless.GaslessError):
        gasless._extract_calls({"steps": [{"kind": "signature", "items": []}]})


def test_sign_batch_produces_execute_calldata():
    # A throwaway key — signing is deterministic and offline.
    pk = "0x" + "11" * 32
    from eth_account import Account
    user = Account.from_key(pk).address
    calls = [
        {"to": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "value": 0, "data": bytes.fromhex("095ea7b3")},
        {"to": "0x4cd00e387622c35bddb9b4c962c136462338bc31", "value": 0, "data": bytes.fromhex("e8017952")},
    ]
    data = gasless._sign_batch(pk, 56, user, calls, 0)
    assert data[:4] == gasless._EXECUTE_SELECTOR
    # Deterministic for a fixed key + calls + nonce.
    assert gasless._sign_batch(pk, 56, user, calls, 0) == data
    # A different Calibur nonce changes the payload.
    assert gasless._sign_batch(pk, 56, user, calls, 1) != data


def test_sign_authorization_shape():
    pk = "0x" + "11" * 32
    auth = gasless._sign_authorization(pk, 56, 3)
    assert auth["chainId"] == 56
    assert auth["address"].lower() == gasless.CALIBUR_ADDRESS.lower()
    assert auth["nonce"] == 3
    assert auth["yParity"] in (0, 1)
    assert auth["r"].startswith("0x") and auth["s"].startswith("0x")
