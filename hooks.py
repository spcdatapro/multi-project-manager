"""Lifecycle hooks (run in the Agent Zero framework runtime).

The plugin needs no setup or dependencies, so there is no install(). It does own data outside
its folder: the backups taken before propagations (usr/multi_project_manager/backups), which hold
previous section texts, secret values included. They are removed on uninstall so no secrets are
left behind. Project data is never touched.
"""

import os


def uninstall():
    from usr.plugins.multi_project_manager.helpers import backups

    backups.delete_all()
    folder = backups.backups_dir()
    for path in (folder, os.path.dirname(folder)):
        try:
            os.rmdir(path)  # only if empty: never removes anything we do not own
        except OSError:
            pass
