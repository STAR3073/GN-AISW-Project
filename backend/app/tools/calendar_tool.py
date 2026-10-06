from app.db.database import SessionLocal
from app.models import CalendarEvent, new_id


def create_calendar_events(session_id: str, actions: list[dict]) -> list[dict]:
    created: list[dict] = []
    db = SessionLocal()
    try:
        db.query(CalendarEvent).filter(CalendarEvent.session_id == session_id).delete()
        for action in actions:
            deadline = action.get("deadline")
            if not deadline:
                continue
            event = CalendarEvent(
                id=new_id(),
                session_id=session_id,
                title=f"[마감] {action.get('title')}",
                start_at=f"{deadline}T09:00:00",
                end_at=f"{deadline}T10:00:00",
                source="mock",
            )
            db.add(event)
            created.append(
                {
                    "id": event.id,
                    "title": event.title,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "source": event.source,
                }
            )
        db.commit()
    finally:
        db.close()
    return created
