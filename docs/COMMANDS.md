# `poly` CLI — Command Reference

Agent-facing overview of **every** command the `poly` CLI supports, grouped by topic, with all
options. Source of truth: `poly/cli.py` (root + `buy`/`sell`) and `poly/groups/{wallet,setup,markets,clob_trade,data}.py`.

> **Always invoke via `uv run`** (the project needs Python ≥3.11; bare `poly` is only on PATH if you
> activated the venv or ran `uv tool install`). Examples below omit `uv run` for brevity — prepend it.

## Command map

| Topic | Commands |
|---|---|
| Setup & wallet | `setup` · `wallet create` · `wallet import` · `wallet show` · `wallet address` · `wallet reset` |
| Market discovery (read-only) | `markets search` · `markets get` · `markets list` |
| Trading | `buy` · `sell` · `clob create-order` · `clob market-order` · `clob post-orders` |
| Order management | `clob orders` · `clob order` · `clob cancel` · `clob cancel-orders` · `clob cancel-market` · `clob cancel-all` |
| Account & balances | `clob balance` · `clob trades` · `data positions` · `data value` |

---

## 0. Global options & conventions

Put global options **before** the command: `poly [GLOBAL] COMMAND [ARGS]`.

| Option | Default | Meaning |
|---|---|---|
| `-o`, `--output [table\|json]` | `table` | `table` = curated columns; `json` = every field (token ids, condition ids, …). Use `json` when scripting. |
| `--private-key TEXT` | — | Override the signer key for this run only. |

**Key resolution order:** `--private-key` flag → `POLYMARKET_PRIVATE_KEY` env → `~/.config/polymarket/config.json`. The project `.env` is **not** read.

**Two wallet addresses** (see §6 gotchas): the **signer EOA** (your private key's raw address, signs orders) vs the **api_wallet** (SDK-derived account that holds USDC + positions and that orders execute from; what polymarket.com labels "Address — for API use only, do not send funds"). You do **not** send funds to `api_wallet` directly — deposit via the website.

> **⚠️ `api_wallet` is a *derived guess* and can be the WRONG address.** One private key deterministically derives several distinct on-chain addresses — the EOA plus up to four contract wallets (POLY_PROXY / GNOSIS_SAFE / **two** variants of the type-3 deposit wallet: `uups` and `beacon`). With no `wallet_address` set, the SDK computes the deposit wallet from the *current* factory (`derive_current_deposit_wallet_address_sync`), which is **not guaranteed** to be the wallet your account actually deployed and funded. In a real account we saw `api_wallet` resolve to the `beacon` variant (0 USDC) while the funds — and the account the CLOB/website recognize — lived on the `uups` variant. **Never deposit to a CLI-printed address; deposit only via polymarket.com. Verify `wallet show`'s `api_wallet` equals the deposit address on polymarket.com/settings, and if it differs, pin the correct one via `"wallet_address": "0x…"` in `config.json`** (there is no `--wallet` flag).

```bash
poly -o json data value          # global option before the command
```

---

## 1. Setup & wallet

Local signer-key management, backed by `~/.config/polymarket/config.json` (written `chmod 600`). Keys are never printed.

### `setup`
Configure your signer key (first-time setup).

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--private-key TEXT` | optional | — | The key to store. If omitted, you're prompted (hidden input). |

```bash
poly setup                                  # prompts for the key (hidden)
poly setup --private-key 0xABC...           # non-interactive
```

### `wallet create`
Generate a brand-new random wallet and save it.

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--force` | optional | `false` | Overwrite an existing saved key. Without it, refuses if a key exists. |

### `wallet import <PRIVATE_KEY>`
Import an existing private key. `<PRIVATE_KEY>` is a required argument.

```bash
poly wallet import 0xABC...
```

### `wallet show`
Print your **signer EOA** and **api_wallet** (+ a do-not-send-funds note) + config path (never the key).

### `wallet address`
Print only your **api_wallet** address (the account that holds funds; do not send funds to it directly).

### `wallet reset`
Delete the saved config (`config.json`).

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--force` | optional | `false` | Required to actually delete; without it, refuses. |

---

## 2. Market discovery (`markets`)

Public, **read-only** — uses the unauthenticated client, **no key needed**. Returns columns `question`, `yes_price`, `slug` in table mode; `-o json` also returns `condition_id`, `yes_token_id`, `no_token_id`.

### `markets search <QUERY>`
Find markets by keyword (searches events and their markets).

| Option / Arg | Req | Default | Meaning |
|---|---|---|---|
| `<QUERY>` | **required** | — | Keyword, e.g. `"world cup"`. |
| `--limit INT` | optional | `20` | Max results. |

```bash
poly markets search "world cup"
poly markets search uruguay --limit 25
```

### `markets get <REF>`
Show a single market by **id, slug, or URL** (auto-detected: `http…` → URL, all-digits → id, else slug).

```bash
poly markets get fifwc-ury-esp-2026-06-26-usa
poly markets get https://polymarket.com/event/...
```

### `markets list`
List markets (open by default).

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--limit INT` | optional | `20` | Max results. |
| `--closed` / `--active` | optional | `--active` | `--closed` lists resolved markets; `--active` lists open ones. |

```bash
poly markets list --limit 30
poly markets list --closed
```

> **Event slug ≠ market slug.** A match like `fifwc-tur-usa-2026-06-25` is an *event* with several *markets* (`…-usa`, `…-tur`, `…-draw`). Trading commands need the **market** slug. Use `markets search`/`markets get` to find it.

---

## 3. Trading

`buy` / `sell` are friendly aliases: without `--market` they place a **limit** order (= `clob create-order`); with `--market` a **market** order (= `clob market-order`). Both build, sign locally, preview, then (unless `--dry-run`) ask for a typed `YES` (skip with `--yes`).

### `buy` / `sell`
Identical options for both.

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--token-id`, `--token TEXT` | one target* | — | Trade a CLOB token id directly. |
| `--slug TEXT` | one target* | — | Market slug (+ `--outcome` to pick a side). |
| `--url TEXT` | one target* | — | Market URL (+ `--outcome`). |
| `--outcome TEXT` | optional | `yes` | `yes` or `no`. Ignored with `--token-id`. |
| `--usd TEXT` | size** | — | Limit: size = usd ÷ price (floored). Market **BUY**: USD to spend. |
| `--size TEXT` | size** | — | Shares. Limit, or market **SELL**. |
| `--price TEXT` | limit only | — | Limit price per share, strictly 0–1. Rounded to the market tick. Required for limit orders. |
| `--market` | optional | `false` | Market order instead of limit. |
| `--max-spend TEXT` | optional | = `--usd` | Market BUY only: fee-inclusive USD cap. |
| `--dry-run` | optional | `false` | Build + sign + preview only; **never submits**. |
| `--yes` | optional | `false` | Skip the typed-`YES` confirmation. |

\* Exactly one of `--token-id` / `--slug` / `--url`.  \*\* Limit: `--usd` or `--size`. Market: **BUY needs `--usd`**, **SELL needs `--size`**.

```bash
poly buy  --slug fifwc-ury-esp-2026-06-26-usa --outcome yes --usd 1 --price 0.5 --dry-run
poly sell --token-id 472367... --size 10 --price 0.4 --yes
poly buy  --url https://polymarket.com/event/... --outcome yes --usd 2 --market
```

### `clob create-order`
Explicit **limit** order (what `buy`/`sell` wrap without `--market`).

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--token`, `--token-id TEXT` | one target* | — | Token id. |
| `--slug TEXT` | one target* | — | Market slug. |
| `--url TEXT` | one target* | — | Market URL. |
| `--outcome TEXT` | optional | `yes` | `yes` / `no`. |
| `--side TEXT` | **required** | — | `BUY` or `SELL`. |
| `--price TEXT` | **required** | — | Limit price, 0–1. |
| `--size TEXT` | size** | — | Shares. |
| `--usd TEXT` | size** | — | USD (size = usd ÷ price). |
| `--dry-run` | optional | `false` | Preview only. |
| `--yes` | optional | `false` | Skip confirmation. |

### `clob market-order`
Explicit **market** order (FAK/FOK).

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--token`, `--token-id TEXT` | one target* | — | Token id. |
| `--slug TEXT` | one target* | — | Market slug. |
| `--url TEXT` | one target* | — | Market URL. |
| `--outcome TEXT` | optional | `yes` | `yes` / `no`. |
| `--side TEXT` | **required** | — | `BUY` or `SELL`. |
| `--usd TEXT` | BUY | — | USD to spend (market BUY). |
| `--size TEXT` | SELL | — | Shares to sell (market SELL). |
| `--max-spend TEXT` | optional | = `--usd` | Fee-inclusive USD cap (BUY). |
| `--order-type TEXT` | optional | `FAK` | `FAK` or `FOK`. |
| `--dry-run` | optional | `false` | Preview only. |
| `--yes` | optional | `false` | Skip confirmation. |

### `clob post-orders`
Build and post **multiple limit orders** in one call. All options required; lists are comma-separated and positionally aligned.

| Option | Req | Meaning |
|---|---|---|
| `--tokens TEXT` | **required** | Comma-separated token IDs. |
| `--side TEXT` | **required** | `BUY` / `SELL` (applies to all). |
| `--prices TEXT` | **required** | Comma-separated prices (aligned with tokens). |
| `--sizes TEXT` | **required** | Comma-separated sizes (aligned with tokens). |

```bash
poly clob post-orders --tokens 111,222 --side BUY --prices 0.4,0.6 --sizes 10,5
```

---

## 4. Order management (`clob`)

All require the signer key (authenticated).

### `clob orders`
List your open orders. Columns: `id`, `side`, `price`, `original_size`, `size_matched`, `outcome`, `status`.

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--market TEXT` | optional | — | Filter to one market (condition id). |

### `clob order <ORDER_ID>`
Get details of a single order. `<ORDER_ID>` required argument.

### `clob cancel <ORDER_ID>`
Cancel a single order by ID. `<ORDER_ID>` required argument.

### `clob cancel-orders <IDS>`
Cancel multiple orders. `<IDS>` = comma-separated order IDs (required argument).

```bash
poly clob cancel-orders 0xabc,0xdef
```

### `clob cancel-market`
Cancel all orders for one market.

| Option | Req | Meaning |
|---|---|---|
| `--market TEXT` | **required** | The market (condition id). |

### `clob cancel-all`
Cancel **ALL** open orders.

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--yes` | optional | `false` | Skip the typed-`YES` confirmation (otherwise prompts). |

---

## 5. Account & balances

### `clob balance`
Show your balance for an asset type, in **human units** (USDC / shares); also returns `raw` base units.

| Option | Req | Default | Meaning |
|---|---|---|---|
| `--asset-type TEXT` | **required** | — | `collateral` (USDC cash) or `conditional` (outcome-token shares). |
| `--token TEXT` | optional | — | Token id, required when asset-type is `conditional`. |

```bash
poly clob balance --asset-type collateral
poly clob balance --asset-type conditional --token 472367...
```

### `clob trades`
List your account trades. Columns: `matched_at`, `side`, `outcome`, `price`, `size`, `status`.

### `data positions [ADDRESS]`
List positions. `ADDRESS` is optional and **defaults to your api_wallet** (not the EOA).

| Option / Arg | Req | Default | Meaning |
|---|---|---|---|
| `<ADDRESS>` | optional | your api_wallet | Any wallet to inspect. |
| `--limit INT` | optional | `20` | Max positions. |

### `data value [ADDRESS]`
Portfolio value. `ADDRESS` optional, defaults to your api_wallet.

```bash
poly data value
poly data positions 0x9377... --limit 50
```

---

## 6. Key concepts & gotchas

- **Always `uv run poly …`** — Python ≥3.11 is required; the system `python3` may be older.
- **Signer EOA vs api_wallet** — `wallet address` and the `data` default both use the **api_wallet** (holds funds & trades; the website's "API use only" address — do not send funds to it directly). The EOA only signs and is normally empty. They are different addresses.
- **⚠️ `api_wallet` can be the WRONG derived wallet** — one key derives several addresses (EOA + POLY_PROXY + GNOSIS_SAFE + `uups`/`beacon` type-3 deposit wallets), and the SDK's default pick isn't guaranteed to be your funded account. **Deposit only via polymarket.com** (never to a CLI-printed address); verify `api_wallet` == polymarket.com/settings; if it differs, set `"wallet_address": "0x…"` in `config.json` to force the right account. A live order whose signing wallet ≠ your funded account is rejected for insufficient balance — stop and fix the wallet, don't retry.
- **Balances are 6-decimal base units on-chain** — `clob balance` converts to human units for you (and shows `raw`); other raw SDK numbers may need ÷ 1,000,000.
- **Event slug ≠ market slug** — discover the event, then drill to its market slug (`…-usa`, `…-draw`, …) before trading.
- **`--dry-run` first** — every trade path supports it; it builds + signs + previews **without submitting**. Real submits require a typed `YES` unless `--yes`.
- **Market-order side semantics** — BUY spends `--usd`; SELL delivers `--size`. Mixing is rejected up front.
- **`-o json`** — returns full fields (token ids, condition ids) that table mode omits; use it when feeding another tool.
