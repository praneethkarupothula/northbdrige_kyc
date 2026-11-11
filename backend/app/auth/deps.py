"""Authorization dependencies. Every route declares one of these; there is no implicit access."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import CSRF_COOKIE, CSRF_HEADER, load_session
from app.core.crypto import constant_time_equals, token_hash
from app.core.db import get_db
from app.models import Application, AuthSession, Role, User

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    user: User
    session: AuthSession

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def is_staff(self) -> bool:
        return self.user.role in (Role.REVIEWER, Role.ADMIN)

    def org_ids(self) -> set[str]:
        return {m.organization_id for m in self.user.memberships}


def verify_csrf(request: Request, sess: AuthSession | None) -> None:
    if request.method not in UNSAFE:
        return
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not constant_time_equals(header, cookie):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    if sess is not None and not constant_time_equals(token_hash(header), sess.csrf_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token does not belong to this session")


def csrf_anonymous(request: Request) -> None:
    """For unauthenticated state-changing routes (login, register): double-submit check."""
    verify_csrf(request, None)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    loaded = load_session(db, request)
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    sess, user = loaded
    verify_csrf(request, sess)
    return Principal(user=user, session=sess)


def require_applicant(p: Principal = Depends(current_principal)) -> Principal:
    if p.role != Role.APPLICANT or p.session.portal != "applicant":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Applicant access only")
    return p


def require_staff(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_staff or p.session.portal != "staff":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Northbridge staff access only")
    return p


def require_admin(p: Principal = Depends(require_staff)) -> Principal:
    if p.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access only")
    return p


def authorize_application(p: Principal, app: Application | None) -> Application:
    """Object-level check. Returns 404 (not 403) so IDs of other people's applications are not confirmed."""
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    if p.role == Role.APPLICANT:
        if app.applicant_id != p.user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    elif p.is_staff:
        if app.organization_id not in p.org_ids():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return app
