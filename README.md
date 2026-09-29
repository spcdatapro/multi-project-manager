# Multi-Project Manager

An [Agent Zero](https://github.com/agent0ai/agent-zero) plugin to manage several projects at once.
It saves time when you keep many projects in sync:

- **Edit & propagate**: change a project's description, instructions, non-sensitive variables or
  sensitive variables, and copy the sections you choose to any set of other projects.
- **Duplicate**: create a new project from an existing one under a new name, choosing exactly what
  to copy (instruction files, variables, secrets, MCP servers, skills, knowledge, plugin and agent
  settings, memory, workspace files).
- **History**: every propagation is backed up first, so a mistake is one click to undo.

No external services, no network calls, no extra dependencies.

## Install

In Agent Zero open **Plugins → Install**, choose **Git** and use:

```text
https://github.com/spcdatapro/multi-project-manager
```

Then open it from the sidebar quick-actions menu (**Multi-Project Manager**) or from
**Plugins → Multi-Project Manager → Open**.

Tested with Agent Zero v2.13.

## Usage

### Edit & propagate

1. Pick the **source project**. Its four sections appear and can be edited in place.
2. Tick the sections to copy. For the two variable sections choose **Merge** or **Replace**:
   - **Merge**: keys from the source are added or overwritten; keys that only exist in the target
     are kept, in their original order, with their comments.
   - **Replace**: the target becomes identical to the source (keys only in the target are deleted).
3. Tick the **target projects** and press **Propagate…**. A confirmation lists exactly what will
   change. Your edits to the source, if any, are saved first.

Notes:
- Propagation **overwrites** those sections in the targets. That is what History is for.
- Secret values are never shown in the UI (they appear as `***`); they are copied on the server.
- Close the *Edit project* window of a target before propagating: saving it afterwards would
  overwrite the propagated values.

### Duplicate

Pick the project, type the new title (the folder name is derived from it, like in the native
"Create project" dialog) and tick what to copy. Memory and workspace files (including `.git`) are
off by default because they can be large. Chats are never copied. Duplicating has no backup: delete
the new project to undo it.

### History (backups and undo)

Before anything is written, the previous text of every section that will change (in each target,
and in the source if you edited it) is saved. **History → Undo** restores it. If a project was
edited after the propagation, Undo tells you and lets you keep those later edits or restore anyway.
The last 20 operations are kept.

Backups contain the previous text of the sections, **secret values included**, in
`usr/multi_project_manager/backups/` on the Agent Zero server (owner-only permissions). Delete them
from the History tab whenever you want.

## Uninstall

Delete the plugin from the Plugins dialog. Uninstalling also deletes its backups, so no secret
values are left behind. Your projects are never touched. If you delete the folder by hand instead,
remove `usr/multi_project_manager/` yourself.

## Development

The plugin lives in `usr/plugins/multi_project_manager/`. Tests use `pytest` and run with the
framework's Python, from the Agent Zero root:

```bash
python -m pytest usr/plugins/multi_project_manager/tests -q
```

## License

MIT, see [LICENSE](LICENSE).
