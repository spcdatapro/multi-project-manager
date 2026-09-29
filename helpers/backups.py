"""Backups taken before a propagation overwrites project sections.

One JSON file per operation under usr/multi_project_manager/backups/ (outside the plugin folder,
mode 0600: it holds the previous texts, secrets included). Only the last MAX_BACKUPS are kept.
Nothing here returns section texts to callers other than `load`; `summarize` is UI-safe.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from helpers import files

from usr.plugins.multi_project_manager.helpers.errors import SectionError

BACKUP_DIR_PARTS = ("usr", "multi_project_manager", "backups")
MAX_BACKUPS = 20
_ID_RE = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{6}$")


def backups_dir() -> str:
    return files.get_abs_path(*BACKUP_DIR_PARTS)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(backup_id: str) -> str:
    if not isinstance(backup_id, str) or not _ID_RE.match(backup_id):
        raise SectionError("Invalid backup id")
    return os.path.join(backups_dir(), f"{backup_id}.json")


def new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class Backup:
    """A backup being built during a propagation."""

    def __init__(self, source: str, sections: list[str], modes: dict[str, str]):
        self.record: dict[str, Any] = {
            "id": new_id(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "sections": list(sections),
            "modes": dict(modes),
            "entries": [],
            "undone_at": None,
        }

    @property
    def id(self) -> str:
        return self.record["id"]

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.record["entries"]

    def add_entry(self, project: str, previous: dict[str, str], role: str = "target") -> None:
        self.entries.append(
            {"project": project, "role": role, "previous": dict(previous), "after": {}, "restored": False}
        )

    def save(self) -> None:
        save_record(self.record)


def save_record(record: dict[str, Any]) -> None:
    """Atomic write, owner-only permissions. Raises OSError if the disk refuses."""
    path = _path(record["id"])
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass  # some bind mounts ignore chmod
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    prune()


def load(backup_id: str) -> dict[str, Any]:
    path = _path(backup_id)
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        raise SectionError("Backup not found") from None
    except (OSError, ValueError):
        raise SectionError("Backup could not be read") from None
    if not isinstance(record, dict) or record.get("id") != backup_id or not isinstance(record.get("entries"), list):
        raise SectionError("Backup is corrupted")
    return record


def exists(backup_id: str) -> bool:
    """Validates the id (SectionError if malformed) and checks the file is there."""
    return os.path.isfile(_path(backup_id))


def _ids() -> list[str]:
    try:
        names = os.listdir(backups_dir())
    except FileNotFoundError:
        return []
    return sorted((n[:-5] for n in names if n.endswith(".json") and _ID_RE.match(n[:-5])), reverse=True)


def list_records() -> list[dict[str, Any]]:
    """Newest first. Unreadable files are skipped."""
    records = []
    for backup_id in _ids():
        try:
            records.append(load(backup_id))
        except SectionError:
            continue
    return records


def delete(backup_id: str) -> None:
    try:
        os.remove(_path(backup_id))
    except FileNotFoundError:
        pass


def delete_all() -> int:
    ids = _ids()
    for backup_id in ids:
        delete(backup_id)
    return len(ids)


def prune() -> None:
    for backup_id in _ids()[MAX_BACKUPS:]:
        delete(backup_id)


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    """UI-safe view of a backup: names and section keys only, never any text."""
    return {
        "id": record["id"],
        "created_at": record.get("created_at", ""),
        "source": record.get("source", ""),
        "sections": list(record.get("sections", [])),
        "modes": dict(record.get("modes", {})),
        "undone": bool(record.get("undone_at")),
        "changes": [
            {
                "project": entry.get("project", ""),
                "role": entry.get("role", "target"),
                "sections": list((entry.get("previous") or {}).keys()),
                "restored": bool(entry.get("restored")),
            }
            for entry in record["entries"]
        ],
    }
