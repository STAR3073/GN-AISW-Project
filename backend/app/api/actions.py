from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import Action
from app.schemas import ActionOut, ActionStatusPatch

router = APIRouter()

ALLOWED = {"TODO", "IN_PROGRESS", "DRAFT_COMPLETED", "COMPLETED"}


@router.get("/{action_id}", response_model=ActionOut)
def get_action(action_id: str, db: Session = Depends(get_db)):
    row = db.get(Action, action_id)
    if not row:
        raise HTTPException(404, "Action을 찾을 수 없습니다.")
    return ActionOut.model_validate(row, from_attributes=True)


@router.patch("/{action_id}", response_model=ActionOut)
def patch_action(action_id: str, body: ActionStatusPatch, db: Session = Depends(get_db)):
    if body.status not in ALLOWED:
        raise HTTPException(400, f"허용 상태: {sorted(ALLOWED)}")
    row = db.get(Action, action_id)
    if not row:
        raise HTTPException(404, "Action을 찾을 수 없습니다.")
    row.status = body.status
    db.commit()
    db.refresh(row)
    return ActionOut.model_validate(row, from_attributes=True)
