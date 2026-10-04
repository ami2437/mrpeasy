"""Database backups page (super admin): list, back up now, download, restore."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.dependencies import get_current_active_user, require_role
from app.models import User
from app.services import backups

router = APIRouter(prefix="/api/backups", tags=["backups"], dependencies=[Depends(require_role("super_admin"))])


@router.get("/")
def list_all():
    from app.config.settings import settings
    return {"backups": backups.list_backups(), "every_hours": settings.backup_every_hours, "keep": settings.backup_keep,
            "folder": str(backups.backup_dir()), "copies": [str(d) for d in backups.copy_dirs()]}


@router.post("/")
def make():
    return {"name": backups.backup_now("manual").name}


@router.get("/{name}/file")
def download(name: str):
    f = (backups.backup_dir() / name).resolve()
    if f.parent != backups.backup_dir() or not f.exists():
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(f, filename=name, media_type="application/octet-stream")


@router.post("/{name}/restore")
def restore(name: str, user: User = Depends(get_current_active_user)):
    try:
        safety = backups.restore(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No such backup")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"restored": name, "safety_copy": safety.name}


@router.delete("/{name}")
def delete(name: str):
    f = (backups.backup_dir() / name).resolve()
    if f.parent != backups.backup_dir() or not f.exists():
        raise HTTPException(status_code=404, detail="No such backup")
    f.unlink()
    return {"deleted": name}
