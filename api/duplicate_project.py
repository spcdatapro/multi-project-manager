from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.multi_project_manager.helpers import duplicate, sections


class DuplicateProject(ApiHandler):
    """Duplicate a project under a new title. Input: source, title, options."""

    async def process(self, input: Input, request: Request) -> Output:
        try:
            result = duplicate.duplicate_project(
                input.get("source"), input.get("title"), input.get("options")
            )
        except sections.SectionError as error:
            return Response(str(error), 400)
        return {"ok": True, **result}
