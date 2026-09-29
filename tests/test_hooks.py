from pathlib import Path

import pytest

from helpers import files
from usr.plugins.multi_project_manager import hooks
from usr.plugins.multi_project_manager.helpers import backups


@pytest.fixture
def base(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    return tmp_path


def make_backup():
    backup = backups.Backup("src", ["secrets"], {})
    backup.add_entry("t1", {"secrets": 'K="hush-1234"\n'})
    backup.save()


def test_uninstall_removes_backups_and_the_empty_data_folders(base):
    make_backup()
    make_backup()
    hooks.uninstall()
    assert not (base / "usr" / "multi_project_manager").exists()


def test_uninstall_never_removes_files_it_does_not_own(base):
    make_backup()
    stray = base / "usr" / "multi_project_manager" / "notes.txt"
    stray.write_text("keep", encoding="utf-8")
    hooks.uninstall()
    assert backups.list_records() == []
    assert stray.read_text(encoding="utf-8") == "keep"


def test_uninstall_with_nothing_to_clean_is_harmless(base):
    hooks.uninstall()
    hooks.uninstall()
