from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import load_messages, rewrite_messages
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.catalog import FIXTURE_EFFECTIVE_TO
from quant_crypto_basis.fixtures import FixtureLoader


def _rewrite_index(root: Path, mutate) -> None:
    path = root / "index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    mutate(index)
    path.write_text(json.dumps(index), encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda index: index.update(schema_version="9.9.9"), "schema is unsupported"),
        (lambda index: index.update(certification_scope="network"), "scope is not offline"),
        (lambda index: index.update(files={"binance": "binance/events.json"}), "exactly Binance"),
        (
            lambda index: index["files"].update(okx=17),
            "fixture paths must be strings",
        ),
        (
            lambda index: index["files"].update(okx="BINANCE/EVENTS.JSON"),
            "case-insensitive path ambiguity",
        ),
        (lambda index: index.update(sha256={}), "hashes do not match"),
    ],
)
def test_fixture_manifest_contract_rejects_ambiguous_metadata(
    fixture_root: Path,
    mutation,
    message: str,
) -> None:
    _rewrite_index(fixture_root, mutation)
    with pytest.raises(ValidationError, match=message):
        FixtureLoader(fixture_root).load("binance")


def test_fixture_root_and_safe_path_contracts_fail_closed(
    tmp_path: Path, fixture_root: Path
) -> None:
    plain_file = fixture_root / "binance" / "events.json"
    with pytest.raises(ValidationError, match="physical directory"):
        FixtureLoader(plain_file).load("binance")

    loader = FixtureLoader(fixture_root)
    with pytest.raises(ValidationError, match="canonical POSIX"):
        loader._safe_file("")
    with pytest.raises(ValidationError, match="canonical POSIX"):
        loader._safe_file("binance\\events.json")
    with pytest.raises(ValidationError, match="escapes"):
        loader._safe_file("../fixtures")
    with pytest.raises(ValidationError, match="one physical file"):
        loader._safe_file("binance")

    missing = tmp_path / "missing"
    with pytest.raises((FileNotFoundError, ValidationError)):
        FixtureLoader(missing).load("binance")


def test_fixture_message_and_raw_identity_contracts_fail_closed(fixture_root: Path) -> None:
    rewrite_messages(fixture_root, "binance", [])
    with pytest.raises(ValidationError, match="non-empty message list"):
        FixtureLoader(fixture_root).load("binance")

    messages = load_messages(
        Path(__file__).parents[1] / "src" / "quant_crypto_basis" / "fixtures", "binance"
    )
    missing_symbol = [dict(item) for item in messages]
    del missing_symbol[0]["s"]
    rewrite_messages(fixture_root, "binance", missing_symbol)
    with pytest.raises(ValidationError, match="missing symbol"):
        FixtureLoader(fixture_root).load("binance")

    messages = load_messages(
        Path(__file__).parents[1] / "src" / "quant_crypto_basis" / "fixtures", "binance"
    )
    before_event = [dict(item) for item in messages]
    before_event[0]["received_at"] = before_event[0]["T"] - 1
    rewrite_messages(fixture_root, "binance", before_event)
    with pytest.raises(ValidationError, match="precedes event_time"):
        FixtureLoader(fixture_root).load("binance")

    outside = [dict(item) for item in messages]
    outside[0]["T"] = 1
    outside[0]["received_at"] = 1
    rewrite_messages(fixture_root, "binance", outside)
    with pytest.raises(ValidationError, match="outside its declared applicability"):
        FixtureLoader(fixture_root).load("binance")


def test_manifest_time_parser_rejects_non_timestamp_and_invalid_timestamp() -> None:
    with pytest.raises(ValidationError, match="must be a timestamp"):
        FixtureLoader._manifest_time(None, "effective_from")
    with pytest.raises(ValidationError, match="is invalid"):
        FixtureLoader._manifest_time("not-a-time", "effective_from")


def test_normalized_fixture_validation_rejects_each_temporal_and_identity_violation() -> None:
    loader = FixtureLoader()
    batch = loader.load("binance")
    records = [dict(record) for record in batch.records]
    events = list(batch.events)
    kwargs = {
        "provider": "binance",
        "records": records,
        "cutoff": FIXTURE_EFFECTIVE_TO,
        "start": batch.applicability_start,
        "end": batch.applicability_end,
    }

    cases = [
        ([replace(events[0], source="other"), *events[1:]], records, "source differs"),
        (
            [events[1], events[0], *events[2:]],
            [records[1], records[0], *records[2:]],
            "out of available-time",
        ),
        (
            [
                replace(events[0], available_at=FIXTURE_EFFECTIVE_TO + timedelta(seconds=1)),
                *events[1:],
            ],
            records,
            "future data",
        ),
        (
            [
                replace(events[0], event_time=batch.applicability_start - timedelta(seconds=1)),
                *events[1:],
            ],
            records,
            "outside fixture",
        ),
        (
            [
                replace(
                    events[0], trading_day=batch.applicability_start.date() - timedelta(days=1)
                ),
                *events[1:],
            ],
            records,
            "trading_day",
        ),
        (
            [replace(events[0], session_id="wrong-session"), *events[1:]],
            records,
            "session identity",
        ),
    ]
    for mutated, mutated_records, message in cases:
        with pytest.raises(ValidationError, match=message):
            loader._validate_normalized(
                events=tuple(mutated),
                records=mutated_records,
                **{key: value for key, value in kwargs.items() if key != "records"},
            )

    bad_records = list(records)
    bad_records[0]["available_at"] = "1970-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="decode changed available_at"):
        loader._validate_normalized(
            events=tuple(events),
            records=bad_records,
            **{key: value for key, value in kwargs.items() if key != "records"},
        )
