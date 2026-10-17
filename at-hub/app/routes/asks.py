"""Assign / Ask a person on a record (services/asks.py). Open to every signed-in user: who can see which record is checked
inside -- the asker must see it, and only people whose role can see it can be asked."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.dependencies import get_current_active_user
from app.models import User
from app.services import asks

router = APIRouter(prefix="/api/asks", tags=["asks"])


class AskIn(BaseModel):
    entity_type: str
    entity_id: int
    user: str
    action: str = "review"
    comment: Optional[str] = None
    due_date: Optional[str] = None


class ReplyIn(BaseModel):
    text: str


@router.get("/")
def for_record(entity_type: str, entity_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.for_record(db, user, entity_type, entity_id)


@router.get("/people")
def people(entity_type: str, entity_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Everyone, each with ok / why not (can't see purchase orders, can't see prices...)."""
    return asks.eligible(db, entity_type, entity_id, user)


@router.get("/mine")
def mine(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.mine(db, user)


@router.post("/")
def create(data: AskIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.create(db, user, data.entity_type, data.entity_id, data.user, data.action, data.comment, data.due_date)


@router.post("/{ask_id}/reply")
def reply(ask_id: int, data: ReplyIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.reply(db, user, ask_id, data.text)


@router.post("/{ask_id}/done")
def done(ask_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.done(db, user, ask_id)


@router.post("/{ask_id}/withdraw")
def withdraw(ask_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return asks.withdraw(db, user, ask_id)
