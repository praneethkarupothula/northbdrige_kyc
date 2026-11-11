"""Server-managed sessions with HttpOnly cookies and double-submit CSRF tokens bound to the session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import new_token, token_hash
from app.models import AuthSession, User

SESSION_COOKIE = "nb_session"
CSRF_COOKIE = "nb_csrf"
CSRF_HEADER = "x-csrf-token"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def set_csrf_cookie(response: Response, csrf: str) -> None:
    s = get_settings()
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=s.secure_cookies, samesite="strict", path="/")


def create_session(db: Session, response: Response, user: User, portal: str) -> AuthSession:
    s = get_settings()
    token, csrf = new_token(), new_token()
    now = datetime.now(UTC)
    sess = AuthSession(
        token_hash=token_hash(token),
        csrf_hash=token_hash(csrf),
        user_id=user.id,
        portal=portal,
        expires_at=now + timedelta(minutes=s.session_ttl_minutes),
        last_seen_at=now,
    )
    db.add(sess)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=s.secure_cookies, samesite="lax", path="/",
        max_age=s.session_ttl_minutes * 60,
    )
    set_csrf_cookie(response, csrf)
    return sess


def load_session(db: Session, request: Request) -> tuple[AuthSession, User] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    s = get_settings()
    now = datetime.now(UTC)
    if _aware(sess.expires_at) <= now or _aware(sess.last_seen_at) + timedelta(minutes=s.session_idle_minutes) <= now:
        sess.revoked_at = now
        db.commit()
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if now - _aware(sess.last_seen_at) > timedelta(seconds=60):
        sess.last_seen_at = now
        db.commit()
    return sess, user


def revoke_session(db: Session, sess: AuthSession, response: Response) -> None:
    sess.revoked_at = datetime.now(UTC)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def revoke_all_sessions(db: Session, user_id: str) -> None:
    now = datetime.now(UTC)
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))):
        sess.revoked_at = now
