# poly/cli.py
import typer

from .context import CliContext
from .groups import wallet
from .groups import setup as setup_group
from .groups import markets
from .groups import clob_trade
from .groups import data

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Polymarket CLI.")
_OUTPUT = {"fmt": "table"}  # mirrored for main()'s error envelope

app.add_typer(wallet.app, name="wallet")
app.command("setup")(setup_group.setup_cmd)
app.add_typer(markets.app, name="markets")
app.add_typer(clob_trade.app, name="clob")
app.add_typer(data.app, name="data")


@app.callback()
def root(
    ctx: typer.Context,
    output: str = typer.Option("table", "--output", "-o", help="table or json"),
    private_key: str = typer.Option(None, "--private-key", help="Override signer key."),
) -> None:
    if output not in ("table", "json"):
        raise typer.BadParameter("--output must be 'table' or 'json'")
    _OUTPUT["fmt"] = output
    ctx.obj = CliContext(output=output, private_key=private_key)


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


def main() -> int:
    try:
        import click as _click_mod
    except ModuleNotFoundError:
        from typer import _click as _click_mod  # type: ignore[no-redef]
    from polymarket import PolymarketError
    from .output import print_error
    try:
        app(standalone_mode=False)
        return 0
    except (_click_mod.exceptions.Abort, KeyboardInterrupt):
        print_error(_OUTPUT["fmt"], "aborted")
        return 1
    except _click_mod.exceptions.ClickException as exc:  # usage errors
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


if __name__ == "__main__":
    main()
