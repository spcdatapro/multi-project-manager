import json
import os
import stat
from pathlib import Path

import pytest

from helpers import files
from usr.plugins.multi_project_manager.helpers import backups
from usr.plugins.multi_project_manager.helpers.errors import SectionError


@pytest.fixture
def base(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    return tmp_path


def make_backup(secret="hush-1234"):
    backup = backups.Backup("src", ["secrets"], {"secrets": "merge"})
    backup.add_entry("t1", {"secrets": f'K="{secret}"\n'})
    backup.save()
    return backup


def test_save_and_load_roundtrip_lives_outside_the_plugin_folder(base):
    backup = make_backup()
    path = Path(backups.backups_dir()) / f"{backup.id}.json"
    assert path.is_file()
    assert "plugins" not in path.relative_to(base).parts
    assert backups.load(backup.id)["entries"][0]["previous"] == {"secrets": 'K="hush-1234"\n'}


def test_backup_file_is_private_and_no_temp_file_is_left(base):
    backup = make_backup()
    path = Path(backups.backups_dir()) / f"{backup.id}.json"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & 0o077 == 0, oct(mode)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


@pytest.mark.parametrize("bad", ["../etc/passwd", "x", "", None, 5, "20260101T000000000000Z-zzzzzz", "a/b"])
def test_invalid_ids_are_rejected_before_touching_the_disk(base, bad):
    for call in (backups.load, backups.exists):
        with pytest.raises(SectionError, match="Invalid backup id"):
            call(bad)


def test_missing_and_corrupted_backups_raise_clear_errors(base):
    with pytest.raises(SectionError, match="not found"):
        backups.load("20260101T000000000000Z-abcdef")
    backup = make_backup()
    path = Path(backups.backups_dir()) / f"{backup.id}.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SectionError, match="could not be read"):
        backups.load(backup.id)
    assert backups.list_records() == []  # unreadable files are skipped, not fatal
    assert backups.exists(backup.id) is True  # ...but can still be deleted
    backups.delete(backup.id)
    assert backups.exists(backup.id) is False


def test_only_the_newest_backups_are_kept(base, monkeypatch):
    monkeypatch.setattr(backups, "MAX_BACKUPS", 3)
    ids = [make_backup().id for _ in range(5)]
    kept = [r["id"] for r in backups.list_records()]
    assert kept == sorted(ids, reverse=True)[:3]


def test_summary_never_contains_section_text(base):
    backup = make_backup(secret="hush-1234")
    summary = backups.summarize(backups.load(backup.id))
    assert "hush-1234" not in json.dumps(summary)
    assert summary["changes"] == [{"project": "t1", "role": "target", "sections": ["secrets"], "restored": False}]
    assert summary["undone"] is False


def test_delete_and_delete_all(base):
    a, b = make_backup(), make_backup()
    backups.delete(a.id)
    backups.delete(a.id)  # deleting twice is harmless
    assert [r["id"] for r in backups.list_records()] == [b.id]
    assert backups.delete_all() == 1
    assert backups.delete_all() == 0
