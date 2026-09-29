from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.multi_project_manager.helpers import sections


class UndoBackup(ApiHandler):
    """Restore a backup. Input: id, force (overwrite sections changed after the operation)."""

    async def process(self, input: Input, request: Request) -> Output:
        force = input.get("force", False)
        if not isinstance(force, bool):
            return Response("force must be true or false", 400)
        try:
            outcome = sections.undo_backup(input.get("id"), force)
        except sections.SectionError as error:
            return Response(str(error), 400)
        results = outcome["results"]
        return {
            "ok": all(r["ok"] for r in results),
            "results": results,
            "undone": outcome["undone"],
            "has_conflicts": any(r.get("conflicts") for r in results),
        }
