import json
import pytest
from poly import config


def test_resolve_prefers_flag_then_env_then_config():
    assert config.resolve_private_key(flag="0xf", env="0xe", config="0xc") == "0xf"
    assert config.resolve_private_key(flag=None, env="0xe", config="0xc") == "0xe"
    assert config.resolve_private_key(flag=None, env=None, config="0xc") == "0xc"
    assert config.resolve_private_key() is None


def test_save_config_is_chmod_600(tmp_path):
    p = tmp_path / "config.json"
    config.save_config({"private_key": "0xabc"}, path=p)
    assert json.loads(p.read_text())["private_key"] == "0xabc"
    assert (p.stat().st_mode & 0o777) == 0o600


def test_load_settings_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(SystemExit):
        config.load_settings(path=tmp_path / "missing.json")


def test_load_settings_normalizes_0x_prefix(tmp_path):
    p = tmp_path / "config.json"
    config.save_config({"private_key": "abc"}, path=p)
    s = config.load_settings(path=p)
    assert s.private_key == "0xabc"


def test_load_settings_ignores_stale_signature_type(tmp_path, monkeypatch):
    """A config with a non-integer 'signature_type' value must not raise."""
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"private_key": "0xabc", "signature_type": "proxy"}))
    s = config.load_settings(path=p)
    assert s.private_key == "0xabc"


def test_resolve_environment_defaults_to_production(monkeypatch):
    for env in config._URL_ENV.values():
        monkeypatch.delenv(env, raising=False)
    from polymarket.environments import PRODUCTION
    assert config.resolve_environment() is PRODUCTION


def test_resolve_environment_overrides_only_set_urls(monkeypatch):
    for env in config._URL_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("POLYMARKET_CLOB_URL", "http://proxy:7001")
    monkeypatch.setenv("POLYMARKET_GAMMA_URL", "http://proxy:7002")
    from polymarket.environments import PRODUCTION
    env = config.resolve_environment()
    # overridden
    assert env.clob_url == "http://proxy:7001"
    assert env.gamma_url == "http://proxy:7002"
    # untouched URLs stay production
    assert env.data_url == PRODUCTION.data_url
    # chain id + contract addresses MUST stay production (order signatures depend on them)
    assert env.chain_id == PRODUCTION.chain_id
    assert env.collateral_token == PRODUCTION.collateral_token
    assert env.standard_exchange == PRODUCTION.standard_exchange


def test_resolve_relayer_api_key_none_without_env(monkeypatch):
    monkeypatch.delenv("POLYMARKET_RELAYER_API_KEY", raising=False)
    assert config.resolve_relayer_api_key("0x" + "1" * 64) is None


def test_resolve_relayer_api_key_derives_address_from_key(monkeypatch):
    from eth_account import Account
    pk = "0x" + "1" * 64
    monkeypatch.setenv("POLYMARKET_RELAYER_API_KEY", "uuid-abc")
    monkeypatch.delenv("POLYMARKET_RELAYER_ADDRESS", raising=False)
    ak = config.resolve_relayer_api_key(pk)
    assert ak is not None
    assert ak.key == "uuid-abc"
    assert ak.address == Account.from_key(pk).address  # derived from signer when not set


def test_resolve_relayer_api_key_honors_explicit_address(monkeypatch):
    monkeypatch.setenv("POLYMARKET_RELAYER_API_KEY", "uuid-abc")
    monkeypatch.setenv("POLYMARKET_RELAYER_ADDRESS", "0xDeAdBeef00000000000000000000000000000000")
    ak = config.resolve_relayer_api_key("0x" + "1" * 64)
    # SDK checksums (EIP-55) the address; compare case-insensitively
    assert ak.address.lower() == "0xdeadbeef00000000000000000000000000000000"


# ---- deposit-wallet resolution: authoritative sources only, never the SDK's derivation ----

_PK = "0x" + "a" * 64


def _fake_public(profile):
    from types import SimpleNamespace
    return lambda: SimpleNamespace(get_public_profile=lambda address: profile)


def test_resolve_deposit_wallet_uses_polymarket_profile(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(config, "build_public_client", _fake_public(SimpleNamespace(wallet="0xPROXY")))
    assert config.resolve_deposit_wallet(_PK) == "0xPROXY"


def test_resolve_deposit_wallet_none_when_no_profile(monkeypatch):
    # A signer that never logged in to Polymarket has no profile -> nothing authoritative.
    monkeypatch.setattr(config, "build_public_client", _fake_public(None))
    assert config.resolve_deposit_wallet(_PK) is None


def test_resolve_deposit_wallet_swallows_lookup_errors(monkeypatch):
    def boom():
        raise RuntimeError("gamma down")
    monkeypatch.setattr(config, "build_public_client", boom)
    assert config.resolve_deposit_wallet(_PK) is None  # best-effort: never breaks a command


def test_build_secure_client_refuses_to_derive(monkeypatch):
    # No pin and no profile -> must fail loudly rather than let the SDK guess an address.
    monkeypatch.setattr(config, "resolve_deposit_wallet", lambda pk: None)
    with pytest.raises(SystemExit) as exc:
        config.build_secure_client(config.Settings(private_key=_PK, wallet_address=None))
    assert "deposit wallet" in str(exc.value).lower()


def test_build_secure_client_prefers_the_pin_over_the_lookup(monkeypatch):
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(config, "resolve_deposit_wallet",
                        lambda pk: pytest.fail("must not look up when a pin exists"))
    import polymarket
    monkeypatch.setattr(polymarket.SecureClient, "create", staticmethod(fake_create))
    config.build_secure_client(config.Settings(private_key=_PK, wallet_address="0xPINNED"))
    assert seen["wallet"] == "0xPINNED"
