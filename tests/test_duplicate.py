import json
from pathlib import Path

import pytest

from helpers import files, projects
from usr.plugins.multi_project_manager.helpers import duplicate
from usr.plugins.multi_project_manager.helpers.sections import SectionError


@pytest.fixture
def base(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    projects_dir = tmp_path / "usr" / "projects"
    projects_dir.mkdir(parents=True)
    src = projects_dir / "src"
    meta = src / ".a0proj"
    for rel, content in {
        "project.json": json.dumps(
            {"title": "Source", "description": "D", "instructions": "I", "color": "#abc",
             "git_url": "https://example.com/r.git"}
        ),
        "variables.env": "A=1\n",
        "secrets.env": 'TOKEN="hush-1234"\n',
        "mcp_servers.json": '{"mcpServers": {}}',
        "instructions/rules.md": "rules",
        "knowledge/k.txt": "know",
        "skills/s/SKILL.md": "skill",
        "plugins/_model_config/config.json": "{}",
        "agents/p/plugins/x/config.json": "{}",
        "agents.json": "{}",
        "memory/index.faiss": "mem",
        "future_feature/data.txt": "future",
    }.items():
        path = meta / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (src / "README.md").write_text("workspace", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("git", encoding="utf-8")
    return projects_dir


def rel_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_defaults_copy_config_but_not_memory_or_workspace(base):
    result = duplicate.duplicate_project("src", "My Copy")
    assert result["name"] == "my_copy"
    assert "memory" in result["skipped"] and "workspace_files" in result["skipped"]
    copied = rel_files(base / "my_copy")
    assert ".a0proj/variables.env" in copied
    assert ".a0proj/secrets.env" in copied
    assert ".a0proj/instructions/rules.md" in copied
    assert ".a0proj/plugins/_model_config/config.json" in copied
    assert ".a0proj/agents/p/plugins/x/config.json" in copied
    assert ".a0proj/agents.json" in copied
    assert ".a0proj/future_feature/data.txt" in copied
    assert ".a0proj/memory/index.faiss" not in copied
    assert "README.md" not in copied and ".git/config" not in copied


def test_header_gets_new_title_and_git_url_cleared_without_workspace(base):
    duplicate.duplicate_project("src", "My Copy")
    header = json.loads((base / "my_copy" / ".a0proj" / "project.json").read_text(encoding="utf-8"))
    assert header["title"] == "My Copy"
    assert header["git_url"] == ""
    assert header["description"] == "D" and header["color"] == "#abc"
    original = json.loads((base / "src" / ".a0proj" / "project.json").read_text(encoding="utf-8"))
    assert original["title"] == "Source" and original["git_url"] == "https://example.com/r.git"


@pytest.mark.parametrize(
    "option, missing",
    [
        ("instruction_files", ".a0proj/instructions/rules.md"),
        ("variables", ".a0proj/variables.env"),
        ("secrets", ".a0proj/secrets.env"),
        ("mcp_servers", ".a0proj/mcp_servers.json"),
        ("skills", ".a0proj/skills/s/SKILL.md"),
        ("knowledge", ".a0proj/knowledge/k.txt"),
        ("plugin_settings", ".a0proj/plugins/_model_config/config.json"),
    ],
)
def test_unchecking_an_option_leaves_out_only_that_data(base, option, missing):
    duplicate.duplicate_project("src", "Baseline")
    duplicate.duplicate_project("src", "Copy", {option: False})
    baseline = rel_files(base / "baseline")
    copied = rel_files(base / "copy")
    removed = baseline - copied
    assert missing in removed
    controlled = tuple(f".a0proj/{entry}" for entry in duplicate.OPTION_ENTRIES[option])
    for path in removed:  # nothing outside this option's entries may disappear
        assert any(path == entry or path.startswith(entry + "/") for entry in controlled), path
    assert ".a0proj/future_feature/data.txt" in copied
    assert ".a0proj/project.json" in copied


def test_plugin_settings_option_covers_agents_and_agents_json(base):
    duplicate.duplicate_project("src", "Copy", {"plugin_settings": False})
    copied = rel_files(base / "copy")
    assert ".a0proj/agents/p/plugins/x/config.json" not in copied
    assert ".a0proj/agents.json" not in copied


def test_memory_and_workspace_can_be_included(base):
    outside = base.parent.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (base / "src" / "link.txt").symlink_to(outside)
    duplicate.duplicate_project("src", "Full", {"memory": True, "workspace_files": True})
    copied = rel_files(base / "full")
    assert ".a0proj/memory/index.faiss" in copied
    assert "README.md" in copied and ".git/config" in copied
    assert (base / "full" / "link.txt").is_symlink()  # copied as a link, never followed
    header = json.loads((base / "full" / ".a0proj" / "project.json").read_text(encoding="utf-8"))
    assert header["git_url"] == "https://example.com/r.git"


def test_slug_matches_the_native_ui_rules():
    assert duplicate.slugify_title("Campaña Pública") == "campana_publica"
    assert duplicate.slugify_title("  My   Project #2 ") == "my_project_2"
    assert duplicate.slugify_title("-a-b-") == "a-b"
    assert duplicate.slugify_title("¿¿??") == ""


def test_name_collision_is_an_error_and_never_renames(base):
    duplicate.duplicate_project("src", "Copy")
    with pytest.raises(SectionError, match="already exists"):
        duplicate.duplicate_project("src", "copy")
    assert sorted(p.name for p in base.iterdir()) == ["copy", "src"]


@pytest.mark.parametrize(
    "source, title, options, message",
    [
        ("src", "   ", None, "Title is required"),
        ("src", "¿¿??", None, "letters or digits"),
        ("ghost", "X", None, "not found"),
        ("../src", "X", None, "Invalid project name"),
        ("src", "X", {"nope": True}, "Unknown option"),
        ("src", "X", {"memory": "yes"}, "true or false"),
        ("src", "X", ["memory"], "must be an object"),
    ],
)
def test_invalid_input(base, source, title, options, message):
    with pytest.raises(SectionError, match=message):
        duplicate.duplicate_project(source, title, options)
    assert [p.name for p in base.iterdir()] == ["src"]


def test_failure_removes_the_partial_copy(base, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(projects, "save_project_header", boom)
    with pytest.raises(RuntimeError):
        duplicate.duplicate_project("src", "Broken")
    assert [p.name for p in base.iterdir()] == ["src"]


def test_a_name_taken_after_the_check_is_never_deleted_by_the_cleanup(base, monkeypatch):
    """Two requests with the same title: the loser must not remove the winner's project."""
    import os

    real_mkdir = os.mkdir

    def other_request_wins(path, *args, **kwargs):
        if str(path).endswith("/copy"):
            real_mkdir(path)  # the "other request" claims the folder first
            (Path(path) / "precious.txt").write_text("winner's data", encoding="utf-8")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(duplicate.os, "mkdir", other_request_wins)
    with pytest.raises(SectionError, match="already exists"):
        duplicate.duplicate_project("src", "Copy")
    assert (base / "copy" / "precious.txt").read_text(encoding="utf-8") == "winner's data"


def test_concurrent_duplicates_with_the_same_title_create_exactly_one_project(base):
    import threading

    outcomes: list[str] = []

    def run():
        try:
            duplicate.duplicate_project("src", "Twin")
            outcomes.append("ok")
        except SectionError:
            outcomes.append("refused")

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok"] + ["refused"] * 5
    assert (base / "twin" / ".a0proj" / "project.json").is_file()
