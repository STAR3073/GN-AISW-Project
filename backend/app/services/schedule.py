from datetime import date, datetime, timedelta


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def infer_final_deadline(actions: list[dict], fallback_days: int = 21) -> date:
    dates = [_as_date(a.get("deadline")) for a in actions]
    dates = [d for d in dates if d]
    if dates:
        return max(dates)
    return date.today() + timedelta(days=fallback_days)


def backschedule(tasks: list[dict], final_deadline: date | None) -> list[dict]:
    if not tasks:
        return tasks
    end = final_deadline or (date.today() + timedelta(days=21))
    start = date.today()
    if end < start:
        end = start
    span = max((end - start).days, len(tasks))
    n = len(tasks)
    for i, task in enumerate(tasks):
        ratio = (i + 1) / n
        due = start + timedelta(days=int(span * ratio))
        if due > end:
            due = end
        task["due_date"] = due.isoformat()
        task["order_index"] = i
        if "status" not in task:
            task["status"] = "TODO"
    tasks[-1]["due_date"] = end.isoformat()
    return tasks
