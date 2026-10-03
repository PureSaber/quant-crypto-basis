"""Local-only fixture backtest command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_crypto_basis.artifacts import write_certified_standard_run
from quant_crypto_basis.runner import DEFAULT_INITIAL_CASH, run_fixture_backtest
from quant_crypto_basis.strategy import BasisFundingConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qcb-run-fixture",
        description="Run an offline research fixture through QExec and QLab standard/v2",
    )
    parser.add_argument("--source", choices=("binance", "okx"), default="binance")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true", help="Read-only fixture/config check")
    parser.add_argument(
        "--code-version",
        help="Optional expected full commit; always verified against the current clean HEAD",
    )
    parser.add_argument("--run-id", default="crypto-basis-fixture-v1")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--initial-cash", default=str(DEFAULT_INITIAL_CASH))
    liquidity = parser.add_mutually_exclusive_group()
    liquidity.add_argument("--liquidity", choices=("maker", "taker"))
    liquidity.add_argument(
        "--taker",
        action="store_true",
        help="Use simulated market orders; default uses passive limits for maker fills",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = BasisFundingConfig(passive_limits=not args.taker and args.liquidity != "taker")
    if args.preflight:
        if args.output is not None or args.code_version is not None:
            parser.error("--preflight does not accept --output or --code-version")
        from quant_crypto_basis.preflight import preflight

        print(
            json.dumps(
                preflight(
                    source=args.source,
                    run_id=args.run_id,
                    seed=args.seed,
                    strategy_config=config,
                    initial_cash=args.initial_cash,
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.output is None:
        parser.error("--output is required unless --preflight is selected")
    run = run_fixture_backtest(
        source=args.source,
        run_id=args.run_id,
        seed=args.seed,
        strategy_config=config,
        initial_cash=args.initial_cash,
    )
    manifest = write_certified_standard_run(
        run,
        args.output,
        code_version=args.code_version,
    )
    print(
        json.dumps(
            {
                "run_id": manifest.run_id,
                "profile": manifest.profile,
                "source": args.source,
                "code_version": manifest.code_version,
                "event_count": run.result.event_count,
                "order_count": run.result.order_count,
                "fill_count": run.result.fill_count,
                "ledger_sha256": run.result.ledger_sha256,
                "standard_v2": str(args.output / "standard" / "v2"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
