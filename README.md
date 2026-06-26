# poly

A friendly command-line tool for trading on **Polymarket** from your deposit wallet —
place buy/sell orders, check balances, and view positions, with human tables or `--output json`
for scripts and agents.

Built on the official **`polymarket-client`** SDK (`Polymarket/py-sdk`), which trades from the
deterministic deposit wallet (signature type 3 / POLY_1271) — the path the legacy
`py-clob-client-v2` could not. See [`docs/superpowers/specs/`](docs/superpowers/specs/) for the
full design.

---

## Install

```bash
uv sync --extra dev
```

> Examples below use `poly`. If it isn't on your `PATH`, prefix with `uv run` (e.g. `uv run poly buy …`).

## Set your key (once)

```bash
poly setup                      # interactive — paste your key when prompted
# or
poly wallet import 0x<your-signer-key>
```

Your key is stored in `~/.config/polymarket/config.json` (file mode `600`). The deposit wallet is
derived from it automatically. A project `.env` file is **not** read.

**Key resolution order:** `--private-key` flag → `POLYMARKET_PRIVATE_KEY` env → config file.

## Quick start

```bash
# 1. Always dry-run first — builds and signs locally, prints the order, and never submits.
poly buy --slug <market-slug> --outcome yes --usd 1 --price 0.50 --dry-run

# 2. Place it for real — shows a preview and asks you to type YES.
poly buy --slug <market-slug> --outcome yes --usd 1 --price 0.50

# 3. Check what you hold.
poly data positions
poly clob balance --asset-type collateral
```

---

## Commands

| Command | What it does |
|---|---|
| `poly buy` / `poly sell` | Place an order (friendly aliases for `clob create-order` / `market-order`) |
| `poly setup` | Configure your signer key |
| **`poly clob`** | Trading + account reads (see below) |
| **`poly data`** | `positions [ADDRESS]`, `value [ADDRESS]` (defaults to your wallet) |
| **`poly wallet`** | `create`, `import`, `show`, `address`, `reset` |

**`poly clob` subcommands**

| Command | What it does |
|---|---|
| `create-order` / `market-order` | Place a limit or market order |
| `post-orders` | Post multiple limit orders at once |
| `orders` / `order ID` | List open orders / show one |
| `trades` | List your account trades |
| `balance` | Show balance + allowance for an asset type |
| `cancel ID` / `cancel-orders IDS` | Cancel one / several orders |
| `cancel-market --market 0x…` | Cancel all orders in a market |
| `cancel-all` | Cancel everything (asks you to type YES) |

**Global options** (before the command): `-o, --output table|json` (default `table`), `--private-key`.

---

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

## Examples

```bash
# Buy $2 of YES by token id, dry-run first.
poly buy --token-id 1234567… --usd 2 --price 0.65 --dry-run

# Sell 10 shares of NO at a 0.40 limit, no prompt.
poly sell --slug <market-slug> --outcome no --size 10 --price 0.40 --yes

# Market BUY $2 (spend capped so fees can't exceed it).
poly buy --url https://polymarket.com/event/… --outcome yes --usd 2 --market

# JSON output for scripts/agents.
poly -o json clob orders
poly -o json data positions 0x93772c4c6332901F9F5e6c3F179D623b07D7BbB7 | jq '.[].cash_pnl'
```

---

## Safety

- Every real submit shows a **preview** and requires typing **`YES`** (skip with `--yes`).
- Use **`--dry-run`** to see exactly what would be sent, signed but never submitted.
- Prices/sizes are validated (price strictly 0–1, size positive), rounded to the market tick, and
  sent to the SDK as **strings, never floats** — avoiding the precision bugs of the legacy client.

## Trading from a non-default wallet

The deposit wallet is derived from your key automatically. To trade from a different existing wallet
(deposit / Safe / proxy / EOA), add `"wallet_address": "0x…"` to `~/.config/polymarket/config.json`.

## Notes

- **Not** built on `py-clob-client-v2` (Python) or `rs-clob-client-v2` (the Rust CLI) — both are
  outdated trade paths that can't post from deposit wallets.
- `--signature-type` and `clob update-balance` are intentionally absent: the SDK supports only the
  deposit-wallet derivation, and has no distinct "refresh balance" call.
- Requires Python ≥3.11; run via `uv`. Tests are offline: `uv run pytest`.
