from __future__ import annotations

from pathlib import Path

import pytest
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.provenance import resolve_clean_head


def test_code_version_resolves_exact_clean_head(
    clean_git_repo: tuple[Path, str],
) -> None:
    repository, head = clean_git_repo
    assert resolve_clean_head(repository_root=repository) == head
    assert resolve_clean_head(expected_code_version=head, repository_root=repository) == head


def test_code_version_rejects_dirty_worktree(
    clean_git_repo: tuple[Path, str],
) -> None:
    repository, _ = clean_git_repo
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="clean Git worktree"):
        resolve_clean_head(repository_root=repository)


def test_code_version_rejects_wrong_project(
    clean_git_repo: tuple[Path, str],
) -> None:
    repository, _ = clean_git_repo
    pyproject = repository / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace("quant-crypto-basis", "some-other-project"),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="not quant-crypto-basis"):
        resolve_clean_head(repository_root=repository)
