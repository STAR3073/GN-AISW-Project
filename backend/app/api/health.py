from fastapi import APIRouter

router = APIRouter()


@router.get("")
def health():
    return {"ok": True, "service": "DocAction AI"}
