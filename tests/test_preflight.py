from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis import cli, runner
from quant_crypto_basis.catalog import BTC_SPOT, ETH_PERP
from quant_crypto_basis.preflight import preflight
from quant_crypto_basis.strategy import BasisFundingConfig


def _state(root):
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def _forbid(*args, **kwargs):
    raise AssertionError("preflight must not create strategy/execution/account state")


@pytest.mark.parametrize("source", ["binance", "okx"])
def test_read_only_preflight_checks_both_sources_without_execution(
    source, fixture_loader, monkeypatch
):
    before = _state(fixture_loader.root)
    for name in ("BasisFundingStrategy", "ExactAccountLedger", "DeterministicRunEngine"):
        monkeypatch.setattr(runner, name, _forbid)
    result = preflight(source=source, fixture_loader=fixture_loader)
    assert result["status"] == "pass"
    assert result["read_only"] and not result["investable"]
    assert result["evidence_kind"] == "synthetic"
    assert result["base_currency"] == "USDT"
    assert result["event_count"] == 17
    assert set(result["cross_source_rows"]) == {"binance", "okx"}
    assert len(result["inputs"]) == 3
    assert _state(fixture_loader.root) == before


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"seed": True}, "seed"),
        ({"seed": -1}, "seed"),
        ({"seed": 1.5}, "seed"),
        ({"initial_cash": "NaN"}, "initial_cash"),
        ({"initial_cash": "Infinity"}, "initial_cash"),
        ({"initial_cash": "0"}, "initial_cash"),
        ({"initial_cash": "-1"}, "initial_cash"),
        ({"initial_cash": "invalid"}, "initial_cash"),
        ({"run_id": " "}, "run_id"),
        ({"source": "unknown"}, "source"),
        ({"strategy_config": {}}, "strategy_config"),
        ({"strategy_config": replace(BasisFundingConfig(), spot_instrument_id="unknown")}, "legs"),
        (
            {"strategy_config": replace(BasisFundingConfig(), perpetual_instrument_id=ETH_PERP)},
            "matched",
        ),
    ],
)
def test_shared_static_rejections_precede_execution(arguments, expected, monkeypatch):
    monkeypatch.setattr(runner, "ExactAccountLedger", _forbid)
    for check in (preflight, runner.run_fixture_backtest):
        with pytest.raises(ValidationError, match=expected):
            check(**arguments)


def test_damage_to_unselected_source_is_rejected(fixture_loader):
    index = json.loads((fixture_loader.root / "index.json").read_text(encoding="utf-8"))
    path = fixture_loader.root / index["files"]["okx"]
    path.write_text("[]", encoding="utf-8")
    for check in (preflight, runner.run_fixture_backtest):
        with pytest.raises(ValidationError, match="SHA-256 mismatch"):
            check(source="binance", fixture_loader=fixture_loader)


@pytest.mark.parametrize("mutation", ["content", "inventory"])
def test_input_change_during_validation_is_rejected(fixture_loader, monkeypatch, mutation):
    original = runner.load_certified_fixtures

    def changing(loader):
        result = original(loader)
        if mutation == "content":
            with (loader.root / "index.json").open("a", encoding="utf-8") as stream:
                stream.write("\n")
        else:
            (loader.root / "new.txt").write_text("changed", encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "load_certified_fixtures", changing)
    with pytest.raises(ValidationError, match="changed during validation"):
        preflight(fixture_loader=fixture_loader)


def test_preflight_and_replay_preserve_input_and_same_configuration(fixture_loader):
    before = _state(fixture_loader.root)
    config = BasisFundingConfig(passive_limits=False)
    checked = preflight(
        source="okx", seed=19, strategy_config=config, fixture_loader=fixture_loader
    )
    run = runner.run_fixture_backtest(
        source="okx", seed=19, strategy_config=config, fixture_loader=fixture_loader
    )
    assert checked["event_count"] == run.result.event_count
    assert checked["config"]["passive_limits"] == run.strategy_config.passive_limits
    assert checked["config"]["initial_cash"] == str(run.initial_cash.to_decimal())
    assert run.result.fill_count == 2
    assert _state(fixture_loader.root) == before
    assert BTC_SPOT in run.snapshot.positions


def test_change_during_replay_is_rejected(fixture_loader, monkeypatch):
    original = runner._assert_complete_pair_executions

    def changing(*args):
        original(*args)
        with (fixture_loader.root / "index.json").open("a", encoding="utf-8") as stream:
            stream.write("\n")

    monkeypatch.setattr(runner, "_assert_complete_pair_executions", changing)
    with pytest.raises(ValidationError, match="changed during replay"):
        runner.run_fixture_backtest(fixture_loader=fixture_loader)


def test_cli_preflight_needs_no_output_or_clean_git_worktree(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_fixture_backtest", _forbid)
    assert cli.main(["--source", "okx", "--preflight", "--taker", "--seed", "19"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["config"]["source"] == "okx"
    assert payload["config"]["seed"] == 19
    assert payload["config"]["passive_limits"] is False
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SystemExit):
        cli.main(["--preflight", "--output", str(tmp_path / "output")])
    with pytest.raises(SystemExit):
        cli.main(["--preflight", "--code-version", "a" * 40])
    with pytest.raises(SystemExit):
        cli.main([])
    assert list(tmp_path.iterdir()) == []
