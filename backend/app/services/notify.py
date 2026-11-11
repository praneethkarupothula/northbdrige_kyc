from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Notification


def notify(db: Session, user_id: str, kind: str, title: str, body: str, application_id: str | None = None) -> None:
    db.add(Notification(user_id=user_id, kind=kind, title=title, body=body, application_id=application_id))
