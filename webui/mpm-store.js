import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { closeModal } from "/js/modals.js";
import { showConfirmDialog } from "/js/confirmDialog.js";
import * as notifications from "/components/notifications/notification-store.js";
import { store as projectsStore } from "/components/projects/projects-store.js";

const PLUGIN = "multi_project_manager";
const MODAL_PATH = `/plugins/${PLUGIN}/webui/main.html`;
const TITLE = "Multi-Project Manager";
const MAX_LISTED = 8;

const api = (handler, data) => callJsonApi(`/plugins/${PLUGIN}/${handler}`, data);

function message(error, fallback) {
  const text = String(error?.message || error || "").trim();
  return (text || fallback).slice(0, 300);
}

const NORMAL = notifications.NotificationPriority.NORMAL;

// Toasts and confirm dialogs render their message as HTML (x-html / innerHTML): escape anything
// that comes from projects or from the server (titles, names, error texts).
const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const toastError = (text) => notifications.toastFrontendError(esc(text), TITLE, 6, PLUGIN, NORMAL, true);
const toastSuccess = (text) => notifications.toastFrontendSuccess(esc(text), TITLE, 4, PLUGIN, NORMAL, true);
const toastWarning = (text) => notifications.toastFrontendWarning(esc(text), TITLE, 6, PLUGIN, NORMAL, true);

const DUPLICATE_OPTIONS = [
  { key: "instruction_files", label: "Instruction files", hint: "" },
  { key: "variables", label: "Non-sensitive variables", hint: "" },
  { key: "secrets", label: "Sensitive variables", hint: "Copies the stored secret values into the new project." },
  { key: "mcp_servers", label: "MCP servers", hint: "" },
  { key: "skills", label: "Skills", hint: "" },
  { key: "knowledge", label: "Knowledge files", hint: "" },
  { key: "plugin_settings", label: "Plugin and agent settings", hint: "Per-project configuration of other plugins and agents." },
  { key: "memory", label: "Memory", hint: "Indexed memories of the project. Can be large." },
  { key: "workspace_files", label: "Workspace files", hint: "Every file outside the project settings, including .git. Can be large." },
];

const SECTION_DEFS = [
  { key: "description", label: "Description", rows: 4, env: false, hint: "" },
  { key: "instructions", label: "Instructions", rows: 10, env: false, hint: "" },
  {
    key: "variables", label: "Non-sensitive variables", rows: 7, env: true,
    hint: 'Visible to the LLM. .env format, one KEY="value" per line.',
  },
  {
    key: "secrets", label: "Sensitive variables", rows: 7, env: true,
    hint: 'Values are masked as ***. Keep KEY="***" to keep the stored value; type a value to replace it.',
  },
];

const MODE_TEXT = {
  merge: "merge: keys from the source are added or overwritten; other keys in the targets are kept",
  replace: "replace: targets end up identical to the source; keys that only exist in the targets are deleted",
};

const emptyFlags = (value) => Object.fromEntries(SECTION_DEFS.map((s) => [s.key, value]));

const model = {
  tab: "propagate", // "propagate" | "duplicate"
  loading: false,
  projects: [],
  duplicateOptions: DUPLICATE_OPTIONS,
  duplicateDefaults: {},
  dup: { source: "", title: "", options: {}, openAfter: true, busy: false },
  sectionDefs: SECTION_DEFS,
  history: { items: [], max: 20, loading: false, busy: false },
  prop: {
    source: "", loadingSections: false, busy: false,
    original: {}, edit: {}, include: emptyFlags(false),
    modes: { variables: "merge", secrets: "merge" }, targets: {},
  },

  async onOpen() {
    await this.load();
  },

  cleanup() {},

  setTab(name) {
    this.tab = name;
    if (name === "history") this.loadBackups();
  },

  async load() {
    this.loading = true;
    try {
      const response = await api("projects_overview", {});
      this.projects = response.projects || [];
      this.duplicateDefaults = response.duplicate_defaults || {};
      this.resetDuplicate();
      await this.resetPropagate();
    } catch (error) {
      toastError(message(error, "Could not load the projects."));
    } finally {
      this.loading = false;
    }
  },

  projectTitle(name) {
    const project = this.projects.find((p) => p.name === name);
    return project?.title || name;
  },

  // ---------- Duplicate ----------

  resetDuplicate() {
    const source = this.projects.some((p) => p.name === this.dup.source)
      ? this.dup.source
      : this.projects[0]?.name || "";
    this.dup = {
      source,
      title: this.dup.title || "",
      options: { ...this.duplicateDefaults },
      openAfter: this.dup.openAfter !== false,
      busy: false,
    };
  },

  dupFolder() {
    return projectsStore._toFolderName(this.dup.title.trim());
  },

  dupHint() {
    if (!this.dup.title.trim()) return "";
    const folder = this.dupFolder();
    if (!folder) return "The title must contain letters or digits.";
    if (this.projects.some((p) => p.name === folder)) return `A project named "${folder}" already exists.`;
    return "";
  },

  canDuplicate() {
    return !this.dup.busy && !!this.dup.source && !!this.dup.title.trim() && !!this.dupFolder() && !this.dupHint();
  },

  async duplicate() {
    if (!this.canDuplicate()) return;
    this.dup.busy = true;
    try {
      const result = await api("duplicate_project", {
        source: this.dup.source,
        title: this.dup.title.trim(),
        options: this.dup.options,
      });
      toastSuccess(`Project "${result.title}" created as ${result.name}.`);
      const openAfter = this.dup.openAfter;
      this.dup.title = "";
      await this.load();
      if (openAfter) {
        await projectsStore.loadProjectsList();
        await closeModal(MODAL_PATH);
        await projectsStore.openEditModal(result.name);
      }
    } catch (error) {
      toastError(message(error, "Could not duplicate the project."));
    } finally {
      this.dup.busy = false;
    }
  },

  // ---------- Edit & propagate ----------

  async resetPropagate() {
    const keep = this.projects.some((p) => p.name === this.prop.source);
    this.prop.source = keep ? this.prop.source : this.projects[0]?.name || "";
    const others = this.propTargetProjects().map((p) => p.name);
    this.prop.targets = Object.fromEntries(others.map((name) => [name, !!this.prop.targets[name]]));
    await this.loadSourceSections();
  },

  async onSourceChanged() {
    const others = this.propTargetProjects().map((p) => p.name);
    this.prop.targets = Object.fromEntries(others.map((name) => [name, !!this.prop.targets[name]]));
    await this.loadSourceSections();
  },

  // `silent` keeps the current sections on screen while resyncing (no collapse, no scroll jump).
  async loadSourceSections(silent = false) {
    const source = this.prop.source;
    if (!silent) {
      this.prop.original = {};
      this.prop.edit = {};
    }
    if (!source) return;
    if (!silent) this.prop.loadingSections = true;
    try {
      const response = await api("project_sections", { name: source });
      if (this.prop.source !== source) return; // the user picked another source meanwhile
      this.prop.original = { ...response.sections };
      this.prop.edit = { ...response.sections };
    } catch (error) {
      toastError(message(error, "Could not load the project sections."));
    } finally {
      if (!silent && this.prop.source === source) this.prop.loadingSections = false;
    }
  },

  propTargetProjects() {
    return this.projects.filter((p) => p.name !== this.prop.source);
  },

  setAllTargets(value) {
    for (const name of Object.keys(this.prop.targets)) this.prop.targets[name] = value;
  },

  selectedTargets() {
    return this.propTargetProjects().filter((p) => this.prop.targets[p.name]);
  },

  selectedSections() {
    return SECTION_DEFS.filter((s) => this.prop.include[s.key]);
  },

  isEdited(key) {
    return (this.prop.edit[key] ?? "") !== (this.prop.original[key] ?? "");
  },

  hasEdits() {
    return SECTION_DEFS.some((s) => this.isEdited(s.key));
  },

  canPropagate() {
    return (
      !this.prop.busy && !this.prop.loadingSections && !!this.prop.source &&
      this.selectedSections().length > 0 && this.selectedTargets().length > 0
    );
  },

  confirmHtml() {
    const sections = this.selectedSections()
      .map((s) => {
        const mode = s.env ? ` <em>(${esc(MODE_TEXT[this.prop.modes[s.key]])})</em>` : "";
        return `<li>${esc(s.label)}${mode}</li>`;
      })
      .join("");
    const targets = this.selectedTargets();
    const listed = targets.slice(0, MAX_LISTED).map((p) => `<li>${esc(p.title || p.name)}</li>`).join("");
    const more = targets.length > MAX_LISTED ? `<li>and ${targets.length - MAX_LISTED} more…</li>` : "";
    const edits = this.hasEdits()
      ? "<p>Your edits to the source project will be saved first.</p>"
      : "";
    const secrets = this.prop.include.secrets ? "<p>Secret values are copied between projects.</p>" : "";
    return `
      <p>Copy these sections from <strong>${esc(this.projectTitle(this.prop.source))}</strong>:</p>
      <ul>${sections}</ul>
      <p>To ${targets.length} project${targets.length === 1 ? "" : "s"}:</p>
      <ul>${listed}${more}</ul>
      <p><strong>Existing values in those sections will be overwritten. This cannot be undone.</strong></p>
      ${edits}${secrets}
      <p>Close the Edit project window of any target before continuing; saving it afterwards would undo this change.</p>`;
  },

  async propagate() {
    if (!this.canPropagate()) return;
    const confirmed = await showConfirmDialog({
      title: "Propagate sections",
      message: this.confirmHtml(),
      type: "warning",
      confirmText: "Propagate",
      cancelText: "Cancel",
    });
    if (!confirmed) return;

    this.prop.busy = true;
    let reachedServer = false;
    try {
      const sections = this.selectedSections().map((s) => s.key);
      const sourceEdits = Object.fromEntries(
        SECTION_DEFS.filter((s) => this.isEdited(s.key)).map((s) => [s.key, this.prop.edit[s.key] ?? ""])
      );
      const response = await api("propagate_sections", {
        source: this.prop.source,
        targets: this.selectedTargets().map((p) => p.name),
        sections,
        modes: { ...this.prop.modes },
        source_edits: sourceEdits,
      });
      reachedServer = true;
      const failed = (response.results || []).filter((r) => !r.ok);
      const done = (response.results || []).length - failed.length;
      if (done > 0) {
        const undo = response.backup_id ? " You can undo it from the History tab." : "";
        toastSuccess(`Updated ${done} project${done === 1 ? "" : "s"}.${undo}`);
      }
      if (response.backup_id) this.loadBackups(true);
      for (const result of failed.slice(0, 5)) {
        toastError(`${this.projectTitle(result.project)}: ${result.error}`);
      }
      if (failed.length > 5) toastWarning(`${failed.length - 5} more projects failed.`);
    } catch (error) {
      toastError(message(error, "Could not propagate the sections."));
    } finally {
      this.prop.busy = false;
      // Resync only after a successful call (edits are saved now). On a rejected request keep
      // what the user typed so nothing is lost.
      if (reachedServer) await this.loadSourceSections(true);
    }
  },

  // ---------- History (backups and undo) ----------

  sectionLabel(key) {
    return SECTION_DEFS.find((s) => s.key === key)?.label || key;
  },

  formatDate(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? String(iso || "") : date.toLocaleString();
  },

  pendingChanges(backup) {
    return backup.changes.filter((c) => !c.restored);
  },

  async loadBackups(silent = false) {
    if (!silent) this.history.loading = true;
    try {
      const response = await api("list_backups", {});
      this.history.items = response.backups || [];
      this.history.max = response.max_backups || 20;
    } catch (error) {
      toastError(message(error, "Could not load the history."));
    } finally {
      this.history.loading = false;
    }
  },

  undoHtml(backup) {
    const rows = this.pendingChanges(backup)
      .map((c) => {
        const labels = c.sections.map((k) => esc(this.sectionLabel(k))).join(", ");
        const role = c.role === "source" ? " (source project)" : "";
        return `<li>${esc(c.title)}${role}: ${labels}</li>`;
      })
      .join("");
    return `
      <p>Restore these projects to how they were before <strong>${esc(this.formatDate(backup.created_at))}</strong>:</p>
      <ul>${rows}</ul>
      <p><strong>The current values of those sections will be overwritten.</strong></p>`;
  },

  async undo(backup) {
    if (this.history.busy || backup.undone) return;
    const confirmed = await showConfirmDialog({
      title: "Undo propagation",
      message: this.undoHtml(backup),
      type: "warning",
      confirmText: "Restore",
      cancelText: "Cancel",
    });
    if (!confirmed) return;
    this.history.busy = true;
    try {
      let response = await api("undo_backup", { id: backup.id });
      if (response.has_conflicts) {
        const conflicted = response.results.filter((r) => r.conflicts?.length);
        const list = conflicted
          .map((r) => `<li>${esc(this.projectTitle(r.project))}: ${r.conflicts.map((k) => esc(this.sectionLabel(k))).join(", ")}</li>`)
          .join("");
        const force = await showConfirmDialog({
          title: "Changed since then",
          message: `<p>These sections were edited after the propagation:</p><ul>${list}</ul>
            <p><strong>Restoring them will discard those later edits.</strong> Restore anyway?</p>`,
          type: "danger",
          confirmText: "Restore anyway",
          cancelText: "Keep them",
        });
        const restoredNow = response.results.filter((r) => r.ok).length;
        if (force) {
          const second = await api("undo_backup", { id: backup.id, force: true });
          response = { ...second, results: [...response.results.filter((r) => r.ok), ...second.results] };
        } else if (restoredNow > 0) {
          toastSuccess(`Restored ${restoredNow} project${restoredNow === 1 ? "" : "s"}. The others were left as they are.`);
          response = null;
        } else {
          response = null;
        }
      }
      if (response) {
        const failed = response.results.filter((r) => !r.ok);
        const restored = response.results.length - failed.length;
        if (restored > 0) toastSuccess(`Restored ${restored} project${restored === 1 ? "" : "s"}.`);
        for (const result of failed.slice(0, 5)) toastError(`${this.projectTitle(result.project)}: ${result.error}`);
      }
    } catch (error) {
      toastError(message(error, "Could not restore the backup."));
    } finally {
      this.history.busy = false;
      await this.loadBackups(true);
      await this.loadSourceSections(true); // the source project may have been restored too
    }
  },

  async deleteBackup(backup) {
    if (this.history.busy) return;
    const confirmed = await showConfirmDialog({
      title: "Delete backup",
      message: "<p>You will no longer be able to undo this propagation.</p>",
      type: "warning",
      confirmText: "Delete",
      cancelText: "Cancel",
    });
    if (!confirmed) return;
    await this.runDelete({ id: backup.id }, "Backup deleted.");
  },

  async deleteAllBackups() {
    if (this.history.busy || this.history.items.length === 0) return;
    const confirmed = await showConfirmDialog({
      title: "Delete all backups",
      message: `<p>Deletes all ${this.history.items.length} backups, including any secret values they hold. You will no longer be able to undo those propagations.</p>`,
      type: "danger",
      confirmText: "Delete all",
      cancelText: "Cancel",
    });
    if (!confirmed) return;
    await this.runDelete({ all: true }, "All backups deleted.");
  },

  async runDelete(payload, doneText) {
    this.history.busy = true;
    try {
      await api("delete_backup", payload);
      toastSuccess(doneText);
    } catch (error) {
      toastError(message(error, "Could not delete the backup."));
    } finally {
      this.history.busy = false;
      await this.loadBackups(true);
    }
  },
};

export const store = createStore("multiProjectManager", model);
