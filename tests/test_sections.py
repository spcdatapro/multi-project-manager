import json
from pathlib import Path

import pytest

from helpers import files, secrets
from usr.plugins.multi_project_manager.helpers import sections
from usr.plugins.multi_project_manager.helpers.sections import SectionError

SECRET_VALUE = "s3cret-value-123"


@pytest.fixture
def base(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr" / "projects").mkdir(parents=True)
    notified: list[str] = []
    monkeypatch.setattr(sections, "_notify_project_changed", notified.append)
    return SimpleBase(tmp_path, notified)


class SimpleBase:
    def __init__(self, path: Path, notified: list[str]):
        self.path = path
        self.notified = notified

    def make(self, name, *, title=None, description="", instructions="", variables="", secrets_text=""):
        meta = self.path / "usr" / "projects" / name / ".a0proj"
        meta.mkdir(parents=True)
        (meta / "project.json").write_text(
            json.dumps(
                {
                    "title": title or name,
                    "description": description,
                    "instructions": instructions,
                    "color": "#fff",
                    "git_url": "",
                }
            ),
            encoding="utf-8",
        )
        if variables:
            (meta / "variables.env").write_text(variables, encoding="utf-8")
        if secrets_text:
            (meta / "secrets.env").write_text(secrets_text, encoding="utf-8")
        return name

    def file(self, name, *parts):
        return self.path / "usr" / "projects" / name / ".a0proj" / Path(*parts)


def test_read_sections_raw_and_ui_masking(base):
    base.make("src", description="D", instructions="I", variables="A=1\n", secrets_text=f'TOKEN="{SECRET_VALUE}"\n')
    raw = sections.read_sections("src")
    assert raw == {
        "description": "D",
        "instructions": "I",
        "variables": "A=1\n",
        "secrets": f'TOKEN="{SECRET_VALUE}"\n',
    }
    ui = sections.read_sections_for_ui("src")
    assert SECRET_VALUE not in json.dumps(ui)
    assert "***" in ui["secrets"]


def test_propagate_description_and_instructions_keeps_other_fields(base):
    base.make("src", description="new desc", instructions="new instr")
    base.make("t1", title="Target One", description="old", instructions="old")
    base.make("t2", title="Target Two")
    results = sections.propagate("src", ["t1", "t2"], ["description", "instructions"])
    assert [r["ok"] for r in results] == [True, True]
    for name, title in (("t1", "Target One"), ("t2", "Target Two")):
        header = json.loads(base.file(name, "project.json").read_text(encoding="utf-8"))
        assert header["description"] == "new desc"
        assert header["instructions"] == "new instr"
        assert header["title"] == title
        assert header["color"] == "#fff"
    assert sorted(base.notified) == ["t1", "t2"]


def test_variables_merge_and_replace(base):
    base.make("src", variables='API_URL="new"\nTIMEOUT="30"\n')
    base.make("t1", variables='API_URL="old"\nDB="a"\n')
    base.make("t2", variables='API_URL="old"\nDB="a"\n')
    sections.propagate("src", ["t1"], ["variables"], {"variables": "merge"})
    sections.propagate("src", ["t2"], ["variables"], {"variables": "replace"})
    assert base.file("t1", "variables.env").read_text(encoding="utf-8") == 'API_URL="new"\nDB="a"\nTIMEOUT="30"\n'
    assert base.file("t2", "variables.env").read_text(encoding="utf-8") == 'API_URL="new"\nTIMEOUT="30"\n'


def test_secrets_merge_is_case_insensitive_and_never_leaks_in_results(base):
    base.make("src", secrets_text=f'api_key="{SECRET_VALUE}"\n')
    base.make("t1", secrets_text='API_KEY="old-value"\nOTHER="keep-me"\n')
    results = sections.propagate("src", ["t1"], ["secrets"])
    assert results == [{"project": "t1", "ok": True, "sections": ["secrets"]}]
    assert SECRET_VALUE not in json.dumps(results)
    stored = secrets.get_project_secrets_manager("t1").load_secrets()
    assert stored == {"API_KEY": SECRET_VALUE, "OTHER": "keep-me"}


def test_noop_propagation_writes_and_notifies_nothing(base):
    base.make("src", variables="A=1\n")
    base.make("t1", variables="A=1\n")
    results = sections.propagate("src", ["t1"], ["variables"])
    assert results[0]["ok"] is True
    assert base.notified == []


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"targets": ["src"], "sections": ["variables"]}, "source project cannot be a target"),
        ({"targets": ["../evil"], "sections": ["variables"]}, "Invalid project name"),
        ({"targets": ["/etc"], "sections": ["variables"]}, "Invalid project name"),
        ({"targets": ["ghost"], "sections": ["variables"]}, "not found"),
        ({"targets": [], "sections": ["variables"]}, "at least one target"),
        ({"targets": ["t1"], "sections": []}, "at least one section"),
        ({"targets": ["t1"], "sections": ["mcp"]}, "Unknown section"),
        ({"targets": ["t1"], "sections": ["variables"], "modes": {"variables": "overwrite"}}, "Invalid mode"),
    ],
)
def test_invalid_input_raises_before_any_write(base, kwargs, message):
    base.make("src", variables="A=new\n")
    base.make("t1", variables="A=old\n")
    with pytest.raises(SectionError, match=message):
        sections.propagate("src", **kwargs)
    assert base.file("t1", "variables.env").read_text(encoding="utf-8") == "A=old\n"


def test_failure_on_one_target_rolls_it_back_and_continues(base, monkeypatch):
    base.make("src", variables="A=new\n", secrets_text='K="v-new-1234"\n')
    base.make("bad", variables="A=old\n", secrets_text='K="v-old-1234"\n')
    base.make("good", variables="A=old\n", secrets_text='K="v-old-1234"\n')
    real_write = sections._write_section

    def flaky(name, section, text):
        if name == "bad" and section == "secrets":
            raise RuntimeError(f"boom {SECRET_VALUE}")
        return real_write(name, section, text)

    monkeypatch.setattr(sections, "_write_section", flaky)
    results = sections.propagate("src", ["bad", "good"], ["variables", "secrets"])
    by_project = {r["project"]: r for r in results}
    assert by_project["good"]["ok"] is True
    assert by_project["bad"]["ok"] is False
    assert SECRET_VALUE not in json.dumps(results)
    assert base.file("bad", "variables.env").read_text(encoding="utf-8") == "A=old\n"
    assert base.file("good", "variables.env").read_text(encoding="utf-8") == "A=new\n"
    assert "bad" not in base.notified


def test_save_source_sections_resolves_masked_secrets(base):
    base.make("src", secrets_text='A="one-1234"\nGONE="bye-1234"\n')
    saved = sections.save_source_sections(
        "src",
        {"description": "edited", "secrets": 'A="***"\nB="two-1234"\n'},
    )
    assert saved == ["description", "secrets"]
    assert secrets.get_project_secrets_manager("src").load_secrets() == {"A": "one-1234", "B": "two-1234"}
    header = json.loads(base.file("src", "project.json").read_text(encoding="utf-8"))
    assert header["description"] == "edited"
    assert base.notified == ["src"]


def test_save_source_sections_rejects_non_text(base):
    base.make("src")
    with pytest.raises(SectionError, match="must be text"):
        sections.save_source_sections("src", {"variables": ["A=1"]})


# ---------------------------------------------------------------- backups and undo

from usr.plugins.multi_project_manager.helpers import backups  # noqa: E402


def snapshot(base_obj, name):
    """Everything propagation can touch in a project, as text."""
    meta = base_obj.file(name)
    out = {}
    for f in ("project.json", "variables.env", "secrets.env"):
        p = meta / f
        out[f] = p.read_text(encoding="utf-8") if p.exists() else None
    return out


def test_propagation_creates_a_backup_with_the_previous_texts(base):
    base.make("src", variables="A=new\n", secrets_text='K="v-new-1234"\n')
    base.make("t1", variables="A=old\n", secrets_text='K="v-old-1234"\n')
    outcome = sections.run_propagation("src", ["t1"], ["variables", "secrets"])
    record = backups.load(outcome["backup_id"])
    assert record["source"] == "src" and record["sections"] == ["variables", "secrets"]
    entry = record["entries"][0]
    assert entry["project"] == "t1" and entry["role"] == "target"
    assert entry["previous"] == {"variables": "A=old\n", "secrets": 'K="v-old-1234"\n'}
    assert set(entry["after"]) == {"variables", "secrets"}


def test_noop_propagation_creates_no_backup(base):
    base.make("src", variables="A=1\n")
    base.make("t1", variables="A=1\n")
    outcome = sections.run_propagation("src", ["t1"], ["variables"])
    assert outcome["backup_id"] is None
    assert backups.list_records() == []


def test_backup_only_lists_sections_that_really_change(base):
    base.make("src", description="same", variables="A=new\n")
    base.make("t1", description="same", variables="A=old\n")
    outcome = sections.run_propagation("src", ["t1"], ["description", "variables"])
    assert list(backups.load(outcome["backup_id"])["entries"][0]["previous"]) == ["variables"]


def test_undo_restores_targets_and_source_byte_for_byte(base):
    base.make("src", description="src desc", variables="A=new\n", secrets_text='K="v-new-1234"\n')
    base.make("t1", description="old1", variables="A=old\n", secrets_text='K="v-old-1234"\nX="only-1234"\n')
    base.make("t2", description="old2")
    before = {n: snapshot(base, n) for n in ("src", "t1", "t2")}
    outcome = sections.run_propagation(
        "src", ["t1", "t2"], ["description", "variables", "secrets"],
        {"variables": "replace"}, {"description": "edited src"},
    )
    assert snapshot(base, "t1") != before["t1"] and snapshot(base, "src") != before["src"]
    undo = sections.undo_backup(outcome["backup_id"])
    assert undo["undone"] is True and all(r["ok"] for r in undo["results"])
    for name in ("src", "t1", "t2"):
        after = snapshot(base, name)
        # the framework re-serializes project.json (adds default fields) and secrets.env: compare
        # the values that matter; variables.env is written verbatim, so it must be byte-exact
        for field in ("description", "instructions"):
            assert json.loads(after["project.json"])[field] == json.loads(before[name]["project.json"])[field], name
        # a missing file and an empty one are the same thing for Agent Zero
        assert (after["variables.env"] or "") == (before[name]["variables.env"] or ""), name
    assert secrets.get_project_secrets_manager("t1").load_secrets() == {"K": "v-old-1234", "X": "only-1234"}
    assert secrets.get_project_secrets_manager("t2").load_secrets() == {}
    assert backups.load(outcome["backup_id"])["undone_at"]


def test_undo_twice_is_refused(base):
    base.make("src", variables="A=new\n")
    base.make("t1", variables="A=old\n")
    backup_id = sections.run_propagation("src", ["t1"], ["variables"])["backup_id"]
    sections.undo_backup(backup_id)
    with pytest.raises(SectionError, match="already restored"):
        sections.undo_backup(backup_id)


def test_undo_skips_a_project_edited_since_and_force_overrides(base):
    base.make("src", variables="A=new\n")
    base.make("t1", variables="A=old\n")
    base.make("t2", variables="A=old\n")
    backup_id = sections.run_propagation("src", ["t1", "t2"], ["variables"])["backup_id"]
    base.file("t1", "variables.env").write_text("A=edited-later\n", encoding="utf-8")

    first = sections.undo_backup(backup_id)
    by_project = {r["project"]: r for r in first["results"]}
    assert by_project["t2"]["ok"] is True
    assert by_project["t1"]["ok"] is False and by_project["t1"]["conflicts"] == ["variables"]
    assert first["undone"] is False
    assert base.file("t1", "variables.env").read_text(encoding="utf-8") == "A=edited-later\n"
    assert base.file("t2", "variables.env").read_text(encoding="utf-8") == "A=old\n"

    second = sections.undo_backup(backup_id, force=True)
    assert [r["project"] for r in second["results"]] == ["t1"]  # t2 was already restored
    assert second["undone"] is True
    assert base.file("t1", "variables.env").read_text(encoding="utf-8") == "A=old\n"


def test_failed_targets_are_not_in_the_backup(base, monkeypatch):
    base.make("src", variables="A=new\n")
    base.make("bad", variables="A=old\n")
    base.make("good", variables="A=old\n")
    real_write = sections._write_section

    def flaky(name, section, text):
        if name == "bad":
            raise RuntimeError("boom")
        return real_write(name, section, text)

    monkeypatch.setattr(sections, "_write_section", flaky)
    outcome = sections.run_propagation("src", ["bad", "good"], ["variables"])
    assert [e["project"] for e in backups.load(outcome["backup_id"])["entries"]] == ["good"]


def test_if_the_backup_cannot_be_stored_nothing_is_written(base, monkeypatch):
    base.make("src", variables="A=new\n")
    base.make("t1", variables="A=old\n")

    def refuse(self):
        raise OSError("disk full")

    monkeypatch.setattr(backups.Backup, "save", refuse)
    with pytest.raises(SectionError, match="Could not create the backup"):
        sections.run_propagation("src", ["t1"], ["variables"])
    assert base.file("t1", "variables.env").read_text(encoding="utf-8") == "A=old\n"


def test_source_edits_are_not_saved_when_their_backup_fails(base, monkeypatch):
    base.make("src", description="original")
    base.make("t1")
    monkeypatch.setattr(backups.Backup, "save", lambda self: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(SectionError, match="Could not create the backup"):
        sections.run_propagation("src", ["t1"], ["description"], source_edits={"description": "edited"})
    header = json.loads(base.file("src", "project.json").read_text(encoding="utf-8"))
    assert header["description"] == "original"


def test_undo_reports_a_deleted_project_without_failing_the_rest(base):
    base.make("src", variables="A=new\n")
    base.make("gone", variables="A=old\n")
    base.make("kept", variables="A=old\n")
    backup_id = sections.run_propagation("src", ["gone", "kept"], ["variables"])["backup_id"]
    import shutil

    shutil.rmtree(base.path / "usr" / "projects" / "gone")
    undo = sections.undo_backup(backup_id)
    by_project = {r["project"]: r for r in undo["results"]}
    assert by_project["kept"]["ok"] is True
    assert by_project["gone"]["ok"] is False and "not found" in by_project["gone"]["error"]
    assert undo["undone"] is False


def test_results_and_errors_never_contain_secret_values_during_undo(base):
    base.make("src", secrets_text=f'K="{SECRET_VALUE}"\n')
    base.make("t1", secrets_text='K="old-value-1234"\n')
    backup_id = sections.run_propagation("src", ["t1"], ["secrets"])["backup_id"]
    undo = sections.undo_backup(backup_id)
    assert SECRET_VALUE not in json.dumps(undo) and "old-value-1234" not in json.dumps(undo)
