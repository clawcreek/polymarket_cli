<p align="center">
  <img src="assets/banner.svg" alt="poly — trade Polymarket from your terminal" width="100%">
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="managed with uv" src="https://img.shields.io/badge/managed%20with-uv-7c3aed?logo=uv&logoColor=white">
  <img alt="built on polymarket-client" src="https://img.shields.io/badge/built%20on-polymarket--client-6366f1">
  <img alt="status beta" src="https://img.shields.io/badge/status-beta-f59e0b">
  <img alt="tests 114 passing" src="https://img.shields.io/badge/tests-114%20passing-22c55e">
  <img alt="license MIT" src="https://img.shields.io/badge/license-MIT-blue">
</p>

<p align="center">
  A friendly CLI for trading on <b>Polymarket</b> from your API wallet — set up a wallet,
  search markets by keyword, and place orders, as readable tables or <code>-o json</code> for scripts and agents.
</p>

<p align="center">
  Built on the official <b><code>polymarket-client</code></b> SDK (<code>Polymarket/py-sdk</code>), trading from your
  deterministic <b>API wallet</b> — the SDK's signature-type-3 deposit wallet, which Polymarket's website labels
  "for API use only — do not send funds".
</p>

---

## Install

**Prerequisite:** [`uv`](https://docs.astral.sh/uv/) — it manages Python for you, so you don't need a
separate Python install.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell): irm https://astral.sh/uv/install.ps1 | iex
```

### Option A — install as a global command (recommended)

Install straight from GitHub — no clone needed — and get a `poly` command available everywhere:

```bash
uv tool install git+https://github.com/NetMindAI-Open/polymarket_cli.git
```

Or from a local clone:

```bash
git clone https://github.com/NetMindAI-Open/polymarket_cli.git
cd polymarket_cli
uv tool install .          # add --editable to track your local source changes live
```

Verify, then jump to **The flow** below:

```bash
poly --help
```

> - If your shell can't find `poly` afterward, run `uv tool update-shell` and restart the terminal
>   (uv installs commands into `~/.local/bin`).
> - If install complains about a prerelease dependency, append `--prerelease allow`.
> - Update later with `uv tool upgrade poly-cli`; remove with `uv tool uninstall poly-cli`.

### Option B — run from source (for development)

```bash
git clone https://github.com/NetMindAI-Open/polymarket_cli.git
cd polymarket_cli
uv sync --extra dev        # creates .venv with dev deps
uv run poly --help
```

> With Option B there's no global `poly` command — prefix every example below with `uv run`
> (e.g. `uv run poly markets search "world cup"`).

---

## The flow

A full session goes: **① set up your wallet → ② check your account → ③ find a market → ④ trade.**

```mermaid
flowchart LR
    A["① Set up<br/>wallet import"] --> B["② Check<br/>balance · positions"]
    B --> C["③ Find<br/>markets search"]
    C --> D["④ Trade<br/>buy · sell"]
    classDef step fill:#0b1020,stroke:#34d399,stroke-width:1.5px,color:#e2e8f0,rx:8,ry:8;
    class A,B,C,D step;
```

### ① Set up your wallet

Store your signer key once (your API wallet is derived from it automatically):

```bash
poly setup                              # interactive — paste your key when prompted
poly wallet import 0x<your-signer-key>  # or non-interactive
```

The key is saved to `~/.config/polymarket/config.json` (mode `600`). A project `.env` is **not** read.
Resolution order: `--private-key` flag → `POLYMARKET_PRIVATE_KEY` env → config file.
(See [`config.example.json`](config.example.json) for the format.)

```bash
poly wallet show        # your signer EOA + api_wallet address + config path (never prints the key)
poly wallet address     # just the api_wallet address
```

> **`api_wallet`** is your Polymarket account — it holds your funds and trades. It's the address the website
> labels *"for API use only — do not send funds."* Add money through the website's **Deposit** flow, **not** by
> sending crypto to `api_wallet` directly.

### ② Check your account

```bash
poly clob balance --asset-type collateral     # your USDC cash
poly data positions                           # what you hold (defaults to your api_wallet)
poly data value                               # portfolio value
```

### ③ Find a market

Search by keyword — the table shows each market's **slug** (which you trade with);
full **token ids** and `condition_id` are available with `-o json`:

```bash
poly markets search "world cup"               # keyword search
poly markets get will-usa-win-the-world-cup   # one market by slug / id / URL
poly markets list --limit 20                  # browse open markets
```

Example (`poly markets search "world cup"`):
```
question                                       yes_price  slug
Will Portugal reach the Round of 16 ...        0.745      will-portugal-reach-the-round-of-16-...
Will Mexico reach the Round of 16 ...          0.62       will-mexico-reach-the-round-of-16-...
```
(Tables show a readable subset of columns; `-o json` returns every field.)

### ④ Trade

Always **dry-run first** — it builds and signs the order locally and prints it, but never submits:

```bash
poly buy --slug will-portugal-reach-the-round-of-16 --outcome yes --usd 1 --price 0.75 --dry-run
```

Then place it for real (shows a preview and asks you to type `YES`):

```bash
poly buy  --slug <slug> --outcome yes --usd 1 --price 0.75      # buy $1 of YES at a 0.75 limit
poly sell --slug <slug> --outcome no  --size 10 --price 0.40    # sell 10 NO shares at 0.40
poly buy  --token-id 4636895... --usd 2 --market               # market buy $2 by token id
```

Manage your orders afterward:

```bash
poly clob orders          # open orders
poly clob trades          # your trade history
poly clob cancel <ID>     # cancel one; or `poly clob cancel-all`
```

---

## All commands

Global options (before the command): `-o, --output table|json` (default `table`), `--private-key`.

| Group | Commands |
|---|---|
| *(top level)* | `setup`, `buy`, `sell`, `approve`, `redeem`, `report` |
| `wallet` | `create`, `import`, `show`, `address`, `reset` |
| `markets` | `search`, `get`, `list` |
| `clob` | `create-order`, `market-order`, `post-orders`, `orders`, `order`, `trades`, `balance`, `cancel`, `cancel-orders`, `cancel-market`, `cancel-all` |
| `data` | `positions`, `value` |

`approve` sets the one-time gasless trading approvals; `redeem` collects your winnings from a resolved
market; `report` fetches a precomputed market report when one is configured.

### Collecting winnings

A resolved market doesn't pay out by itself. `poly data positions` marks claimable rows with
**`redeemable`** — redeem them without going back to the website:

```bash
poly -o json data positions                       # look for redeemable: true
poly -o json redeem --slug <market-slug>          # or --condition-id <id>
```

Gasless when a Relayer key is configured; otherwise it broadcasts from your signer and needs MATIC.

`poly buy`/`sell` are friendly aliases for `clob create-order` / `market-order`. Run `poly <group> --help`
or `poly <group> <command> --help` for full options.

## Order options

For `buy`, `sell`, `clob create-order`, and `clob market-order`:

| Flag | Meaning |
|---|---|
| `--token-id` · `--slug` · `--url` | What to trade — pick one. With `--slug`/`--url`, add `--outcome`. |
| `--outcome yes\|no` | Which side of the market (default `yes`). Ignored with `--token-id`. |
| `--usd` · `--size` | Spend N dollars **or** trade N shares. (Limit: either. Market BUY needs `--usd`; market SELL needs `--size`.) |
| `--price` | Limit price per share, between 0 and 1. Required unless `--market`. Rounded to the market tick. |
| `--market` | Market order instead of a limit order. |
| `--max-spend` | Market BUY only: fee-inclusive USD cap (defaults to `--usd`, so you never overspend). |
| `--dry-run` | Build and sign locally, print details, **do not submit**. |
| `--yes` | Skip the typed-`YES` confirmation. |

## JSON output

Add `-o json` before any command for machine-readable output:

```bash
poly -o json markets search "election" | jq '.[].slug'
poly -o json data positions 0x9377... | jq '.[].cash_pnl'
```

## Safety

- Every real submit shows a **preview** and requires typing **`YES`** (skip with `--yes`).
- Use **`--dry-run`** to see exactly what would be sent — signed locally, never submitted.
- Prices/sizes are validated and sent to the SDK as **strings, never floats** (avoiding the legacy
  client's precision bugs). Market BUY is capped by `--max-spend` so fees can't overspend.

## Config & non-default wallet

`~/.config/polymarket/config.json` holds your key (and, optionally, a wallet to trade from):

```json
{
  "private_key": "0x...",
  "wallet_address": "0x..."   // optional — omit to use the default api_wallet
}
```

## Notes

- **Not** built on `py-clob-client-v2` (Python) or `rs-clob-client-v2` (the Rust CLI) — both are
  outdated trade paths that can't post from deposit wallets.
- `--signature-type` and `clob update-balance` are intentionally absent: the SDK supports only the
  deposit-wallet derivation and has no distinct "refresh balance" call.
- Requires Python ≥3.11; run via `uv`. Tests are offline: `uv run pytest`.
