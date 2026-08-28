from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from quant_crypto_basis.fixtures import FixtureLoader


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    source = Path(__file__).parents[1] / "src" / "quant_crypto_basis" / "fixtures"
    target = tmp_path / "fixtures"
    shutil.copytree(source, target)
    return target


def rewrite_messages(root: Path, provider: str, messages: list[dict]) -> None:
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    relative = index["files"][provider]
    event_path = root / relative
    event_path.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    index["sha256"][relative] = hashlib.sha256(event_path.read_bytes()).hexdigest()
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_messages(root: Path, provider: str) -> list[dict]:
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    return json.loads((root / index["files"][provider]).read_text(encoding="utf-8"))


@pytest.fixture
def fixture_loader(fixture_root: Path) -> FixtureLoader:
    return FixtureLoader(fixture_root)
