"""Fail-closed Git provenance for certified local artifacts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from quant_data_kit.exceptions import ValidationError

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_PROJECT_NAME = "quant-crypto-basis"


def _git(candidate: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(candidate), *arguments],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
        )
    except OSError as exc:
        raise ValidationError("Git is required to certify code_version") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise ValidationError(f"cannot resolve certified Git repository: {detail}")
    return completed.stdout.strip()


def resolve_clean_head(
    *,
    expected_code_version: str | None = None,
    repository_root: Path | None = None,
) -> str:
    """Return the current full HEAD only for this project and a clean worktree."""
    candidate = (repository_root or Path.cwd()).resolve()
    root_text = _git(candidate, "rev-parse", "--show-toplevel")
    root = Path(root_text).resolve()
    pyproject_path = root / "pyproject.toml"
    try:
        project_name = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["name"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError("certified Git root must contain a valid pyproject.toml") from exc
    if project_name != _PROJECT_NAME:
        raise ValidationError("certified Git root is not quant-crypto-basis")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValidationError("certified code_version requires a clean Git worktree")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if _COMMIT_PATTERN.fullmatch(head) is None:
        raise ValidationError("Git HEAD did not resolve to a full lowercase commit SHA")
    if expected_code_version is not None:
        if _COMMIT_PATTERN.fullmatch(expected_code_version) is None:
            raise ValidationError("expected code_version must be a full lowercase commit SHA")
        if expected_code_version != head:
            raise ValidationError("expected code_version does not match current Git HEAD")
    return head
