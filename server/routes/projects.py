"""Project management endpoints."""

from fastapi import APIRouter, HTTPException, Response, status

from server.db.project_store import (
    create_project,
    delete_project,
    get_project_detail,
    get_project_summary,
    list_projects,
    update_project,
)
from server.models.project import Project, ProjectCreate, ProjectDetail, ProjectSummary, ProjectUpdate
from server.routes.trash import router as trash_router


router = APIRouter(tags=["projects"])
router.include_router(trash_router)


@router.get("/api/projects", response_model=list[ProjectSummary])
async def list_all_projects() -> list[ProjectSummary]:
    """List projects for the dashboard home view."""

    return await list_projects()


@router.post("/api/projects", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(body: ProjectCreate) -> ProjectSummary:
    """Create a new project row."""

    project = Project(name=body.name, description=body.description)
    await create_project(project)
    created = await get_project_summary(project.id)
    if created is None:
        raise HTTPException(500, "Project was created but could not be reloaded")
    return created


@router.get("/api/projects/{project_id}", response_model=ProjectDetail)
async def get_project_endpoint(project_id: str) -> ProjectDetail:
    """Return one project plus its chain list."""

    project = await get_project_detail(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return project


@router.put("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_project_endpoint(project_id: str, body: ProjectUpdate) -> Response:
    """Update project metadata and cover selection."""

    updated = await update_project(project_id, body)
    if updated is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_endpoint(project_id: str) -> Response:
    """Soft-delete a project row."""

    deleted = await delete_project(project_id)
    if not deleted:
        raise HTTPException(404, f"Project {project_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
