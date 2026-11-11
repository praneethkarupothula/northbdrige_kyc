from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import Principal, csrf_anonymous, current_principal
from app.auth.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.auth.sessions import create_session, revoke_all_sessions, revoke_session, set_csrf_cookie
from app.core.config import get_settings
from app.core.crypto import keyed_lookup_hash, new_token, token_hash
from app.core.db import get_db
from app.models import AuthToken, EmailOutbox, LoginAttempt, Role, User
from app.schemas.api import (
    ChangePasswordIn, EmailIn, LoginIn, Msg, ProfileIn, RegisterIn, ResetConfirmIn, TokenIn, UserOut,
)
from app.services.audit import audit
from app.services.email import send_email

router = APIRouter(prefix="/v1/auth", tags=["auth"])
dev_router = APIRouter(prefix="/v1/dev", tags=["development only"])

GENERIC_LOGIN_ERROR = "Email or password is incorrect."


def _user_out(user: User, portal: str) -> UserOut:
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role,
                   email_verified=user.email_verified_at is not None, portal=portal,
                   organization_ids=[m.organization_id for m in user.memberships])


def _issue_token(db: Session, user: User, purpose: str, minutes: int) -> str:
    raw = new_token()
    db.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=token_hash(raw),
                     expires_at=datetime.now(UTC) + timedelta(minutes=minutes)))
    return raw


def _consume_token(db: Session, raw: str, purpose: str) -> AuthToken:
    tok = db.scalar(select(AuthToken).where(AuthToken.token_hash == token_hash(raw), AuthToken.purpose == purpose))
    exp = tok.expires_at.replace(tzinfo=tok.expires_at.tzinfo or UTC) if tok else None
    if tok is None or tok.used_at is not None or exp <= datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired.")
    tok.used_at = datetime.now(UTC)
    return tok


def send_verification(db: Session, user: User) -> None:
    s = get_settings()
    raw = _issue_token(db, user, "verify_email", s.email_token_ttl_minutes)
    send_email(db, user.email, "Verify your Northbridge KYC email",
               f"Hello,\n\nConfirm your email address by opening this link:\n"
               f"{s.public_base_url}/verify-email?token={raw}\n\nThe link expires in "
               f"{s.email_token_ttl_minutes // 60} hours and can be used once.\n")


def send_password_setup(db: Session, user: User, *, purpose_text: str) -> None:
    s = get_settings()
    raw = _issue_token(db, user, "reset_password", s.reset_token_ttl_minutes if "reset" in purpose_text else 60 * 24)
    portal = "/staff/reset-password" if user.role != Role.APPLICANT else "/reset-password"
    send_email(db, user.email, f"Northbridge KYC: {purpose_text}",
               f"Hello,\n\nUse this single-use link to {purpose_text}:\n{s.public_base_url}{portal}?token={raw}\n\n"
               "If you did not request this, you can ignore this email.\n")


def _rate_key(email: str, request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    return keyed_lookup_hash("email:" + email.lower()), keyed_lookup_hash("ip:" + ip)


def _check_rate(db: Session, keys: tuple[str, str]) -> None:
    s = get_settings()
    since = datetime.now(UTC) - timedelta(minutes=s.login_window_minutes)
    email_fail = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.key_hash == keys[0], LoginAttempt.succeeded.is_(False), LoginAttempt.created_at >= since))
    ip_fail = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.key_hash == keys[1], LoginAttempt.succeeded.is_(False), LoginAttempt.created_at >= since))
    if email_fail >= s.login_max_failures or ip_fail >= s.login_max_failures * 4:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Too many sign-in attempts. Try again in {s.login_window_minutes} minutes.")


@router.get("/csrf", response_model=Msg, summary="Issue a CSRF cookie for unauthenticated forms")
def csrf(response: Response, request: Request):
    if not request.cookies.get("nb_csrf"):
        set_csrf_cookie(response, new_token())
    return Msg(detail="ok")


@router.post("/register", response_model=UserOut, status_code=201, dependencies=[Depends(csrf_anonymous)])
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    if body.password != body.password_confirm:
        raise HTTPException(422, "Passwords do not match.")
    problems = password_problems(body.password, email=body.email, full_name=body.full_name)
    if problems:
        raise HTTPException(422, " ".join(problems))
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Please sign in.")
    user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.password),
                role=Role.APPLICANT)  # registration can never create a privileged role
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists. Please sign in.") from None
    send_verification(db, user)
    create_session(db, response, user, "applicant")
    audit(db, action="user.registered", target_type="user", target_id=user.id, actor=user)
    db.commit()
    return _user_out(user, "applicant")


def _login(body: LoginIn, request: Request, response: Response, db: Session, portal: str) -> UserOut:
    keys = _rate_key(body.email, request)
    _check_rate(db, keys)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(user.password_hash if user else None, body.password)
    allowed = user is not None and user.is_active and (
        (portal == "applicant" and user.role == Role.APPLICANT)
        or (portal == "staff" and user.role in (Role.REVIEWER, Role.ADMIN))
    )
    if not ok or not allowed:
        db.add_all([LoginAttempt(key_hash=keys[0]), LoginAttempt(key_hash=keys[1])])
        db.commit()
        if ok and user and not allowed and user.is_active:
            other = "Northbridge staff" if portal == "applicant" else "applicant"
            raise HTTPException(403, f"This account cannot sign in here. Use the {other} sign-in page.")
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    db.add(LoginAttempt(key_hash=keys[0], succeeded=True))
    create_session(db, response, user, portal)
    audit(db, action="session.login", target_type="user", target_id=user.id, actor=user, portal=portal)
    db.commit()
    return _user_out(user, portal)


@router.post("/applicant/login", response_model=UserOut, dependencies=[Depends(csrf_anonymous)])
def applicant_login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    return _login(body, request, response, db, "applicant")


@router.post("/staff/login", response_model=UserOut, dependencies=[Depends(csrf_anonymous)])
def staff_login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    return _login(body, request, response, db, "staff")


@router.post("/logout", response_model=Msg)
def logout(response: Response, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sess = db.merge(p.session)
    revoke_session(db, sess, response)
    db.commit()
    return Msg(detail="Signed out")


@router.get("/me", response_model=UserOut)
def me(p: Principal = Depends(current_principal)):
    return _user_out(p.user, p.session.portal)


@router.patch("/me", response_model=UserOut)
def update_profile(body: ProfileIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = db.get(User, p.user.id)
    user.full_name = " ".join(body.full_name.split())
    audit(db, action="user.profile_updated", target_type="user", target_id=user.id, actor=user, field="full_name")
    db.commit()
    return _user_out(user, p.session.portal)


@router.post("/verify-email", response_model=Msg, dependencies=[Depends(csrf_anonymous)])
def verify_email(body: TokenIn, db: Session = Depends(get_db)):
    tok = _consume_token(db, body.token, "verify_email")
    user = db.get(User, tok.user_id)
    user.email_verified_at = datetime.now(UTC)
    audit(db, action="user.email_verified", target_type="user", target_id=user.id, actor=user)
    db.commit()
    return Msg(detail="Email verified.")


@router.post("/resend-verification", response_model=Msg)
def resend_verification(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = db.get(User, p.user.id)
    if user.email_verified_at is None:
        send_verification(db, user)
        db.commit()
    return Msg(detail="If your email is not yet verified, a new link has been sent.")


@router.post("/password-reset/request", response_model=Msg, dependencies=[Depends(csrf_anonymous)])
def reset_request(body: EmailIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user and user.is_active:
        send_password_setup(db, user, purpose_text="reset your password")
        db.commit()
    return Msg(detail="If an account exists for this email, a reset link has been sent.")


@router.post("/password-reset/confirm", response_model=Msg, dependencies=[Depends(csrf_anonymous)])
def reset_confirm(body: ResetConfirmIn, db: Session = Depends(get_db)):
    if body.password != body.password_confirm:
        raise HTTPException(422, "Passwords do not match.")
    tok = _consume_token(db, body.token, "reset_password")
    user = db.get(User, tok.user_id)
    problems = password_problems(body.password, email=user.email, full_name=user.full_name)
    if problems:
        db.rollback()
        raise HTTPException(422, " ".join(problems))
    user.password_hash = hash_password(body.password)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)  # possession of the emailed link proves the address
    revoke_all_sessions(db, user.id)
    audit(db, action="user.password_reset", target_type="user", target_id=user.id, actor=user)
    db.commit()
    return Msg(detail="Password updated. Please sign in.")


@router.post("/change-password", response_model=Msg)
def change_password(body: ChangePasswordIn, response: Response, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    user = db.get(User, p.user.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    if body.password != body.password_confirm:
        raise HTTPException(422, "Passwords do not match.")
    problems = password_problems(body.password, email=user.email, full_name=user.full_name)
    if problems:
        raise HTTPException(422, " ".join(problems))
    user.password_hash = hash_password(body.password)
    revoke_all_sessions(db, user.id)
    create_session(db, response, user, p.session.portal)
    audit(db, action="user.password_changed", target_type="user", target_id=user.id, actor=user)
    db.commit()
    return Msg(detail="Password changed.")


@dev_router.get("/emails", summary="DEVELOPMENT MAILBOX - disabled in production")
def dev_emails(to: str | None = None, db: Session = Depends(get_db)):
    if get_settings().app_env == "production" or not get_settings().dev_email_capture:
        raise HTTPException(404, "Not found")
    q = select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(50)
    if to:
        q = q.where(EmailOutbox.to_address == to.lower())
    return [{"to": e.to_address, "subject": e.subject, "body": e.body, "created_at": e.created_at,
             "transport": e.transport, "notice": "Development email capture - not delivered"}
            for e in db.scalars(q)]
