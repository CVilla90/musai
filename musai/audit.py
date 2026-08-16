import json
from datetime import datetime
from sqlmodel import Session
from musai.models import AuditLog


def log(
    session: Session,
    action: str,
    *,
    actor: str = "system",
    target: str | None = None,
    env: str | None = None,
    dry_run: bool = True,
    detail: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        target=target,
        env=env,
        dry_run=dry_run,
        detail_json=json.dumps(detail or {}),
        created_at=datetime.utcnow(),
    )
    session.add(entry)
    session.flush()
    return entry
