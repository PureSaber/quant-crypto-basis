from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import load_messages, rewrite_messages
from quant_data_kit import (
    BookDeltaEvent,
    BookSnapshotEvent,
    FundingRateEvent,
    MarkPriceEvent,
    QuoteEvent,
    TradeEvent,
)
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.catalog import FIXTURE_EFFECTIVE_FROM
from quant_crypto_basis.fixtures import FixtureLoader, load_certified_fixtures


def test_dual_source_fixture_quality_is_complete_without_price_equality() -> None:
    batches, report = load_certified_fixtures()
    assert set(batches) == {"binance", "okx"}
    assert report.providers == ("binance", "okx")
    assert report.price_equality_required is False
    assert report.row_counts == {"binance": 17, "okx": 17}
    assert len(report.common_instruments) == 4
    assert all(len(types) == 6 for types in report.event_types.values())
    expected_event_types = (
        TradeEvent,
        QuoteEvent,
        BookSnapshotEvent,
        BookDeltaEvent,
        FundingRateEvent,
        MarkPriceEvent,
    )
    assert all(isinstance(event, expected_event_types) for event in batches["binance"].events)
    binance_trade = next(
        record for record in batches["binance"].records if record["event_type"] == "trade"
    )
    okx_trade = next(record for record in batches["okx"].records if record["event_type"] == "trade")
    assert binance_trade["price"] != okx_trade["price"]


def test_fixture_hashes_scope_24x7_and_applicability_are_exact() -> None:
    loader = FixtureLoader()
    root = loader.root
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["applicability"]["historical_listing_claim"] is False
    for provider, relative in index["files"].items():
        batch = loader.load(provider)
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == batch.file_sha256
        assert all(event.session_id.startswith(f"{provider}-24x7-") for event in batch.events)
        assert all(event.trading_day == event.event_time.date() for event in batch.events)


def test_fixture_loader_rejects_unknown_source_and_future_available_data() -> None:
    loader = FixtureLoader()
    with pytest.raises(ValidationError, match="unsupported"):
        loader.load("kraken")
    with pytest.raises(ValidationError, match="not yet available|future"):
        loader.load("binance", as_of=FIXTURE_EFFECTIVE_FROM + timedelta(seconds=2))


def test_binance_gap_duplicate_and_available_time_disorder_fail_closed(
    fixture_root: Path,
) -> None:
    messages = load_messages(fixture_root, "binance")
    gap = deepcopy(messages)
    update = next(item for item in gap if item["e"] == "depthUpdate")
    update["pu"] = 99
    rewrite_messages(fixture_root, "binance", gap)
    with pytest.raises(ValidationError, match="gap|bridge"):
        FixtureLoader(fixture_root).load("binance")

    rewrite_messages(fixture_root, "binance", messages)
    duplicate = deepcopy(messages)
    duplicate.append(deepcopy(duplicate[-1]))
    rewrite_messages(fixture_root, "binance", duplicate)
    with pytest.raises(ValidationError, match="Duplicate|duplicate"):
        FixtureLoader(fixture_root).load("binance")

    disorder = deepcopy(messages)
    disorder[0], disorder[1] = disorder[1], disorder[0]
    rewrite_messages(fixture_root, "binance", disorder)
    with pytest.raises(ValidationError, match="out of available-time order"):
        FixtureLoader(fixture_root).load("binance")


def test_okx_checksum_and_file_hash_fail_closed(fixture_root: Path) -> None:
    messages = load_messages(fixture_root, "okx")
    bad_checksum = deepcopy(messages)
    snapshot = next(item for item in bad_checksum if item.get("action") == "snapshot")
    snapshot["checksum"] += 1
    rewrite_messages(fixture_root, "okx", bad_checksum)
    with pytest.raises(ValidationError, match="checksum mismatch"):
        FixtureLoader(fixture_root).load("okx")

    rewrite_messages(fixture_root, "okx", messages)
    event_path = fixture_root / "okx" / "events.json"
    event_path.write_text(event_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValidationError, match="SHA-256 mismatch"):
        FixtureLoader(fixture_root).load("okx")


def test_fixture_directory_and_manifest_ambiguity_fail_closed(fixture_root: Path) -> None:
    (fixture_root / "rogue.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError, match="file set is ambiguous"):
        FixtureLoader(fixture_root).load("binance")
    (fixture_root / "rogue.json").unlink()

    index_path = fixture_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["applicability"]["historical_listing_claim"] = True
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValidationError, match="listing history"):
        FixtureLoader(fixture_root).load("binance")


def test_cross_source_universe_and_event_coverage_must_match(fixture_root: Path) -> None:
    messages = load_messages(fixture_root, "okx")
    without_eth = [item for item in messages if not str(item["instId"]).startswith("ETH")]
    rewrite_messages(fixture_root, "okx", without_eth)
    with pytest.raises(ValidationError, match="instrument coverage differs"):
        load_certified_fixtures(FixtureLoader(fixture_root))

    without_marks = [item for item in messages if item["channel"] != "mark-price"]
    rewrite_messages(fixture_root, "okx", without_marks)
    with pytest.raises(ValidationError, match="event coverage is incomplete"):
        load_certified_fixtures(FixtureLoader(fixture_root))


def test_fixture_invalid_json_and_non_object_messages_fail_closed(fixture_root: Path) -> None:
    index_path = fixture_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    path = fixture_root / index["files"]["binance"]
    path.write_text("not-json", encoding="utf-8")
    index["sha256"][index["files"]["binance"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValidationError, match="invalid JSON"):
        FixtureLoader(fixture_root).load("binance")

    path.write_text("[1]", encoding="utf-8")
    index["sha256"][index["files"]["binance"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValidationError, match="non-object"):
        FixtureLoader(fixture_root).load("binance")
