# tests/test_wallet.py
import json
from types import SimpleNamespace

from typer.testing import CliRunner
from poly.cli import app
from poly import config, context
import poly.groups.wallet as wallet_mod

runner = CliRunner()


def test_import_writes_key(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["wallet", "import", "0x" + "a" * 64])
    assert result.exit_code == 0
    assert json.loads(p.read_text())["private_key"] == "0x" + "a" * 64


def test_address_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    result = runner.invoke(app, ["wallet", "address"])
    assert result.exit_code != 0


def test_create_generates_key(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["wallet", "create"])
    assert result.exit_code == 0
    saved = json.loads(p.read_text())
    assert "private_key" in saved
    assert saved["private_key"].startswith("0x")
    assert len(saved["private_key"]) == 66  # 0x + 64 hex chars


def test_create_blocked_when_key_exists(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"private_key": "0x" + "b" * 64}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["wallet", "create"])
    assert result.exit_code != 0


def test_show_does_not_print_key(tmp_path, monkeypatch):
    raw_key = "0x" + "a" * 64
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"private_key": raw_key}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    # avoid a real network call for the deposit wallet
    monkeypatch.setattr(context, "secure", lambda ctx: SimpleNamespace(wallet="0xDEPOSIT"))
    result = runner.invoke(app, ["wallet", "show"])
    assert result.exit_code == 0
    assert "signer_eoa" in result.output
    assert "0xDEPOSIT" in result.output
    assert raw_key not in result.output


def test_show_reads_env_key(tmp_path, monkeypatch):
    """`wallet show` must honor the same key-resolution order as every other
    command (flag > env > config). A platform that injects POLYMARKET_PRIVATE_KEY
    and never writes config.json still gets a real answer, not nulls."""
    raw_key = "0x" + "a" * 64
    from eth_account import Account
    eoa = Account.from_key(raw_key).address
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", raw_key)
    monkeypatch.setattr(context, "secure", lambda ctx: SimpleNamespace(wallet="0xDEPOSIT"))
    result = runner.invoke(app, ["-o", "json", "wallet", "show"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["signer_eoa"] == eoa
    assert data["api_wallet"] == "0xDEPOSIT"
    assert data["key_source"] == "env"
    assert raw_key not in result.output


def test_show_env_key_without_0x_prefix(tmp_path, monkeypatch):
    """Injected keys often come without the 0x prefix; show must still derive."""
    raw_key = "b" * 64
    from eth_account import Account
    eoa = Account.from_key("0x" + raw_key).address
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", raw_key)
    monkeypatch.setattr(context, "secure", lambda ctx: SimpleNamespace(wallet="0xDEPOSIT"))
    result = runner.invoke(app, ["-o", "json", "wallet", "show"])
    assert result.exit_code == 0
    assert json.loads(result.output)["signer_eoa"] == eoa


def test_show_no_key_names_the_places_it_looked(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", tmp_path / "none.json")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    result = runner.invoke(app, ["-o", "json", "wallet", "show"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["signer_eoa"] is None
    assert data["api_wallet"] is None
    assert "POLYMARKET_PRIVATE_KEY" in data["note"]


def test_reset_requires_force(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"private_key": "0x" + "c" * 64}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["wallet", "reset"])
    assert result.exit_code != 0
    assert p.exists()


def test_reset_force_deletes(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"private_key": "0x" + "d" * 64}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(wallet_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["wallet", "reset", "--force"])
    assert result.exit_code == 0
    assert not p.exists()
