from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.multi_project_manager.helpers import sections


class ProjectSections(ApiHandler):
    """Return one project's sections for editing. Secret values are masked (`***`)."""

    async def process(self, input: Input, request: Request) -> Output:
        try:
            name = sections.require_project(input.get("name"))
            return {"ok": True, "name": name, "sections": sections.read_sections_for_ui(name)}
        except sections.SectionError as error:
            return Response(str(error), 400)
