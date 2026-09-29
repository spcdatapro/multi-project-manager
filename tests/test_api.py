import asyncio
import json
from pathlib import Path

import pytest

from helpers import files
from helpers.api import ApiHandler
from helpers.modules import load_classes_from_file
from usr.plugins.multi_project_manager.api.delete_backup import DeleteBackup
from usr.plugins.multi_project_manager.api.duplicate_project import DuplicateProject
from usr.plugins.multi_project_manager.api.list_backups import ListBackups
from usr.plugins.multi_project_manager.api.project_sections import ProjectSections
from usr.plugins.multi_project_manager.api.undo_backup import UndoBackup
from usr.plugins.multi_project_manager.api.projects_overview import ProjectsOverview
from usr.plugins.multi_project_manager.api.propagate_sections import PropagateSections
from usr.plugins.multi_project_manager.helpers import sections

SECRET_VALUE = "s3cret-value-123"
API_DIR = Path(__file__).resolve().parent.parent / "api"
HANDLERS = {
    "projects_overview": ProjectsOverview,
    "project_sections": ProjectSections,
    "duplicate_project": DuplicateProject,
    "propagate_sections": PropagateSections,
    "list_backups": ListBackups,
    "undo_backup": UndoBackup,
    "delete_backup": DeleteBackup,
}


def call(handler_cls, payload):
    return asyncio.run(handler_cls(None, None).process(payload, None))


def status_and_text(response):
    return response.status_code, response.get_data(as_text=True)


@pytest.fixture
def base(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(sections, "_notify_project_changed", lambda name: None)
    root = tmp_path / "usr" / "projects"
    root.mkdir(parents=True)
    return root


def make(root: Path, name, *, variables="", secrets_text="", description=""):
    meta = root / name / ".a0proj"
    meta.mkdir(parents=True)
    (meta / "project.json").write_text(
        json.dumps({"title": name.title(), "description": description, "instructions": ""}),
        encoding="utf-8",
    )
    (meta / "variables.env").write_text(variables, encoding="utf-8")
    if secrets_text:
        (meta / "secrets.env").write_text(secrets_text, encoding="utf-8")


@pytest.mark.parametrize("file_name, handler_cls", HANDLERS.items())
def test_each_file_exposes_exactly_its_handler_with_secure_defaults(file_name, handler_cls):
    classes = load_classes_from_file(str(API_DIR / f"{file_name}.py"), ApiHandler)
    assert [cls.__name__ for cls in classes] == [handler_cls.__name__]
    assert handler_cls.requires_auth() is True
    assert handler_cls.requires_csrf() is True
    assert handler_cls.requires_api_key() is False
    assert handler_cls.requires_loopback() is False
    assert handler_cls.get_methods() == ["POST"]


def test_overview_lists_projects_sections_and_defaults(base):
    make(base, "alpha")
    make(base, "beta")
    data = call(ProjectsOverview, {})
    assert data["ok"] is True
    assert [p["name"] for p in data["projects"]] == ["alpha", "beta"]
    assert data["sections"] == ["description", "instructions", "variables", "secrets"]
    assert data["duplicate_defaults"]["memory"] is False
    assert data["duplicate_defaults"]["secrets"] is True


def test_project_sections_masks_secrets(base):
    make(base, "alpha", variables="A=1\n", secrets_text=f'TOKEN="{SECRET_VALUE}"\n')
    data = call(ProjectSections, {"name": "alpha"})
    assert data["sections"]["variables"] == "A=1\n"
    assert SECRET_VALUE not in json.dumps(data)


def test_project_sections_rejects_bad_names(base):
    make(base, "alpha")
    for name in ("../alpha", "/etc", "ghost", None):
        status, _ = status_and_text(call(ProjectSections, {"name": name}))
        assert status == 400


def test_duplicate_success_and_collision(base):
    make(base, "alpha")
    data = call(DuplicateProject, {"source": "alpha", "title": "Alpha Copy"})
    assert data["ok"] is True and data["name"] == "alpha_copy"
    status, text = status_and_text(call(DuplicateProject, {"source": "alpha", "title": "alpha copy"}))
    assert status == 400 and "already exists" in text


def test_propagate_success_with_source_edits(base):
    make(base, "src", description="old")
    make(base, "t1")
    data = call(
        PropagateSections,
        {
            "source": "src",
            "targets": ["t1"],
            "sections": ["description"],
            "source_edits": {"description": "edited in plugin"},
        },
    )
    assert data["ok"] is True
    assert data["results"] == [{"project": "t1", "ok": True, "sections": ["description"]}]
    assert isinstance(data["backup_id"], str)
    for name in ("src", "t1"):
        header = json.loads((base / name / ".a0proj" / "project.json").read_text(encoding="utf-8"))
        assert header["description"] == "edited in plugin"


def test_invalid_request_does_not_save_source_edits(base):
    make(base, "src", description="original")
    status, _ = status_and_text(
        call(
            PropagateSections,
            {
                "source": "src",
                "targets": ["ghost"],
                "sections": ["description"],
                "source_edits": {"description": "should not be saved"},
            },
        )
    )
    assert status == 400
    header = json.loads((base / "src" / ".a0proj" / "project.json").read_text(encoding="utf-8"))
    assert header["description"] == "original"


def test_propagate_reports_partial_failure_without_secrets(base, monkeypatch):
    make(base, "src", variables="A=new\n", secrets_text=f'K="{SECRET_VALUE}"\n')
    make(base, "bad", variables="A=old\n")
    make(base, "good", variables="A=old\n")
    real_write = sections._write_section

    def flaky(name, section, text):
        if name == "bad":
            raise RuntimeError(f"boom {SECRET_VALUE}")
        return real_write(name, section, text)

    monkeypatch.setattr(sections, "_write_section", flaky)
    data = call(
        PropagateSections,
        {"source": "src", "targets": ["bad", "good"], "sections": ["variables", "secrets"]},
    )
    assert data["ok"] is False
    assert {r["project"]: r["ok"] for r in data["results"]} == {"bad": False, "good": True}
    assert SECRET_VALUE not in json.dumps(data)


# ---------------------------------------------------------------- backups

def propagate_secret(base):
    make(base, "src", secrets_text=f'K="{SECRET_VALUE}"\n', variables="A=new\n")
    make(base, "t1", secrets_text='K="old-value-1234"\n', variables="A=old\n")
    return call(
        PropagateSections,
        {"source": "src", "targets": ["t1"], "sections": ["variables", "secrets"]},
    )["backup_id"]


def test_list_backups_shows_names_and_sections_but_no_text(base):
    backup_id = propagate_secret(base)
    data = call(ListBackups, {})
    assert data["ok"] is True and data["max_backups"] == 20
    (item,) = data["backups"]
    assert item["id"] == backup_id and item["source_title"] == "Src"
    assert item["changes"] == [
        {"project": "t1", "title": "T1", "exists": True, "role": "target",
         "sections": ["variables", "secrets"], "restored": False}
    ]
    dumped = json.dumps(data)
    assert SECRET_VALUE not in dumped and "old-value-1234" not in dumped


def test_undo_backup_restores_and_reports(base):
    backup_id = propagate_secret(base)
    data = call(UndoBackup, {"id": backup_id})
    assert data["ok"] is True and data["undone"] is True and data["has_conflicts"] is False
    assert (base / "t1" / ".a0proj" / "variables.env").read_text(encoding="utf-8") == "A=old\n"
    assert "old-value-1234" not in json.dumps(data)


def test_undo_backup_reports_conflicts_and_force_resolves_them(base):
    backup_id = propagate_secret(base)
    (base / "t1" / ".a0proj" / "variables.env").write_text("A=later\n", encoding="utf-8")
    first = call(UndoBackup, {"id": backup_id})
    assert first["ok"] is False and first["has_conflicts"] is True and first["undone"] is False
    second = call(UndoBackup, {"id": backup_id, "force": True})
    assert second["ok"] is True and second["undone"] is True


@pytest.mark.parametrize("payload", [{"id": "../x"}, {"id": None}, {"id": "20260101T000000000000Z-abcdef"}])
def test_undo_backup_rejects_bad_or_unknown_ids(base, payload):
    status, _ = status_and_text(call(UndoBackup, payload))
    assert status == 400


def test_undo_backup_requires_a_boolean_force(base):
    backup_id = propagate_secret(base)
    status, text = status_and_text(call(UndoBackup, {"id": backup_id, "force": "yes"}))
    assert status == 400 and "force" in text


def test_delete_backup_one_all_and_unknown(base):
    first = propagate_secret(base)
    assert call(DeleteBackup, {"id": first}) == {"ok": True, "deleted": 1}
    assert call(ListBackups, {})["backups"] == []
    status, text = status_and_text(call(DeleteBackup, {"id": first}))
    assert status == 400 and "not found" in text
    status, _ = status_and_text(call(DeleteBackup, {"id": "../etc/passwd"}))
    assert status == 400
    propagate_secret_again = call(
        PropagateSections,
        {"source": "src", "targets": ["t1"], "sections": ["description"], "source_edits": {"description": "x"}},
    )
    assert propagate_secret_again["backup_id"]
    assert call(DeleteBackup, {"all": True}) == {"ok": True, "deleted": 1}
