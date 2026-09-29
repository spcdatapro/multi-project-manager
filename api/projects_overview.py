from helpers import projects
from helpers.api import ApiHandler, Input, Output, Request
from usr.plugins.multi_project_manager.helpers import duplicate, sections


class ProjectsOverview(ApiHandler):
    """List projects plus the section names and duplicate option defaults the UI needs."""

    async def process(self, input: Input, request: Request) -> Output:
        return {
            "ok": True,
            "projects": projects.get_active_projects_list(),
            "sections": list(sections.SECTIONS),
            "duplicate_defaults": duplicate.DEFAULT_OPTIONS,
        }
