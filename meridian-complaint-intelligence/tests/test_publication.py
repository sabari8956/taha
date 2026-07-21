from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from meridian_assistant.publication import publish_generation  # noqa: E402


def publish_fixture(tmp_path: Path, contents: bytes = b"database") -> tuple[Path, Path, Path]:
    temporary = tmp_path / "temporary.duckdb"
    temporary.write_bytes(contents)
    output_root = tmp_path / "output"
    database = output_root / "snapshot.duckdb"
    report = output_root / "report.json"
    return temporary, database, report


def test_symlinked_generation_root_is_rejected_before_outside_write(tmp_path: Path) -> None:
    temporary, database, report = publish_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    database.parent.mkdir()
    (database.parent / ".generations").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match=".generations must be a non-symlink directory"):
        publish_generation(temporary, {"status": "new"}, database, report)

    assert not list(outside.iterdir())
    assert temporary.read_bytes() == b"database"


def test_current_symlink_outside_generation_root_is_rejected(tmp_path: Path) -> None:
    temporary, database, report = publish_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    database.parent.mkdir()
    (database.parent / ".generations").mkdir()
    (database.parent / "current").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="current must point directly"):
        publish_generation(temporary, {"status": "new"}, database, report)

    assert temporary.read_bytes() == b"database"


def test_valid_current_control_symlink_is_replaced(tmp_path: Path) -> None:
    first_temporary, database, report = publish_fixture(tmp_path, b"first")
    publish_generation(first_temporary, {"status": "first"}, database, report)
    old_current = os.readlink(database.parent / "current")

    second_temporary = tmp_path / "second.duckdb"
    second_temporary.write_bytes(b"second")
    publish_generation(second_temporary, {"status": "second"}, database, report)

    assert os.readlink(database.parent / "current") != old_current
    assert (database.parent / "current" / database.name).read_bytes() == b"second"


def test_pointer_replacement_failure_keeps_current_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_temporary, database, report = publish_fixture(tmp_path, b"first")
    publish_generation(first_temporary, {"status": "first"}, database, report)
    current = database.parent / "current"
    old_target = os.readlink(current)

    second_temporary = tmp_path / "second.duckdb"
    second_temporary.write_bytes(b"second")
    original_replace = os.replace

    def fail_pointer_replace(source: str | Path, destination: str | Path) -> None:
        if Path(source).name.startswith(".current-") and Path(destination) == current:
            raise OSError("pointer replacement failed")
        original_replace(source, destination)

    monkeypatch.setattr("meridian_assistant.publication.os.replace", fail_pointer_replace)
    with pytest.raises(OSError, match="pointer replacement failed"):
        publish_generation(second_temporary, {"status": "second"}, database, report)

    assert os.readlink(current) == old_target
    assert not list(database.parent.glob(".current-*"))
