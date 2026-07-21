"""Generation-based publication for matched DuckDB and JSON artifacts.

Control paths are deliberately validated before publication: ``.generations`` is a
real directory and ``current`` is either absent or our direct generation pointer.
This protects local builds from following a pre-existing control-path symlink.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any


def current_artifact_path(requested_path: str | Path) -> Path:
    """Return the stable path through which a requested artifact is consumed."""
    output = Path(requested_path).expanduser().resolve()
    return output.parent / "current" / output.name


def _lstat(path: Path) -> os.stat_result | None:
    """Return metadata without following a link, or ``None`` when absent."""
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _generation_root(root: Path) -> Path:
    """Create and validate the non-symlink directory holding generations."""
    generations = root / ".generations"
    metadata = _lstat(generations)
    if metadata is None:
        generations.mkdir(parents=True)
        metadata = _lstat(generations)
    if metadata is None or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(".generations must be a non-symlink directory")

    # Resolve only after rejecting the control-path symlink, so all child targets
    # are constructed beneath the directory we just checked.
    resolved = generations.resolve(strict=True)
    if resolved.parent != root.resolve(strict=True):
        raise ValueError(".generations must be directly beneath the output directory")
    return resolved


def _validate_current_pointer(root: Path, generations: Path) -> None:
    """Accept only an absent pointer or a direct pointer to an existing generation."""
    current = root / "current"
    metadata = _lstat(current)
    if metadata is None:
        return
    if not stat.S_ISLNK(metadata.st_mode):
        raise ValueError("current must be a symlink or be absent")

    target_name = Path(os.readlink(current))
    if target_name.is_absolute():
        raise ValueError("current must point directly to a non-symlink generation")
    target = root / target_name
    target_metadata = _lstat(target)
    if (
        target_metadata is None
        or stat.S_ISLNK(target_metadata.st_mode)
        or not stat.S_ISDIR(target_metadata.st_mode)
        or target.parent.resolve(strict=True) != generations
    ):
        raise ValueError("current must point directly to a non-symlink generation")


def publish_generation(
    database_temporary: Path,
    report: dict[str, Any],
    database_output: str | Path,
    report_output: str | Path,
) -> tuple[Path, Path]:
    """Publish a database/report pair by atomically switching one directory pointer.

    The requested paths supply artifact names and must share a directory. Consumers read
    the returned ``current/<name>`` paths; an interrupted build cannot alter that pointer.

    This is protection against accidental or pre-existing local control-path links, not a
    defense against a hostile concurrent process that swaps directory entries between the
    checks and filesystem operations. Such a process requires OS-level directory locks or
    descriptor-relative operations outside this local pipeline's threat model.
    """
    database = Path(database_output).expanduser().resolve()
    report_path = Path(report_output).expanduser().resolve()
    if database.parent != report_path.parent:
        raise ValueError("database and JSON output must have the same parent directory")
    if database.name == report_path.name:
        raise ValueError("database and JSON output must have distinct names")

    root = database.parent
    generations = _generation_root(root)
    _validate_current_pointer(root, generations)
    generation_name = uuid.uuid4().hex
    pending = generations / f".pending-{generation_name}"
    generation = generations / generation_name
    pointer_temporary = root / f".current-{generation_name}"
    pending_created = False
    pointer_created = False
    try:
        # UUID collisions are extraordinarily unlikely, but never overwrite a generation
        # if one does occur (or if another local process created the same path).
        if _lstat(generation) is not None or _lstat(pending) is not None:
            raise FileExistsError(f"generation path already exists: {generation_name}")
        pending.mkdir()
        pending_created = True
        shutil.move(str(database_temporary), pending / database.name)
        with (pending / report_path.name).open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(pending, generation)
        pending_created = False
        os.symlink(str(Path(".generations") / generation_name), pointer_temporary)
        pointer_created = True
        os.replace(pointer_temporary, root / "current")
        pointer_created = False
    except Exception:
        if pending_created:
            shutil.rmtree(pending, ignore_errors=True)
        raise
    finally:
        if pointer_created:
            pointer_temporary.unlink(missing_ok=True)
    return current_artifact_path(database), current_artifact_path(report_path)
