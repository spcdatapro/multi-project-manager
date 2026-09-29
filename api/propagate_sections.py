from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.multi_project_manager.helpers import sections


class PropagateSections(ApiHandler):
    """Copy sections of a source project to target projects, after backing up what is overwritten.

    Input: source, targets, sections, modes ({variables|secrets: merge|replace}),
    source_edits (optional edits to save to the source first).
    Output: results (one per target) and backup_id (null when nothing changed).
    """

    async def process(self, input: Input, request: Request) -> Output:
        try:
            outcome = sections.run_propagation(
                input.get("source"),
                input.get("targets"),
                input.get("sections"),
                input.get("modes"),
                input.get("source_edits"),
            )
        except sections.SectionError as error:
            return Response(str(error), 400)
        results = outcome["results"]
        return {"ok": all(r["ok"] for r in results), "results": results, "backup_id": outcome["backup_id"]}
