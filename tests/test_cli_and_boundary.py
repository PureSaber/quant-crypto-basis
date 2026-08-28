from __future__ import annotations

import json
from pathlib import Path

import pytest
from quant_data_kit.exceptions import ValidationError
from quant_lab import load_and_validate_standard_run

from quant_crypto_basis.cli import main


def test_cli_resolves_clean_head_and_writes_valid_standard_v2(
    tmp_path: Path,
    capsys,
    monkeypatch,
    clean_git_repo: tuple[Path, str],
) -> None:
    repository, head = clean_git_repo
    monkeypatch.chdir(repository)
    output = tmp_path / "cli-run"
    assert (
        main(
            [
                "--source",
                "binance",
                "--output",
                str(output),
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
    assert payload["code_version"] == head
    assert payload["fill_count"] == 2
    assert Path(payload["standard_v2"]) == output / "standard" / "v2"
    manifest = load_and_validate_standard_run(output)
    assert manifest.run_id == "cli-golden"
    assert manifest.code_version == head


@pytest.mark.parametrize("code_version", [None, "0" * 40])
def test_cli_rejects_dirty_or_mismatched_head(
    tmp_path: Path,
    monkeypatch,
    clean_git_repo: tuple[Path, str],
    code_version: str | None,
) -> None:
    repository, _ = clean_git_repo
    monkeypatch.chdir(repository)
    arguments = ["--output", str(tmp_path / "rejected")]
    if code_version is None:
        (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    else:
        arguments.extend(["--code-version", code_version])
    with pytest.raises(ValidationError, match="clean Git worktree|does not match"):
        main(arguments)


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
