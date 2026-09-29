from helpers import projects
from helpers.api import ApiHandler, Input, Output, Request
from usr.plugins.multi_project_manager.helpers import backups


class ListBackups(ApiHandler):
    """List the backups taken before propagations, newest first. Names only, never any text."""

    async def process(self, input: Input, request: Request) -> Output:
        titles = {p["name"]: p.get("title") or p["name"] for p in projects.get_active_projects_list()}
        items = []
        for record in backups.list_records():
            item = backups.summarize(record)
            item["source_title"] = titles.get(item["source"], item["source"])
            for change in item["changes"]:
                change["title"] = titles.get(change["project"], change["project"])
                change["exists"] = change["project"] in titles
            items.append(item)
        return {"ok": True, "backups": items, "max_backups": backups.MAX_BACKUPS}
