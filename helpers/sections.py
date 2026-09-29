"""Read, edit, propagate and undo project sections.

Sections: description, instructions (project.json), variables (variables.env)
and secrets (secrets.env). Raw secret values only exist inside this module, the
backup files and the project files; anything meant for the UI goes through
`read_sections_for_ui`, which masks them.
"""

from __future__ import annotations

import functools
import os
import threading
from datetime import datetime, timezone
from typing import Any

from helpers import projects, secrets
from helpers.print_style import PrintStyle

from usr.plugins.multi_project_manager.helpers import backups, env_merge
from usr.plugins.multi_project_manager.helpers.errors import SectionError

__all__ = ["SectionError"]

DESCRIPTION = "description"
INSTRUCTIONS = "instructions"
VARIABLES = "variables"
SECRETS = "secrets"
SECTIONS = (DESCRIPTION, INSTRUCTIONS, VARIABLES, SECRETS)
ENV_SECTIONS = (VARIABLES, SECRETS)
_HEADER_SECTIONS = (DESCRIPTION, INSTRUCTIONS)
BACKUP_FAILED = "Could not create the backup, so the target projects were not changed."

# Requests run on worker threads: one write operation at a time, so backups and writes of two
# propagations / undos / duplicates can never interleave.
OPERATION_LOCK = threading.RLock()


def serialized(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with OPERATION_LOCK:
            return func(*args, **kwargs)

    return wrapper


def _notify_project_changed(name: str) -> None:
    projects.reactivate_project_in_chats(name)


def require_project(name: Any) -> str:
    try:
        clean = projects.validate_project_name(name)
    except ValueError as exc:
        raise SectionError("Invalid project name") from exc
    header = projects.get_project_meta(clean, projects.PROJECT_HEADER_FILE)
    if not os.path.isfile(header):
        raise SectionError(f'Project "{clean}" not found')
    return clean


def _clean_sections(sections: Any) -> list[str]:
    if not isinstance(sections, (list, tuple)) or not sections:
        raise SectionError("Select at least one section")
    unknown = [s for s in sections if s not in SECTIONS]
    if unknown:
        raise SectionError(f"Unknown section: {unknown[0]}")
    return [s for s in SECTIONS if s in sections]  # canonical order, deduped


def _secrets_manager(name: str) -> secrets.SecretsManager:
    return secrets.get_project_secrets_manager(name)


def read_sections(name: str, sections: list[str] | None = None) -> dict[str, str]:
    """RAW section texts (secrets unmasked). Backend use only, never return to the UI."""
    wanted = sections or list(SECTIONS)
    result: dict[str, str] = {}
    if any(s in _HEADER_SECTIONS for s in wanted):
        basic = projects.load_basic_project_data(name)
        for s in _HEADER_SECTIONS:
            if s in wanted:
                result[s] = str(basic.get(s, "") or "")
    if VARIABLES in wanted:
        result[VARIABLES] = projects.load_project_variables(name)
    if SECRETS in wanted:
        result[SECRETS] = _secrets_manager(name).read_secrets_raw()
    return result


def read_sections_for_ui(name: str) -> dict[str, str]:
    """All sections with secret values masked as `***`."""
    result = read_sections(name, [DESCRIPTION, INSTRUCTIONS, VARIABLES])
    result[SECRETS] = _secrets_manager(name).get_masked_secrets()
    return result


def _write_section(name: str, section: str, text: str) -> None:
    if section in _HEADER_SECTIONS:
        basic = projects.load_basic_project_data(name)
        basic[section] = text  # type: ignore[literal-required]
        projects.save_project_header(name, basic)
    elif section == VARIABLES:
        projects.save_project_variables(name, text)
    else:
        _secrets_manager(name).save_secrets(text)


def save_source_sections(name: str, submitted: dict[str, Any]) -> list[str]:
    """Save edits made in the UI to the source project; returns the sections saved.

    Secrets use the framework merge: `KEY="***"` keeps the stored value, keys
    omitted are deleted and new keys are added.
    """
    name = require_project(name)
    saved: list[str] = []
    for section in SECTIONS:
        if section not in submitted:
            continue
        text = submitted[section]
        if not isinstance(text, str):
            raise SectionError(f"Section {section} must be text")
        if section == SECRETS:
            projects.save_project_secrets(name, text)
        else:
            _write_section(name, section, text)
        saved.append(section)
    if saved:
        _notify_project_changed(name)
    return saved


def _compute(section: str, source_text: str, target_text: str, mode: str) -> str:
    if section in ENV_SECTIONS:
        return env_merge.apply_mode(mode, source_text, target_text, upper_keys=(section == SECRETS))
    return source_text


def _error_result(project: str, exc: Exception, what: str) -> dict[str, Any]:
    if isinstance(exc, SectionError):
        return {"project": project, "ok": False, "error": str(exc)}
    # never echo str(exc): it could quote file contents
    PrintStyle.error(f"multi_project_manager: {what} {project} failed ({type(exc).__name__})")
    return {"project": project, "ok": False, "error": f"Unexpected error ({type(exc).__name__})"}


def propagate(
    source: str,
    targets: list[str],
    sections: list[str],
    modes: dict[str, str] | None = None,
    source_edits: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Like `run_propagation` but returns only the per-target results."""
    return run_propagation(source, targets, sections, modes, source_edits)["results"]


@serialized
def run_propagation(
    source: str,
    targets: list[str],
    sections: list[str],
    modes: dict[str, str] | None = None,
    source_edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy `sections` of `source` to every target. Returns {"results": [...], "backup_id": ...}.

    `modes` maps variables/secrets to `merge` (default) or `replace`.
    `source_edits` are UI edits saved to the source first (see save_source_sections).
    Order: validate everything -> back up what will be overwritten -> write. Input
    problems raise SectionError before anything is written, and so does a backup that
    cannot be stored. A failure on one target restores what was already written to it
    and does not stop the others. Results never include section contents.
    """
    source = require_project(source)
    chosen = _clean_sections(sections)
    modes = modes or {}
    edits = source_edits or {}
    if not isinstance(edits, dict):
        raise SectionError("Source edits must be an object")
    for key, value in edits.items():
        if key not in SECTIONS:
            raise SectionError(f"Unknown section: {key}")
        if not isinstance(value, str):
            raise SectionError(f"Section {key} must be text")
    for section in ENV_SECTIONS:
        if section in chosen and modes.get(section, env_merge.MODE_MERGE) not in env_merge.MODES:
            raise SectionError(f"Invalid mode for {section}")
    if not isinstance(targets, (list, tuple)) or not targets:
        raise SectionError("Select at least one target project")
    unique_targets: list[str] = []
    for target in targets:
        clean = require_project(target)
        if clean == source:
            raise SectionError("The source project cannot be a target")
        if clean not in unique_targets:
            unique_targets.append(clean)

    used_modes = {s: modes.get(s, env_merge.MODE_MERGE) for s in ENV_SECTIONS if s in chosen}
    backup = backups.Backup(source, chosen, used_modes)

    # 1) the source's own edits: back up first, then save
    if edits:
        edited = [s for s in SECTIONS if s in edits]
        backup.add_entry(source, read_sections(source, edited), role="source")
        try:
            backup.save()
        except OSError:
            raise SectionError("Could not create the backup, so nothing was changed.") from None
        try:
            save_source_sections(source, edits)
        except Exception:
            backups.delete(backup.id)  # nothing was saved: drop the useless backup
            raise

    # 2) plan every target (reads only)
    source_texts = read_sections(source, chosen)
    plans: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    outcome: dict[str, dict[str, Any]] = {}
    for target in unique_targets:
        try:
            previous = read_sections(target, chosen)
            new_texts = {
                s: _compute(s, source_texts[s], previous[s], modes.get(s, env_merge.MODE_MERGE)) for s in chosen
            }
            plans[target] = (previous, new_texts)
        except Exception as exc:
            outcome[target] = _error_result(target, exc, "reading")

    # 3) back up everything that will really change, before the first write
    for target, (previous, new_texts) in plans.items():
        changed = [s for s in chosen if new_texts[s] != previous[s]]
        if changed:
            backup.add_entry(target, {s: previous[s] for s in changed})
    if any(entry["role"] == "target" for entry in backup.entries):
        try:
            backup.save()
        except OSError:
            raise SectionError(BACKUP_FAILED) from None

    # 4) write
    failed: set[str] = set()
    for target, (previous, new_texts) in plans.items():
        try:
            _write_target(target, chosen, previous, new_texts)
            outcome[target] = {"project": target, "ok": True, "sections": chosen}
        except Exception as exc:
            failed.add(target)
            outcome[target] = _error_result(target, exc, "propagating to")

    # 5) finalize the backup: drop rolled-back targets, record what is on disk now
    backup_id = _finalize_backup(backup, failed)
    return {"results": [outcome[t] for t in unique_targets], "backup_id": backup_id}


def _finalize_backup(backup: backups.Backup, failed: set[str]) -> str | None:
    backup.record["entries"] = [e for e in backup.entries if e["project"] not in failed]
    if not backup.entries:
        backups.delete(backup.id)
        return None
    for entry in backup.entries:
        try:
            current = read_sections(entry["project"], list(entry["previous"]))
            entry["after"] = {s: backups.sha(current[s]) for s in entry["previous"]}
        except Exception:
            entry["after"] = {}  # no conflict detection for this entry, undo still works
    try:
        backup.save()
    except OSError:
        PrintStyle.error("multi_project_manager: could not update the backup after writing")
    return backup.id


def _write_target(
    target: str, chosen: list[str], previous: dict[str, str], new_texts: dict[str, str]
) -> None:
    written: list[str] = []
    try:
        for section in chosen:
            if new_texts[section] != previous[section]:
                _write_section(target, section, new_texts[section])
                written.append(section)
        if written:
            _notify_project_changed(target)
    except Exception:
        for section in reversed(written):
            try:
                _write_section(target, section, previous[section])
            except Exception:
                PrintStyle.error(f"multi_project_manager: rollback of {section} on {target} failed")
        raise


@serialized
def undo_backup(backup_id: str, force: bool = False) -> dict[str, Any]:
    """Restore the texts saved in a backup. Returns {"results": [...], "undone": bool}.

    A project whose section changed after the operation is reported with `conflicts` and left
    alone unless `force` is true. Already restored entries are skipped, so a second call with
    `force` only handles what is left.
    """
    record = backups.load(backup_id)
    if record.get("undone_at"):
        raise SectionError("This backup was already restored")
    results = [_restore_entry(entry, force) for entry in record["entries"] if not entry.get("restored")]
    if all(entry.get("restored") for entry in record["entries"]):
        record["undone_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        backups.save_record(record)
    except OSError:
        PrintStyle.error("multi_project_manager: could not update the backup after restoring")
    return {"results": results, "undone": bool(record.get("undone_at"))}


def _restore_entry(entry: dict[str, Any], force: bool) -> dict[str, Any]:
    project = str(entry.get("project", ""))
    previous: dict[str, str] = entry.get("previous") or {}
    keys = [s for s in SECTIONS if s in previous]
    try:
        require_project(project)
        current = read_sections(project, keys)
        after = entry.get("after") or {}
        conflicts = [s for s in keys if after.get(s) and backups.sha(current[s]) != after[s]]
        if conflicts and not force:
            return {
                "project": project, "ok": False, "conflicts": conflicts,
                "error": "Changed after the operation",
            }
        restored: list[str] = []
        try:
            for section in keys:
                if current[section] != previous[section]:
                    _write_section(project, section, previous[section])
                    restored.append(section)
        except Exception:
            for section in reversed(restored):
                try:
                    _write_section(project, section, current[section])
                except Exception:
                    PrintStyle.error(f"multi_project_manager: undo rollback of {section} on {project} failed")
            raise
        if restored:
            _notify_project_changed(project)
        entry["restored"] = True
        return {"project": project, "ok": True, "sections": keys}
    except Exception as exc:
        return _error_result(project, exc, "restoring")
