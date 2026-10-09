"""Download a backup to your own computer (permission "backups.download", Super admin by default).

    GET /api/local-backup/status          when it was last done, and whether one is due (every 3 days)
    GET /api/local-backup/download?files=  a .zip: the database (+ every attached file)
    POST /api/local-backup/saved           the browser saved it -- only this resets the 3-day clock
Someone with the permission who hasn't had a download in 3 days gets a pop-up they can't close until they take one
(openLocalBackup in auth-guard.js)."""
import shutil

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config.database import get_db
from app.dependencies import get_current_active_user, require_perm
from app.models import User
from app.services import backups

router = APIRouter(prefix="/api/local-backup", tags=["backups"], dependencies=[Depends(require_perm("backups.download"))])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return backups.local_status(db)


@router.get("/download")
def download(files: bool = True, user: User = Depends(get_current_active_user)):
    path = backups.build_local_copy(include_files=files)
    size = path.stat().st_size
    return FileResponse(path, filename=path.name, media_type="application/zip",
                        headers={"Cache-Control": "no-store", "X-Backup-Size": str(size)},
                        background=BackgroundTask(shutil.rmtree, path.parent, ignore_errors=True))


class SavedIn(BaseModel):
    files: bool
    size: int


@router.post("/saved")
def saved(data: SavedIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """The browser finished writing the download to disk (a cancelled Save As never gets here)."""
    if data.size <= 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Nothing was saved")
    backups.record_local_copy(db, user.username, data.files, data.size)
    return backups.local_status(db)
