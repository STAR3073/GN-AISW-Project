P1_KEYWORDS = (
    "제출",
    "보고서",
    "소스",
    "발표",
    "시연",
    "mvp",
    "필수",
    "마감",
    "설명서",
)
P3_KEYWORDS = ("선택", "가산", "우대", "자율")


def rescore_priority(action: dict) -> str:
    blob = f"{action.get('title', '')} {action.get('deliverable', '')} {action.get('description', '')}".lower()
    if any(k in blob for k in P3_KEYWORDS):
        return "P3"
    if any(k in blob for k in P1_KEYWORDS):
        return "P1"
    return action.get("priority") or "P2"


def apply_priority_rules(actions: list[dict]) -> list[dict]:
    for action in actions:
        action["priority"] = rescore_priority(action)
        action["status"] = action.get("status") or "TODO"
    return actions
