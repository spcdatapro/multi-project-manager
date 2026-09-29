"""Duplicate a project under a new title / folder name."""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from typing import Any

from helpers import projects

from usr.plugins.multi_project_manager.helpers.sections import SectionError, require_project, serialized

# option -> entries inside .a0proj it controls. Everything else in .a0proj is always copied,
# so data added by future Agent Zero versions or other plugins is not lost.
OPTION_ENTRIES: dict[str, tuple[str, ...]] = {
    "instruction_files": ("instructions",),
    "variables": ("variables.env",),
    "secrets": ("secrets.env",),
    "mcp_servers": (projects.PROJECT_MCP_SERVERS_FILE,),
    "skills": ("skills",),
    "knowledge": ("knowledge",),
    "plugin_settings": ("plugins", "agents", "agents.json"),
    "memory": ("memory",),
}
WORKSPACE_OPTION = "workspace_files"  # everything outside .a0proj, .git included
DEFAULT_OPTIONS: dict[str, bool] = {
    **{key: key != "memory" for key in OPTION_ENTRIES},
    WORKSPACE_OPTION: False,
}


def slugify_title(title: str) -> str:
    """Folder name for a title. Same rules as `_toFolderName` in projects-store.js."""
    s = unicodedata.normalize("NFD", title or "")
    s = re.sub(r"[̀-ͯ]", "", s).lower()
    s = re.sub(r"[^a-z0-9\s-]", "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_{2,}", "_", s)
    s = re.sub(r"^-+|-+$", "", s)
    return re.sub(r"^_+|_+$", "", s)


def _clean_options(options: Any) -> dict[str, bool]:
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise SectionError("Options must be an object")
    unknown = [key for key in options if key not in DEFAULT_OPTIONS]
    if unknown:
        raise SectionError(f"Unknown option: {unknown[0]}")
    if any(not isinstance(value, bool) for value in options.values()):
        raise SectionError("Options must be true or false")
    return {**DEFAULT_OPTIONS, **options}


@serialized
def duplicate_project(source: str, title: str, options: dict[str, bool] | None = None) -> dict[str, Any]:
    source = require_project(source)
    title = str(title or "").strip()
    if not title:
        raise SectionError("Title is required")
    name = slugify_title(title)
    if not name:
        raise SectionError("Title must contain letters or digits")
    opts = _clean_options(options)

    source_folder = projects.get_project_folder(source)
    dest_folder = projects.get_project_folder(name)

    excluded = {
        entry
        for option, entries in OPTION_ENTRIES.items()
        if not opts[option]
        for entry in entries
    }

    def ignore_meta(directory: str, names: list[str]) -> set[str]:
        if os.path.abspath(directory) == os.path.abspath(source_meta):
            return excluded.intersection(names)
        return set()

    source_meta = projects.get_project_meta(source)
    # Atomic claim of the folder: it fails if the name exists (even as a dangling link). From here
    # the folder is ours, so the cleanup below can never delete someone else's project.
    os.makedirs(os.path.dirname(dest_folder), exist_ok=True)
    try:
        os.mkdir(dest_folder)
    except FileExistsError:
        raise SectionError(f'A project named "{name}" already exists') from None
    try:
        if opts[WORKSPACE_OPTION]:
            shutil.copytree(source_folder, dest_folder, symlinks=True, ignore=ignore_meta, dirs_exist_ok=True)
        else:
            shutil.copytree(
                source_meta,
                os.path.join(dest_folder, projects.PROJECT_META_DIR),
                symlinks=True,
                ignore=ignore_meta,
            )
        basic = projects.load_basic_project_data(name)
        basic["title"] = title
        if not opts[WORKSPACE_OPTION]:
            basic["git_url"] = ""
        projects.save_project_header(name, basic)
        projects.create_project_meta_folders(name)
    except Exception:
        shutil.rmtree(dest_folder, ignore_errors=True)  # our own partial copy (claimed above)
        raise
    return {
        "name": name,
        "title": title,
        "copied": [key for key, on in opts.items() if on],
        "skipped": [key for key, on in opts.items() if not on],
    }
