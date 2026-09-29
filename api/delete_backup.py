from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.multi_project_manager.helpers import backups, sections


class DeleteBackup(ApiHandler):
    """Delete one backup (input: id) or all of them (input: all=true). Deleting is permanent."""

    async def process(self, input: Input, request: Request) -> Output:
        if input.get("all") is True:
            return {"ok": True, "deleted": backups.delete_all()}
        try:
            backup_id = input.get("id")
            if not backups.exists(backup_id):
                raise sections.SectionError("Backup not found")
        except sections.SectionError as error:
            return Response(str(error), 400)
        backups.delete(backup_id)
        return {"ok": True, "deleted": 1}
