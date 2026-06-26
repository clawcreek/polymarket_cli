# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`poly` is a CLI for placing buy/sell orders on Polymarket from a deposit wallet, built on Typer. Source lives in
`poly/` (~1 100 lines across 10 modules + 4 group sub-apps); mirror test modules live in `tests/`.

## Commands

Use **`uv`** for everything. The project requires **Python ≥3.11**, but the system `python3` here is 3.9 —
running `python3 -m pytest` or `pytest` directly will fail. `uv run` selects the right interpreter and venv.

```bash
uv sync --extra dev          # create .venv + install deps (SDK is a prerelease; allowed via [tool.uv])
# Key setup (pick one):
poly setup                   # interactive wizard — writes ~/.config/polymarket/config.json
poly wallet import 0x<key>   # non-interactively import a key
# Or: export POLYMARKET_PRIVATE_KEY=0x...

uv run poly buy --slug <market-slug> --outcome yes --usd 1 --price 0.50 --dry-run   # never submits
uv run pytest                # all tests (testpaths = ["tests"])
uv run pytest tests/test_orders.py::test_decimal_str_strips_trailing_zeros   # single test
uv run pytest --cov=poly --cov-report=term-missing                           # coverage
```

There is **no configured linter/formatter** in `pyproject.toml` (no ruff/black config). Don't invent a lint
command; tests are the gate.

## The core invariant: Decimal in, strings out

This project exists to sidestep bugs in the legacy `py-clob-client-v2`. The single most important rule,
which every numeric path depends on:

> **All prices/sizes are computed with `decimal.Decimal` and handed to the SDK as strings — never floats.**

Passing floats let the legacy client corrupt clean prices (`0.51` → `0.5100011…`) and the CLOB rejected them
(issues #59/#66/#68). `poly/orders.py` is the chokepoint: `to_decimal()` parses via `str()` (never `float()`),
and `build_signed_*` / `_market_kwargs` emit `decimal_str(...)`. **Any new numeric code must keep values as
`Decimal` and serialize with `decimal_str` before the SDK boundary.** Breaking this reintroduces the exact bug
the tool was built to avoid.

## SDK and wallet model

- Built on the **official unified `polymarket-client` SDK** (`Polymarket/py-sdk`, prerelease `0.1.0b9`),
  imported as `from polymarket import ...` — **not** the legacy `py-clob-client-v2`.
- `PublicClient()` for unauthenticated market reads; `SecureClient.create(private_key=, wallet=)`
  for signing/submitting. Both are imported **lazily** inside `poly/config.py` so pure-logic tests don't pay
  the SDK import cost.
- Auth is a single signer private key. The SDK **derives the deposit wallet** (signature type 3 / POLY_1271)
  deterministically from it — this type-3 path is what the legacy client could not post.
- Key resolution order: `--private-key` flag → `POLYMARKET_PRIVATE_KEY` env → `~/.config/polymarket/config.json`.
  The project `.env` file is **not read**.

## Architecture (data flow per command)

The root Typer app in `poly/cli.py` delegates to group sub-apps and the top-level `buy`/`sell` aliases.
The trading flow is:

**resolve target (`market.py`) → build plan (`trade.build_plan`) → preview + sign → confirm → submit (`trade.run`)**

| File | Responsibility |
|---|---|
| `poly/cli.py` | Root Typer `app`; `@app.callback()` stores `CliContext` on `ctx.obj`; `buy`/`sell` top-level aliases call `trade.build_plan` + `trade.run`. Mounts group sub-apps via `app.add_typer`. |
| `poly/context.py` | `CliContext` dataclass; `context.public(ctx)` / `context.secure(ctx)` injection seams — these are the only places clients are constructed and the points tests replace with fakes. |
| `poly/trade.py` | `trade.build_plan(...)` is the pure orchestrator: resolves target, builds an `OrderPlan`. `trade.run(ctx, ...)` emits preview, calls `_build_signed`, then either emits dry-run output or confirms + submits. `_build_signed` is the single place orders are constructed (shared by dry-run and live paths). `InsufficientAllowanceError` triggers a one-time user-confirmed approvals retry inside `_submit`. |
| `poly/market.py` | `resolve_target()` maps `--token-id` / `--slug` / `--url` (+ `--outcome`) to a frozen `ResolvedTarget` via the public Gamma API. Best-effort: failures return `None`/partial data and never block direct `--token-id` trading. `live_price()` likewise swallows errors. |
| `poly/orders.py` | Validation, sizing, tick rounding, and Decimal→string serialization. `build_signed_limit_order` / `build_signed_market_order` **sign without posting**; `post_signed_order` submits separately. Enforces SDK market-order semantics: BUY takes `amount` (USD), SELL takes `shares`. |
| `poly/config.py` | `Settings` frozen dataclass (`private_key` marked `field(repr=False)` so tracebacks never leak it). `load_settings()` implements the key resolution order above. `build_public_client()` / `build_secure_client()`. |
| `poly/output.py` | `emit(fmt, data)` — the only printing point; supports `table` and `json` output modes. |
| `poly/groups/clob_trade.py` | `clob` Typer sub-app: `create-order`, `market-order` (detailed CLOB commands). |
| `poly/groups/wallet.py` | `wallet` sub-app: `show`, `import`, `balance`. |
| `poly/groups/data.py` | `data` sub-app: `positions`, `value`. |
| `poly/groups/setup.py` | `setup` command: interactive wizard that writes `~/.config/polymarket/config.json`. |

### Design points to preserve when editing

- **Don't duplicate signing logic.** `trade._build_signed` is the only place orders are constructed; dry-run
  and live submission both call it. Keep build and submit separate.
- **Keep secrets out of `repr`.** When adding a secret to `Settings`, mark it `field(repr=False)`.
- **Thin command bodies.** Each Typer command resolves a client via `context.public`/`context.secure`, calls
  one SDK or trade function, and emits output — no business logic inline.

## Testing approach

Tests are **network-free** and never touch the real SDK client. `context.public(ctx)` and `context.secure(ctx)`
are the injection seams: tests set `ctx.obj = CliContext(...)` and patch or replace `_public`/`_secure` directly.
`FakePub` and `SimpleNamespace` stand-ins replace SDK clients. Note the real `SignedOrder` has **no `price`/`size`
attributes** — it uses `maker_amount`/`taker_amount`; test fakes mirror that shape.

## Reference

Full design rationale and the list of legacy-SDK bugs this tool sidesteps:
[`docs/superpowers/specs/2026-06-25-poly-cli-design.md`](docs/superpowers/specs/2026-06-25-poly-cli-design.md).
This is a git repo (`main`); commit here, not at the `Side_Projects/` workspace root.
