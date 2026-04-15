"""
Dashboard — serves the single-page application.
All data is fetched client-side via the /api/* REST endpoints.
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/")
@router.get("/contacts/{path:path}")
async def spa(_path: str = ""):
    return FileResponse("app/static/index.html")
