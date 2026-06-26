# poly CLI Refactor — P0+P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the buy/sell-only `poly` tool into a Typer-based `poly <group> <verb>` CLI with a global `-o/--output json|table`, config-file wallet model, and the trading + core read commands (the daily driver).

**Architecture:** A Typer root app mounts one small sub-app per command group. Command bodies are thin: resolve a client → call one py-sdk method → `emit()`. All key/config logic lives in `config.py`, all formatting in `output.py`, all order-flow safety in `trade.py`. P0 builds the skeleton + config + wallet + output; P1 adds trading and `data positions/value` plus `buy`/`sell` aliases.

**Tech Stack:** Python ≥3.11, [Typer](https://typer.tiangolo.com/), `polymarket-client` (py-sdk, beta), `eth-account` (key gen), `pytest`. Package manager: `uv`.

## Global Constraints

- **Do not over-engineer; keep the repo human-readable.** No metaprogramming, no command auto-generation, no plugin system. A reader follows any command top-to-bottom.
- **Thin command bodies:** resolve client → one SDK call → `emit()`. Logic lives in `orders.py`/`trade.py`/`config.py`.
- **Many small files**, one Typer sub-app per group; 800-line hard cap per file.
- **Minimal deps:** add only `typer` and `eth-account`; **remove `python-dotenv`**; no `rich`/`tabulate`.
- **Default signature type = 3** (deposit wallet). Configurable via config or `--signature-type`.
- **Key resolution order:** `--private-key` flag > `POLYMARKET_PRIVATE_KEY` env > `~/.config/polymarket/config.json`. The project `.env` is no longer read.
- **Trading safety:** preview + typed-`YES` (skippable with `--yes`) + `--dry-run` (build/sign, never submit). Market BUY keeps the `max_spend` cap. Prices/sizes passed to the SDK as **strings**.
- **Author guardrail:** the implementer/Claude only ever runs `--dry-run`; real order submission is the user's.
- All tests are **offline** (mock `PublicClient`/`SecureClient`); never place a live order in a test. Target ≥80% coverage on logic modules.
- Commit messages: `<type>: <desc>` (feat/refactor/test/chore/docs). No co-author trailer.

---

## File Structure

| File | Responsibility |
|---|---|
| `poly/cli.py` | Typer root app, global options callback, group mounting, `buy`/`sell` aliases, `main()` error envelope |
| `poly/context.py` | `CliContext` dataclass + `public(ctx)` / `secure(ctx)` lazy client accessors |
| `poly/config.py` | config.json load/save, key resolution, `Settings`, client builders |
| `poly/output.py` | `emit(fmt, data)` (json/table), serialization, `print_error(fmt, msg)` |
| `poly/pagination.py` | `collect(paginator, limit, all_)` |
| `poly/trade.py` | order-flow orchestration: build plan → preview → (dry-run/confirm) → submit; reused by `clob_trade` + `buy`/`sell` |
| `poly/orders.py` | (exists) validation/sizing/build — unchanged interface |
| `poly/market.py` | (exists) target resolution — unchanged interface |
| `poly/groups/clob_trade.py` | `clob` trading + account read commands |
| `poly/groups/data.py` | `data positions` / `data value` (more in P2) |
| `poly/groups/wallet.py` | `wallet` create/import/show/address/reset |
| `poly/groups/setup.py` | `setup` wizard |
| `tests/test_*.py` | one module per new file with logic |

Files to delete at the end of P0/P1: the old argparse `poly/cli.py` is replaced; the old `.env`-based `config.py` is replaced; the buy/sell-era `tests/test_cli.py` is replaced by `test_trade.py`/`test_buy_sell.py`. `poly/orders.py` and `poly/market.py` are kept as-is.

---

## P0 — Skeleton, config, output, wallet

### Task 1: Dependencies + Typer skeleton

**Files:**
- Modify: `pyproject.toml`
- Modify: `poly/cli.py` (replace argparse root with a minimal Typer app)
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Produces: `poly.cli.app` (a `typer.Typer`), `poly.cli.main() -> int` (console entry point).

- [ ] **Step 1: Update dependencies**

In `pyproject.toml`, set:
```toml
dependencies = [
    "polymarket-client==0.1.0b9",
    "typer>=0.12",
    "eth-account>=0.13",
]
```
Remove `python-dotenv`. Keep `[project.scripts] poly = "poly.cli:main"`.

- [ ] **Step 2: Sync env**

Run: `uv sync --extra dev`
Expected: installs `typer`; no `python-dotenv` in the resolved set.

- [ ] **Step 3: Write the failing smoke test**

```python
# tests/test_cli_smoke.py
from typer.testing import CliRunner
from poly.cli import app

runner = CliRunner()

def test_help_lists_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "buy" in result.output
```

- [ ] **Step 4: Run it, verify it fails**

Run: `uv run pytest tests/test_cli_smoke.py -v`
Expected: FAIL (import error / no `buy` yet).

- [ ] **Step 5: Minimal Typer root**

```python
# poly/cli.py
import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Polymarket CLI.")


@app.command()
def buy() -> None:
    """Buy an outcome (implemented in Task 10)."""
    raise typer.Exit(0)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run test, verify pass**

Run: `uv run pytest tests/test_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml poly/cli.py tests/test_cli_smoke.py uv.lock
git commit -m "refactor: replace argparse root with Typer skeleton"
```

---

### Task 2: config.py — config file + key resolution

**Files:**
- Create: `poly/config.py` (replaces the old `.env` version)
- Test: `tests/test_config.py` (replaces the old one)

**Interfaces:**
- Produces:
  - `CONFIG_PATH: pathlib.Path`
  - `DEFAULT_SIGNATURE_TYPE = 3`
  - `Settings(private_key: str, signature_type: int = 3, wallet_address: str | None = None)` (frozen; `private_key` repr-hidden)
  - `load_config(path=CONFIG_PATH) -> dict`
  - `save_config(data: dict, path=CONFIG_PATH) -> None` (mkdir, write, chmod 0600)
  - `resolve_private_key(flag=None, env=None, config=None) -> str | None` (order: flag>env>config)
  - `load_settings(*, private_key=None, signature_type=None, path=CONFIG_PATH) -> Settings` (raises `SystemExit` with friendly msg if no key)
  - `build_public_client()` / `build_secure_client(settings)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
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
    config.save_config({"private_key": "0xabc", "signature_type": 3}, path=p)
    assert json.loads(p.read_text())["private_key"] == "0xabc"
    assert (p.stat().st_mode & 0o777) == 0o600


def test_load_settings_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(SystemExit):
        config.load_settings(path=tmp_path / "missing.json")


def test_load_settings_normalizes_0x_and_defaults_type(tmp_path):
    p = tmp_path / "config.json"
    config.save_config({"private_key": "abc"}, path=p)
    s = config.load_settings(path=p)
    assert s.private_key == "0xabc"
    assert s.signature_type == 3
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (module/functions missing).

- [ ] **Step 3: Implement config.py**

```python
# poly/config.py
"""Config-file wallet model and client construction.

Key resolution order: --private-key flag > POLYMARKET_PRIVATE_KEY env >
~/.config/polymarket/config.json. The project .env is intentionally NOT read.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
CONFIG_PATH = _CONFIG_HOME / "polymarket" / "config.json"
DEFAULT_SIGNATURE_TYPE = 3


@dataclass(frozen=True)
class Settings:
    private_key: str = field(repr=False)
    signature_type: int = DEFAULT_SIGNATURE_TYPE
    wallet_address: str | None = None


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file {path} is not valid JSON: {exc}")


def save_config(data: dict, path: Path = CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def resolve_private_key(flag=None, env=None, config=None) -> str | None:
    return flag or env or config or None


def _normalize_key(key: str) -> str:
    key = key.strip()
    return key if key.startswith("0x") else "0x" + key


def load_settings(*, private_key=None, signature_type=None, path: Path = CONFIG_PATH) -> Settings:
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
    sig = signature_type if signature_type is not None else int(cfg.get("signature_type", DEFAULT_SIGNATURE_TYPE))
    return Settings(private_key=_normalize_key(key), signature_type=sig, wallet_address=cfg.get("wallet_address"))


def build_public_client():
    from polymarket import PublicClient
    return PublicClient()


def build_secure_client(settings: Settings):
    from polymarket import SecureClient
    return SecureClient.create(private_key=settings.private_key, wallet=settings.wallet_address)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add poly/config.py tests/test_config.py
git commit -m "refactor: config-file wallet model with flag>env>file key resolution"
```

---

### Task 3: output.py — emit + error envelope

**Files:**
- Create: `poly/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Produces:
  - `to_jsonable(obj) -> Any` (pydantic → `model_dump(mode="json")`; list/dict recursion; Decimal→str)
  - `emit(fmt: str, data) -> None` (`"json"` prints `json.dumps(..., indent=2)`; `"table"` prints aligned table / key-value)
  - `print_error(fmt: str, message: str) -> None` (json → `{"error": msg}` to stdout; table → `Error: msg` to stderr)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_output.py
import json
from decimal import Decimal
from poly import output


def test_to_jsonable_handles_decimal_and_pydantic():
    class Fake:
        def model_dump(self, mode="python"):
            return {"price": Decimal("0.5")}
    assert output.to_jsonable(Decimal("0.5")) == "0.5"
    assert output.to_jsonable([Fake()]) == [{"price": "0.5"}]


def test_emit_json(capsys):
    output.emit("json", {"a": 1})
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_emit_table_list(capsys):
    output.emit("table", [{"id": "1", "q": "x"}, {"id": "2", "q": "y"}])
    out = capsys.readouterr().out
    assert "id" in out and "q" in out and "x" in out


def test_print_error_json(capsys):
    output.print_error("json", "boom")
    assert json.loads(capsys.readouterr().out) == {"error": "boom"}


def test_print_error_table_stderr(capsys):
    output.print_error("table", "boom")
    captured = capsys.readouterr()
    assert "Error: boom" in captured.err
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_output.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement output.py**

```python
# poly/output.py
"""Output formatting: one place for json vs table rendering and errors."""

import json
import sys
from decimal import Decimal


def to_jsonable(obj):
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump(mode="json"))
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def _render_table(data) -> str:
    data = to_jsonable(data)
    if isinstance(data, list):
        if not data:
            return "(no results)"
        cols = list({k: None for row in data for k in (row if isinstance(row, dict) else {})})
        widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in data)) for c in cols}
        header = "  ".join(c.ljust(widths[c]) for c in cols)
        rows = ["  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols) for r in data]
        return "\n".join([header, *rows])
    if isinstance(data, dict):
        w = max((len(k) for k in data), default=0)
        return "\n".join(f"{k.ljust(w)}  {v}" for k, v in data.items())
    return str(data)


def emit(fmt: str, data) -> None:
    if fmt == "json":
        print(json.dumps(to_jsonable(data), indent=2))
    else:
        print(_render_table(data))


def print_error(fmt: str, message: str) -> None:
    if fmt == "json":
        print(json.dumps({"error": message}))
    else:
        print(f"Error: {message}", file=sys.stderr)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_output.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add poly/output.py tests/test_output.py
git commit -m "feat: output layer with json/table rendering and error envelope"
```

---

### Task 4: context.py + global options + main() error envelope

**Files:**
- Create: `poly/context.py`
- Modify: `poly/cli.py` (root callback wiring global options; `main()` wrapper)
- Test: `tests/test_cli_global.py`

**Interfaces:**
- Consumes: `config.build_public_client`, `config.load_settings`, `config.build_secure_client`, `output.print_error`.
- Produces:
  - `CliContext(output: str = "table", private_key: str | None = None, signature_type: int | None = None)`
  - `public(ctx: typer.Context)` → PublicClient (cached on ctx)
  - `secure(ctx: typer.Context)` → SecureClient (cached on ctx)
  - root callback storing `CliContext` on `ctx.obj`; `_OUTPUT["fmt"]` module state for error formatting.

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli_global.py
from typer.testing import CliRunner
from poly.cli import app

runner = CliRunner()


def test_global_output_flag_parses():
    # `buy --help` should render with the global -o flag present
    result = runner.invoke(app, ["-o", "json", "buy", "--help"])
    assert result.exit_code == 0


def test_unknown_command_is_clean_error():
    result = runner.invoke(app, ["definitely-not-a-command"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_cli_global.py -v`
Expected: FAIL (global flag not defined).

- [ ] **Step 3: Implement context.py**

```python
# poly/context.py
import typer
from dataclasses import dataclass

from . import config


@dataclass
class CliContext:
    output: str = "table"
    private_key: str | None = None
    signature_type: int | None = None
    _public: object = None
    _secure: object = None


def _ctx(ctx: typer.Context) -> CliContext:
    if not isinstance(ctx.obj, CliContext):
        ctx.obj = CliContext()
    return ctx.obj


def public(ctx: typer.Context):
    c = _ctx(ctx)
    if c._public is None:
        c._public = config.build_public_client()
    return c._public


def secure(ctx: typer.Context):
    c = _ctx(ctx)
    if c._secure is None:
        settings = config.load_settings(private_key=c.private_key, signature_type=c.signature_type)
        c._secure = config.build_secure_client(settings)
    return c._secure
```

- [ ] **Step 4: Wire root callback + error envelope in cli.py**

Replace `poly/cli.py` top with:
```python
# poly/cli.py
import typer

from .context import CliContext

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Polymarket CLI.")
_OUTPUT = {"fmt": "table"}  # mirrored for main()'s error envelope


@app.callback()
def root(
    ctx: typer.Context,
    output: str = typer.Option("table", "--output", "-o", help="table or json"),
    private_key: str = typer.Option(None, "--private-key", help="Override signer key."),
    signature_type: int = typer.Option(None, "--signature-type", help="0/1/2/3 (default 3)."),
) -> None:
    if output not in ("table", "json"):
        raise typer.BadParameter("--output must be 'table' or 'json'")
    _OUTPUT["fmt"] = output
    ctx.obj = CliContext(output=output, private_key=private_key, signature_type=signature_type)
```

Replace `main()` with the error envelope (keep the `buy` stub from Task 1 for now):
```python
def main() -> int:
    import click
    from polymarket import PolymarketError
    from .output import print_error
    try:
        app(standalone_mode=False)
        return 0
    except (click.exceptions.Abort, KeyboardInterrupt):
        print_error(_OUTPUT["fmt"], "aborted")
        return 1
    except click.exceptions.ClickException as exc:  # usage errors
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print_error(_OUTPUT["fmt"], exc.code)
            return 1
        return exc.code or 0
    except (ValueError, PolymarketError) as exc:
        print_error(_OUTPUT["fmt"], str(exc))
        return 1
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run pytest tests/test_cli_global.py tests/test_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add poly/context.py poly/cli.py tests/test_cli_global.py
git commit -m "feat: global -o/--private-key/--signature-type options and error envelope"
```

---

### Task 5: pagination.py

**Files:**
- Create: `poly/pagination.py`
- Test: `tests/test_pagination.py`

**Interfaces:**
- Produces: `collect(paginator, limit: int | None = None, all_: bool = False) -> list` — returns first page items (sliced to `limit`), or pages through when `all_=True`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_pagination.py
from types import SimpleNamespace
from poly.pagination import collect


def _single_page(items):
    return SimpleNamespace(first_page=lambda: SimpleNamespace(items=items, has_next=False))


def test_collect_first_page_limit():
    assert collect(_single_page([1, 2, 3, 4, 5]), limit=3) == [1, 2, 3]


def test_collect_no_limit_returns_all_first_page():
    assert collect(_single_page([1, 2, 3])) == [1, 2, 3]
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement pagination.py**

```python
# poly/pagination.py
"""Uniform collection over the SDK's Paginator objects."""


def collect(paginator, limit: int | None = None, all_: bool = False) -> list:
    page = paginator.first_page()
    items = list(getattr(page, "items", []) or [])
    while all_ and getattr(page, "has_next", False):
        page = page.next_page()
        items.extend(getattr(page, "items", []) or [])
    if limit is not None and not all_:
        return items[:limit]
    return items
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poly/pagination.py tests/test_pagination.py
git commit -m "feat: pagination.collect helper"
```

---

### Task 6: wallet group

**Files:**
- Create: `poly/groups/__init__.py` (empty), `poly/groups/wallet.py`
- Modify: `poly/cli.py` (mount `wallet`)
- Test: `tests/test_wallet.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.save_config`, `config.CONFIG_PATH`, `config.load_settings`, `output.emit`.
- Produces: `poly.groups.wallet.app` with commands `create`, `import` (function `import_key`), `show`, `address`, `reset`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_wallet.py
import json
from typer.testing import CliRunner
from poly.cli import app
from poly import config
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
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_wallet.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement wallet.py**

```python
# poly/groups/wallet.py
"""Local wallet/key management backed by config.json."""

import typer
from eth_account import Account

from ..config import CONFIG_PATH, load_config, save_config, load_settings
from ..output import emit
from ..context import CliContext

app = typer.Typer(no_args_is_help=True, help="Manage the signer key (config.json).")


def _fmt(ctx: typer.Context) -> str:
    return ctx.obj.output if isinstance(ctx.obj, CliContext) else "table"


def _store_key(key: str) -> str:
    key = key if key.startswith("0x") else "0x" + key
    cfg = load_config()
    cfg["private_key"] = key
    save_config(cfg)
    return Account.from_key(key).address


@app.command()
def create(ctx: typer.Context, force: bool = typer.Option(False, "--force")) -> None:
    """Generate a new random wallet and save it."""
    if load_config().get("private_key") and not force:
        raise SystemExit("A key already exists. Use --force to overwrite.")
    acct = Account.create()
    addr = _store_key(acct.key.hex())
    emit(_fmt(ctx), {"address": addr, "config": str(CONFIG_PATH)})


@app.command("import")
def import_key(ctx: typer.Context, private_key: str = typer.Argument(...)) -> None:
    """Import an existing private key."""
    addr = _store_key(private_key)
    emit(_fmt(ctx), {"address": addr, "config": str(CONFIG_PATH)})


@app.command()
def show(ctx: typer.Context) -> None:
    """Show wallet address + config path (never prints the key)."""
    cfg = load_config()
    key = cfg.get("private_key")
    addr = Account.from_key(key).address if key else None
    emit(_fmt(ctx), {"address": addr, "signature_type": cfg.get("signature_type", 3), "config": str(CONFIG_PATH)})


@app.command()
def address(ctx: typer.Context) -> None:
    """Print the wallet address."""
    pk = ctx.obj.private_key if isinstance(ctx.obj, CliContext) else None
    settings = load_settings(private_key=pk)
    emit(_fmt(ctx), {"address": Account.from_key(settings.private_key).address})


@app.command()
def reset(ctx: typer.Context, force: bool = typer.Option(False, "--force")) -> None:
    """Delete the saved config."""
    if not force:
        raise SystemExit("This deletes your saved key. Re-run with --force to confirm.")
    CONFIG_PATH.unlink(missing_ok=True)
    emit(_fmt(ctx), {"deleted": str(CONFIG_PATH)})
```

> Note: `show`/`reset` reference the module-level `CONFIG_PATH` imported from `config`; tests patch both `config.CONFIG_PATH` and `wallet.CONFIG_PATH`. `create`/`import`/`show` go through `load_config`/`save_config`, which read `config.CONFIG_PATH` at call time.

- [ ] **Step 4: Mount in cli.py**

Add after the root callback:
```python
from .groups import wallet
app.add_typer(wallet.app, name="wallet")
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run pytest tests/test_wallet.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add poly/groups/__init__.py poly/groups/wallet.py poly/cli.py tests/test_wallet.py
git commit -m "feat: wallet group (create/import/show/address/reset)"
```

---

### Task 7: setup wizard

**Files:**
- Create: `poly/groups/setup.py`
- Modify: `poly/cli.py` (register `setup` as a top-level command)
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `config.load_config/save_config/CONFIG_PATH/DEFAULT_SIGNATURE_TYPE`, `eth_account.Account`, `output.emit`.
- Produces: `poly.groups.setup.setup_cmd(ctx, private_key: str | None)` registered as `poly setup`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_setup.py
import json
from typer.testing import CliRunner
from poly.cli import app
from poly import config
import poly.groups.setup as setup_mod

runner = CliRunner()


def test_setup_imports_given_key(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(setup_mod, "CONFIG_PATH", p)
    result = runner.invoke(app, ["setup", "--private-key", "0x" + "b" * 64])
    assert result.exit_code == 0
    assert json.loads(p.read_text())["private_key"] == "0x" + "b" * 64
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_setup.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement setup.py**

```python
# poly/groups/setup.py
"""First-time setup: store a key in config.json (migrates an old .env key)."""

import typer
from eth_account import Account

from ..config import CONFIG_PATH, DEFAULT_SIGNATURE_TYPE, load_config, save_config
from ..output import emit


def setup_cmd(ctx: typer.Context, private_key: str = typer.Option(None, "--private-key")) -> None:
    """Configure your signer key (use --private-key, or paste when prompted)."""
    key = private_key or typer.prompt("Signer private key (0x...)", hide_input=True)
    key = key if key.startswith("0x") else "0x" + key
    cfg = load_config()
    cfg["private_key"] = key
    cfg.setdefault("signature_type", DEFAULT_SIGNATURE_TYPE)
    save_config(cfg)
    fmt = getattr(ctx.obj, "output", "table")
    emit(fmt, {"address": Account.from_key(key).address, "config": str(CONFIG_PATH)})
```

- [ ] **Step 4: Register in cli.py**

```python
from .groups import setup as setup_group
app.command("setup")(setup_group.setup_cmd)
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run pytest tests/test_setup.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add poly/groups/setup.py poly/cli.py tests/test_setup.py
git commit -m "feat: setup wizard to store/migrate signer key"
```

---

## P1 — Trading + core reads

### Task 8: trade.py — order-flow orchestration (extracted, reusable)

**Files:**
- Create: `poly/trade.py`
- Test: `tests/test_trade.py`

**Interfaces:**
- Consumes: `orders.py` (`build_signed_limit_order`, `build_signed_market_order`, `post_signed_order`, `describe_response`, `is_accepted`, `validate_price`, `round_to_tick`, `compute_size_from_usd`, `validate_size`, `normalize_side`), `market.resolve_target`, `market.live_price`, `output.emit`.
- Produces:
  - `OrderPlan` (frozen dataclass: `kind, side, token_id, order_type, price=None, size=None, amount=None, shares=None, max_spend=None, requested_price=None, tick_assumed=False`)
  - `build_plan(*, side, market_order, token_id=None, slug=None, url=None, outcome="yes", usd=None, size=None, price=None, order_type=None, max_spend=None, pub) -> tuple[ResolvedTarget, OrderPlan]`
  - `run(ctx, *, pub, secure_factory, target, plan, dry_run: bool, yes: bool) -> int` (emit preview; dry-run emits signed identity & returns 0 without posting; else confirm + submit; returns exit code 0/1)

- [ ] **Step 1: Write failing tests** (port the safety guarantees from the old `tests/test_cli.py`)

```python
# tests/test_trade.py
from decimal import Decimal
from types import SimpleNamespace
import pytest
from poly import trade


class FakePub:
    def __init__(self, market=None, by_token=None):
        self._m, self._t = market, by_token or []
    def get_market(self, slug=None, url=None): return self._m
    def list_markets(self, clob_token_ids=None):
        return SimpleNamespace(first_page=lambda: SimpleNamespace(items=self._t))
    def get_price(self, token_id=None, side=None): return Decimal("0.50")


def _market():
    return SimpleNamespace(
        question="Q", condition_id="0xc",
        outcomes=SimpleNamespace(
            yes=SimpleNamespace(token_id="111", label="Yes", price=Decimal("0.5")),
            no=SimpleNamespace(token_id="222", label="No", price=Decimal("0.5"))),
        trading=SimpleNamespace(minimum_tick_size="0.01", minimum_order_size="5"),
        prices=SimpleNamespace(best_bid="0.49", best_ask="0.51"))


def test_build_plan_limit_usd_to_size():
    pub = FakePub(by_token=[_market()])
    target, plan = trade.build_plan(side="BUY", market_order=False, token_id="111",
                                    usd="1", price="0.5", pub=pub)
    assert plan.kind == "limit" and str(plan.size) == "2"


def test_market_buy_requires_usd():
    pub = FakePub(market=_market())
    with pytest.raises(SystemExit):
        trade.build_plan(side="BUY", market_order=True, slug="x", size="5", pub=pub)


def test_market_sell_requires_size():
    pub = FakePub(market=_market())
    with pytest.raises(SystemExit):
        trade.build_plan(side="SELL", market_order=True, slug="x", usd="5", pub=pub)
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_trade.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement trade.py**

Port the planning + preview + submit logic from the prior `poly/cli.py` (commit `2979939`): `_resolve_order_plan`→split into `_limit_plan`/`_market_plan`, `_print_preview`/`_print_signed_identity`→build a dict and `emit`, `_submit`/`_confirm`/`run_order`→`run`. Keep every safety rule verbatim: `0<price<1`; `round_to_tick` then re-validate (reject if rounds to 0/1); side-aware market mapping (BUY→`amount` + `max_spend` default = `usd`; SELL→`shares`); string serialization; dry-run never posts; typed-`YES` confirm; approvals retry on `InsufficientAllowanceError`.

```python
# poly/trade.py  (skeleton — fill bodies by porting from the old cli.py)
from dataclasses import dataclass
from decimal import Decimal
from . import orders
from .market import resolve_target, live_price
from .output import emit

try:
    from polymarket import InsufficientAllowanceError
except Exception:
    InsufficientAllowanceError = ()


@dataclass(frozen=True)
class OrderPlan:
    kind: str            # "limit" | "market"
    side: str
    token_id: str
    order_type: str
    price: Decimal | None = None
    size: Decimal | None = None
    amount: str | None = None
    shares: str | None = None
    max_spend: str | None = None
    requested_price: Decimal | None = None
    tick_assumed: bool = False


def _market_plan(side, target, usd, size, order_type, max_spend) -> OrderPlan:
    ot = (order_type or "FAK").upper()
    if side == "BUY":
        if usd is None:
            raise SystemExit("A market BUY needs --usd (USD to spend); --size is for market SELL.")
        return OrderPlan("market", side, target.token_id, ot, amount=usd, max_spend=(max_spend or usd))
    if size is None:
        raise SystemExit("A market SELL needs --size (shares to sell); --usd is for market BUY.")
    return OrderPlan("market", side, target.token_id, ot, shares=size)


def _limit_plan(side, target, usd, size, price, order_type) -> OrderPlan:
    if (order_type or "GTC").upper() != "GTC":
        raise SystemExit("Limit orders only support --order-type GTC. Use --market for FAK/FOK.")
    if not price:
        raise SystemExit("--price is required for limit orders (or use --market).")
    requested = orders.validate_price(price)
    p = orders.round_to_tick(requested, target.tick_size)
    try:
        orders.validate_price(p)
    except ValueError:
        tick = orders.decimal_str(target.tick_size) if target.tick_size else "0.01"
        raise SystemExit(f"--price {orders.decimal_str(requested)} rounds to {orders.decimal_str(p)} "
                         f"at tick {tick}, which is not tradable (must be strictly between 0 and 1).")
    sz = orders.compute_size_from_usd(usd, p) if usd is not None else orders.validate_size(size)
    return OrderPlan("limit", side, target.token_id, "GTC", price=p, size=sz,
                     requested_price=requested, tick_assumed=target.tick_size is None)


def build_plan(*, side, market_order, token_id=None, slug=None, url=None, outcome="yes",
               usd=None, size=None, price=None, order_type=None, max_spend=None, pub):
    target = resolve_target(pub, token_id=token_id, slug=slug, url=url, outcome=outcome)
    plan = (_market_plan if market_order else _limit_plan)(
        side, target, usd, size, *( (order_type, max_spend) if market_order else (price, order_type) ))
    return target, plan


def run(ctx, *, pub, secure_factory, target, plan, dry_run, yes) -> int:
    fmt = getattr(ctx.obj, "output", "table")
    client = secure_factory()
    book = live_price(pub, plan.token_id, plan.side)
    emit(fmt, _preview_dict(target, plan, client, book))
    signed = _build_signed(client, plan)
    if dry_run:
        emit(fmt, _signed_dict(client, signed))
        return 0
    if not yes and not _confirm('Type "YES" to submit this order: '):
        emit(fmt, {"aborted": True})
        return 1
    resp = _submit(client, signed)
    emit(fmt, {"result": orders.describe_response(resp)})
    return 0 if orders.is_accepted(resp) else 1

# _preview_dict, _signed_dict, _build_signed, _submit, _confirm: port verbatim from old cli.py,
# returning dicts for emit() instead of print().
```

(The old `poly/cli.py` from commit `2979939` is the authoritative source for the ported bodies — copy the logic exactly, swapping `print(...)`/`_print_*` for dict-returning helpers fed to `emit`.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_trade.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poly/trade.py tests/test_trade.py
git commit -m "refactor: extract reusable order-flow orchestration into trade.py"
```

---

### Task 9: clob trading + account-read commands

**Files:**
- Create: `poly/groups/clob_trade.py`
- Modify: `poly/cli.py` (mount `clob`)
- Test: `tests/test_clob_trade.py`

**Interfaces:**
- Consumes: `trade.build_plan`, `trade.run`, `context.public`, `context.secure`, `output.emit`, `pagination.collect`, `orders.normalize_side`.
- Produces: `poly.groups.clob_trade.app` with: `create-order`, `market-order`, `post-orders`, `cancel`, `cancel-orders`, `cancel-market`, `cancel-all`, `orders`, `order`, `trades`, `balance`, `update-balance`.

- [ ] **Step 1: Write failing tests** (representative: trade dry-run + a read)

```python
# tests/test_clob_trade.py
from decimal import Decimal
from types import SimpleNamespace
from typer.testing import CliRunner
from poly.cli import app
from poly import context

runner = CliRunner()


class FakePub:
    def list_markets(self, clob_token_ids=None):
        return SimpleNamespace(first_page=lambda: SimpleNamespace(items=[]))
    def get_price(self, token_id=None, side=None): return Decimal("0.5")


class FakeSecure:
    wallet = "0xWALLET"
    def __init__(self): self.posted = []
    def create_limit_order(self, **k):
        return SimpleNamespace(maker=self.wallet, signer=self.wallet, token_id=k["token_id"],
                               side=k["side"], maker_amount="1", taker_amount="2", order_type="GTC")
    def post_order(self, s): self.posted.append(s); return SimpleNamespace(ok=True, order_id="o1", status="MATCHED")
    def list_open_orders(self, **k):
        return SimpleNamespace(first_page=lambda: SimpleNamespace(items=[{"id": "o1", "price": "0.5"}], has_next=False))


def test_create_order_dry_run_does_not_post(monkeypatch):
    fake = FakeSecure()
    monkeypatch.setattr(context, "public", lambda ctx: FakePub())
    monkeypatch.setattr(context, "secure", lambda ctx: fake)
    result = runner.invoke(app, ["clob", "create-order", "--token", "111", "--side", "buy",
                                 "--size", "5", "--price", "0.5", "--dry-run"])
    assert result.exit_code == 0
    assert fake.posted == []


def test_orders_read_json(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    result = runner.invoke(app, ["-o", "json", "clob", "orders"])
    assert result.exit_code == 0 and "o1" in result.output
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_clob_trade.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement clob_trade.py**

Thin commands. Trading delegates to `trade`; reads call one SDK method + `emit`. Full code for two representative commands; the rest follow the identical shape with the exact SDK calls listed.

```python
# poly/groups/clob_trade.py
import typer
from ..context import public, secure
from ..output import emit
from ..pagination import collect
from ..orders import normalize_side
from .. import trade

app = typer.Typer(no_args_is_help=True, help="CLOB trading and account reads.")


@app.command("create-order")
def create_order(ctx: typer.Context,
                 token: str = typer.Option(None, "--token", "--token-id"),
                 slug: str = typer.Option(None), url: str = typer.Option(None),
                 outcome: str = typer.Option("yes"),
                 side: str = typer.Option(..., "--side"),
                 price: str = typer.Option(..., "--price"),
                 size: str = typer.Option(None), usd: str = typer.Option(None),
                 dry_run: bool = typer.Option(False, "--dry-run"),
                 yes: bool = typer.Option(False, "--yes")) -> None:
    """Place a limit order."""
    pub = public(ctx)
    target, plan = trade.build_plan(side=normalize_side(side), market_order=False, token_id=token,
                                    slug=slug, url=url, outcome=outcome, usd=usd, size=size, price=price, pub=pub)
    raise typer.Exit(trade.run(ctx, pub=pub, secure_factory=lambda: secure(ctx),
                               target=target, plan=plan, dry_run=dry_run, yes=yes))


@app.command()
def orders(ctx: typer.Context, market: str = typer.Option(None)) -> None:
    """List your open orders."""
    page = secure(ctx).list_open_orders(market=market) if market else secure(ctx).list_open_orders()
    emit(ctx.obj.output, collect(page))
```

Remaining commands (same thin pattern; exact SDK calls — implement each as its own `@app.command`):
- `market-order` (`--token/--slug/--url`,`--outcome`,`--side`,`--usd`/`--size`,`--max-spend`,`--order-type`,`--dry-run`,`--yes`) → `trade.build_plan(market_order=True, ...)` + `trade.run`
- `post-orders` (`--tokens`,`--side`,`--prices`,`--sizes`) → loop `orders.build_signed_limit_order` then `secure.post_orders([...])`; `emit` `[describe_response(r) for r]`
- `cancel ORDER_ID` → `emit(fmt, secure.cancel_order(order_id=order_id))`
- `cancel-orders IDS` (comma-separated) → `secure.cancel_orders(order_ids=ids.split(","))`
- `cancel-market --market 0x...` → `secure.cancel_market_orders(market=market)`
- `cancel-all` → confirm typed-YES then `secure.cancel_all()`
- `order ORDER_ID` → `emit(fmt, secure.get_order(order_id=order_id))`
- `trades` → `emit(fmt, collect(secure.list_account_trades()))`
- `balance --asset-type collateral|conditional [--token]` → `emit(fmt, secure.get_balance_allowance(asset_type=asset_type.upper(), token_id=token))`
- `update-balance --asset-type ... [--token]` → re-query `get_balance_allowance` and emit (the SDK refreshes balances internally on order placement; this is a manual re-read)

- [ ] **Step 4: Mount in cli.py**

```python
from .groups import clob_trade
app.add_typer(clob_trade.app, name="clob")
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run pytest tests/test_clob_trade.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add poly/groups/clob_trade.py poly/cli.py tests/test_clob_trade.py
git commit -m "feat: clob trading + account-read commands"
```

---

### Task 10: buy/sell aliases

**Files:**
- Modify: `poly/cli.py` (replace the `buy` stub; add `sell`)
- Test: `tests/test_buy_sell.py`

**Interfaces:**
- Consumes: `trade.build_plan`, `trade.run`, `context.public`, `context.secure`.
- Produces: top-level `poly buy` / `poly sell` with `--token-id/--slug/--url`, `--outcome`, `--usd/--size`, `--price`, `--market`, `--max-spend`, `--dry-run`, `--yes`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_buy_sell.py
from decimal import Decimal
from types import SimpleNamespace
from typer.testing import CliRunner
from poly.cli import app
from poly import context

runner = CliRunner()


class FakePub:
    def list_markets(self, clob_token_ids=None): return SimpleNamespace(first_page=lambda: SimpleNamespace(items=[]))
    def get_price(self, token_id=None, side=None): return Decimal("0.5")


class FakeSecure:
    wallet = "0xW"
    def create_limit_order(self, **k):
        return SimpleNamespace(maker="0xW", signer="0xW", token_id=k["token_id"], side=k["side"],
                               maker_amount="1", taker_amount="2", order_type="GTC")


def test_buy_dry_run(monkeypatch):
    monkeypatch.setattr(context, "secure", lambda ctx: FakeSecure())
    monkeypatch.setattr(context, "public", lambda ctx: FakePub())
    result = runner.invoke(app, ["buy", "--token-id", "111", "--size", "5", "--price", "0.5", "--dry-run"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_buy_sell.py -v`
Expected: FAIL (stub buy ignores args).

- [ ] **Step 3: Implement buy/sell in cli.py**

```python
def _trade_alias(ctx, side, *, token_id, slug, url, outcome, usd, size, price, market, max_spend, dry_run, yes):
    from .context import public, secure
    from . import trade
    pub = public(ctx)
    target, plan = trade.build_plan(side=side, market_order=market, token_id=token_id, slug=slug,
                                    url=url, outcome=outcome, usd=usd, size=size, price=price,
                                    max_spend=max_spend, pub=pub)
    raise typer.Exit(trade.run(ctx, pub=pub, secure_factory=lambda: secure(ctx),
                               target=target, plan=plan, dry_run=dry_run, yes=yes))


@app.command()
def buy(ctx: typer.Context,
        token_id: str = typer.Option(None, "--token-id", "--token"),
        slug: str = typer.Option(None), url: str = typer.Option(None),
        outcome: str = typer.Option("yes"), usd: str = typer.Option(None), size: str = typer.Option(None),
        price: str = typer.Option(None), market: bool = typer.Option(False, "--market"),
        max_spend: str = typer.Option(None, "--max-spend"),
        dry_run: bool = typer.Option(False, "--dry-run"), yes: bool = typer.Option(False, "--yes")) -> None:
    """Buy an outcome (friendly alias for clob create-order/market-order)."""
    _trade_alias(ctx, "BUY", token_id=token_id, slug=slug, url=url, outcome=outcome, usd=usd,
                 size=size, price=price, market=market, max_spend=max_spend, dry_run=dry_run, yes=yes)


@app.command()
def sell(ctx: typer.Context,
         token_id: str = typer.Option(None, "--token-id", "--token"),
         slug: str = typer.Option(None), url: str = typer.Option(None),
         outcome: str = typer.Option("yes"), usd: str = typer.Option(None), size: str = typer.Option(None),
         price: str = typer.Option(None), market: bool = typer.Option(False, "--market"),
         max_spend: str = typer.Option(None, "--max-spend"),
         dry_run: bool = typer.Option(False, "--dry-run"), yes: bool = typer.Option(False, "--yes")) -> None:
    """Sell an outcome."""
    _trade_alias(ctx, "SELL", token_id=token_id, slug=slug, url=url, outcome=outcome, usd=usd,
                 size=size, price=price, market=market, max_spend=max_spend, dry_run=dry_run, yes=yes)
```

(Remove the Task 1 `buy` stub.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_buy_sell.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poly/cli.py tests/test_buy_sell.py
git commit -m "feat: top-level buy/sell aliases over the trade flow"
```

---

### Task 11: data positions + value

**Files:**
- Create: `poly/groups/data.py`
- Modify: `poly/cli.py` (mount `data`)
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: `context.public`, `output.emit`, `pagination.collect`, `config.load_settings`, `eth_account.Account`.
- Produces: `poly.groups.data.app` with `positions [ADDRESS]` and `value [ADDRESS]` (address defaults to the configured wallet).

- [ ] **Step 1: Write failing test**

```python
# tests/test_data.py
from types import SimpleNamespace
from typer.testing import CliRunner
from poly.cli import app
from poly import context

runner = CliRunner()


class FakePub:
    def list_positions(self, user=None, page_size=20):
        return SimpleNamespace(first_page=lambda: SimpleNamespace(items=[{"outcome": "Yes", "size": "5"}], has_next=False))


def test_positions_json(monkeypatch):
    monkeypatch.setattr(context, "public", lambda ctx: FakePub())
    result = runner.invoke(app, ["-o", "json", "data", "positions", "0xWALLET"])
    assert result.exit_code == 0 and "Yes" in result.output
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_data.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement data.py**

```python
# poly/groups/data.py
import typer
from eth_account import Account

from ..context import public
from ..output import emit
from ..pagination import collect
from ..config import load_settings

app = typer.Typer(no_args_is_help=True, help="On-chain portfolio data.")


def _resolve_user(ctx, address):
    if address:
        return address
    pk = getattr(ctx.obj, "private_key", None)
    return Account.from_key(load_settings(private_key=pk).private_key).address


@app.command()
def positions(ctx: typer.Context, address: str = typer.Argument(None), limit: int = 20) -> None:
    """List positions for ADDRESS (default: your wallet)."""
    user = _resolve_user(ctx, address)
    emit(ctx.obj.output, collect(public(ctx).list_positions(user=user, page_size=limit)))


@app.command()
def value(ctx: typer.Context, address: str = typer.Argument(None)) -> None:
    """Portfolio value for ADDRESS (default: your wallet)."""
    user = _resolve_user(ctx, address)
    emit(ctx.obj.output, public(ctx).get_portfolio_values(user=user))
```

- [ ] **Step 4: Mount in cli.py + run**

```python
from .groups import data
app.add_typer(data.app, name="data")
```
Run: `uv run pytest tests/test_data.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add poly/groups/data.py poly/cli.py tests/test_data.py
git commit -m "feat: data positions/value commands"
```

---

### Task 12: Cleanup, full suite, dry-run smoke, README

**Files:**
- Delete: `tests/test_cli.py` (buy/sell-era argparse tests)
- Modify: `README.md`
- Test: whole suite

- [ ] **Step 1: Confirm safety coverage moved, then remove obsolete test**

Verify `tests/test_trade.py` + `tests/test_buy_sell.py` + `tests/test_clob_trade.py` cover every safety case the old `tests/test_cli.py` did (dry-run-no-post, side-aware market mapping, confirm gating, approvals retry, max_spend default, tick rounding to 0/1). Then:
```bash
git rm tests/test_cli.py
```

- [ ] **Step 2: Run the full suite with coverage**

Run: `uv run pytest -q --cov=poly --cov-report=term-missing`
Expected: all pass; ≥80% on `config`, `output`, `orders`, `market`, `trade`, `pagination`.

- [ ] **Step 3: Live dry-run smoke (author runs --dry-run / reads ONLY)**

Run:
```bash
uv run poly --help
uv run poly -o json data positions 0x93772c4c6332901F9F5e6c3F179D623b07D7BbB7
uv run poly buy --token-id 47236739815607347436394828740644657912816815268002585518946427187074399713739 --usd 1 --price 0.52 --dry-run
```
Expected: help lists `buy`/`sell`/`clob`/`data`/`wallet`/`setup`; positions returns JSON; the dry-run prints preview + signed identity and does NOT submit. **Do not run a real order — hand that to the user.**

- [ ] **Step 4: Update README**

Document the `poly <group> <verb>` structure, global `-o json`, the config-file/wallet model (`poly setup`, `poly wallet import`, `.env` no longer read), and keep the safety + deposit-wallet notes.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: drop obsolete argparse tests; update README for the new CLI"
```

---

## Self-Review notes

- **Spec coverage (P0+P1):** config-file wallet ✅ (Tasks 2,6,7), `-o json` + error envelope ✅ (Tasks 3,4), Typer groups ✅ (Tasks 1,4), trading parity create/market/post/cancel*/orders/order/trades/balance ✅ (Task 9), buy/sell aliases ✅ (Task 10), data positions/value ✅ (Task 11), `.env` removal ✅ (Tasks 1,2). Deferred to P2–P5 (separate plan): markets/events/tags/series/comments/profiles/sports, clob reads (price/book/history/etc.), rewards/api/notifications, approve/ctf, shell.
- **Type consistency:** `OrderPlan` fields defined in Task 8 are exactly what `build_plan`/`run` produce/consume and what `clob_trade`/`buy`/`sell` pass through. `emit(fmt, data)` and `print_error(fmt, msg)` signatures are consistent across all tasks (always `ctx.obj.output`). `collect(paginator, limit=None, all_=False)` consistent. `load_settings(*, private_key=..., signature_type=..., path=...)` consistent across config/context/wallet/data.
- **Porting note:** Task 8 reuses the *exact* safety logic from the prior `poly/cli.py` (commit `2979939`) — already adversarially reviewed and dry-run-verified — changing only `print` → dict+`emit`. Do not re-derive it.
- **Placeholder scan:** the only "fill bodies by porting" reference (Task 8 Step 3) points to a specific known-good source file/commit and lists every rule to preserve; the thin-command list in Task 9 gives each command's exact SDK call. No vague TODOs.
