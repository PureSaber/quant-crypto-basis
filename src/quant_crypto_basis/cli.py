"""Local-only fixture backtest command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_crypto_basis.artifacts import write_certified_standard_run
from quant_crypto_basis.runner import run_fixture_backtest
from quant_crypto_basis.strategy import BasisFundingConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qcb-run-fixture",
        description="Run an offline research fixture through QExec and QLab standard/v2",
    )
    parser.add_argument("--source", choices=("binance", "okx"), default="binance")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--run-id", default="crypto-basis-fixture-v1")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--taker",
        action="store_true",
        help="Use simulated market orders; default uses passive limits for maker fills",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BasisFundingConfig(passive_limits=not args.taker)
    run = run_fixture_backtest(
        source=args.source,
        run_id=args.run_id,
        seed=args.seed,
        strategy_config=config,
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
