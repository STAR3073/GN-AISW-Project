from app.models import new_id


def materialize_tasks(session_id: str, actions: list[dict], planned: list[dict]) -> list[dict]:
    by_title = {a.get("title"): a for a in actions}
    tasks: list[dict] = []
    for item in planned:
        action = by_title.get(item.get("action_title")) or {}
        tasks.append(
            {
                "id": item.get("id") or new_id(),
                "session_id": session_id,
                "action_id": action.get("id") or None,
                "action_title": item.get("action_title"),
                "title": item.get("title"),
                "due_date": item.get("due_date"),
                "status": item.get("status") or "TODO",
                "order_index": item.get("order_index", 0),
                "estimated_hours": item.get("estimated_hours", 2.0),
            }
        )
    return tasks
