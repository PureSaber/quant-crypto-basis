from __future__ import annotations

import json
from pathlib import Path

from quant_lab import load_and_validate_standard_run

from quant_crypto_basis.cli import main

CODE_VERSION = "6df91eca238542c9c9d3013f733e7dc7b94f19dc"


def test_cli_runs_only_local_fixture_and_writes_valid_standard_v2(tmp_path: Path, capsys) -> None:
    output = tmp_path / "cli-run"
    assert (
        main(
            [
                "--source",
                "binance",
                "--output",
                str(output),
                "--code-version",
                CODE_VERSION,
                "--run-id",
                "cli-golden",
                "--seed",
                "19",
                "--taker",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == "backtest-ledger"
    assert payload["fill_count"] == 2
    assert Path(payload["standard_v2"]) == output / "standard" / "v2"
    assert load_and_validate_standard_run(output).run_id == "cli-golden"


def test_source_tree_has_no_network_live_broker_or_credential_path() -> None:
    source_root = Path(__file__).parents[1] / "src" / "quant_crypto_basis"
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(source_root.glob("*.py"))
    ).lower()
    forbidden_imports = (
        "import requests",
        "import httpx",
        "import urllib",
        "import socket",
        "import websockets",
        "import ccxt",
    )
    assert not any(value in production for value in forbidden_imports)
    assert "api_key" not in production
    assert "secret_key" not in production
    assert "send_order" not in production
    assert "place_order" not in production
