"""Acceptance 1-3: registration/login/logout, role separation, CSRF, tokens, rate limiting."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tests.conftest import PASSWORD


def _mail(api, to):
    msgs = api.get("/v1/dev/emails", params={"to": to}).json()
    assert msgs, "no captured email"
    return msgs[0]


def _token(body):
    return re.search(r"token=([\w\-]+)", body).group(1)


def test_register_login_logout(make_client):
    api = make_client()
    r = api.post("/v1/auth/register", json={"full_name": "Asha Verma", "email": "asha@example.com",
                                            "password": PASSWORD, "password_confirm": PASSWORD})
    assert r.status_code == 201
    assert r.json()["role"] == "applicant"
    session_cookie = api.c.cookies.get("nb_session")
    assert session_cookie
    assert api.get("/v1/auth/me").json()["email"] == "asha@example.com"
    assert api.post("/v1/auth/logout").status_code == 200
    api.c.cookies.set("nb_session", session_cookie)  # replaying a revoked session must fail
    assert api.get("/v1/auth/me").status_code == 401
    api2 = make_client()
    r = api2.post("/v1/auth/applicant/login", json={"email": "asha@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert api2.get("/v1/auth/me").status_code == 200


def test_session_cookie_flags(make_client):
    api = make_client()
    r = api.post("/v1/auth/register", json={"full_name": "Asha Verma", "email": "flags@example.com",
                                            "password": PASSWORD, "password_confirm": PASSWORD})
    sc = [h for k, h in r.headers.multi_items() if k == "set-cookie" and h.startswith("nb_session=")][0].lower()
    assert "httponly" in sc and "samesite=lax" in sc


def test_registration_validation(make_client):
    api = make_client()
    base = {"full_name": "Asha Verma", "email": "v@example.com"}
    assert api.post("/v1/auth/register", json={**base, "password": PASSWORD, "password_confirm": "nope"}).status_code == 422
    r = api.post("/v1/auth/register", json={**base, "password": "short", "password_confirm": "short"})
    assert r.status_code == 422
    r = api.post("/v1/auth/register", json={**base, "password": PASSWORD, "password_confirm": PASSWORD, "role": "admin"})
    assert r.status_code == 422, "role must not be accepted at registration"
    assert api.post("/v1/auth/register", json={**base, "password": PASSWORD, "password_confirm": PASSWORD}).status_code == 201
    api2 = make_client()
    assert api2.post("/v1/auth/register", json={**base, "password": PASSWORD, "password_confirm": PASSWORD}).status_code == 409


def test_csrf_required(make_client):
    api = make_client()
    r = api.c.post("/v1/auth/register", json={"full_name": "A B", "email": "c@example.com", "password": PASSWORD,
                                              "password_confirm": PASSWORD})
    assert r.status_code == 403
    api.post("/v1/auth/register", json={"full_name": "A B", "email": "c@example.com", "password": PASSWORD,
                                        "password_confirm": PASSWORD})
    assert api.c.post("/v1/applications").status_code == 403  # no header
    assert api.c.post("/v1/applications", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert api.post("/v1/applications").status_code == 200


def test_portal_separation(applicant, make_client, staff):
    a = applicant()
    s = staff()
    # applicant cannot use staff login, staff cannot use applicant login
    x = make_client()
    assert x.post("/v1/auth/staff/login", json={"email": a.email, "password": PASSWORD}).status_code == 403
    assert x.post("/v1/auth/applicant/login", json={"email": s.email, "password": PASSWORD}).status_code == 403
    # applicant session cannot reach staff/admin APIs
    assert a.get("/v1/staff/dashboard").status_code == 403
    assert a.get("/v1/admin/staff").status_code == 403
    # staff cannot use applicant APIs
    assert s.post("/v1/applications").status_code == 403
    # reviewer is not admin
    assert s.get("/v1/admin/policy").status_code == 403
    assert staff("admin").get("/v1/admin/policy").status_code == 200


def test_session_expiry(applicant):
    from app.core.db import session_scope
    from app.models import AuthSession

    a = applicant()
    with session_scope() as db:
        db.execute(update(AuthSession).values(last_seen_at=datetime.now(UTC) - timedelta(hours=3)))
    assert a.get("/v1/auth/me").status_code == 401


def test_login_rate_limit(applicant, make_client):
    a = applicant()
    x = make_client()
    for _ in range(5):
        assert x.post("/v1/auth/applicant/login", json={"email": a.email, "password": "Wrong-pass-1234"}).status_code == 401
    r = x.post("/v1/auth/applicant/login", json={"email": a.email, "password": PASSWORD})
    assert r.status_code == 429


def test_email_verification_single_use(applicant):
    a = applicant()
    tok = _token(_mail(a, a.email)["body"])
    assert a.get("/v1/auth/me").json()["email_verified"] is False
    assert a.post("/v1/auth/verify-email", json={"token": tok}).status_code == 200
    assert a.get("/v1/auth/me").json()["email_verified"] is True
    assert a.post("/v1/auth/verify-email", json={"token": tok}).status_code == 400  # single use


def test_password_reset_expiring_single_use(applicant, make_client):
    from app.core.db import session_scope
    from app.models import AuthToken

    a = applicant()
    x = make_client()
    assert x.post("/v1/auth/password-reset/request", json={"email": a.email}).status_code == 200
    assert x.post("/v1/auth/password-reset/request", json={"email": "nobody@example.com"}).status_code == 200
    mails = [m for m in x.get("/v1/dev/emails", params={"to": a.email}).json() if "reset" in m["subject"]]
    tok = _token(mails[0]["body"])
    new = "Another-Strong-Pass-9"
    assert x.post("/v1/auth/password-reset/confirm", json={"token": tok, "password": new, "password_confirm": new}).status_code == 200
    assert x.post("/v1/auth/password-reset/confirm", json={"token": tok, "password": new, "password_confirm": new}).status_code == 400
    assert a.get("/v1/auth/me").status_code == 401, "existing sessions are revoked after reset"
    assert x.post("/v1/auth/applicant/login", json={"email": a.email, "password": new}).status_code == 200
    # expired token
    x.post("/v1/auth/password-reset/request", json={"email": a.email})
    tok2 = _token([m for m in x.get("/v1/dev/emails", params={"to": a.email}).json() if "reset" in m["subject"]][0]["body"])
    with session_scope() as db:
        for t in db.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))):
            t.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert x.post("/v1/auth/password-reset/confirm", json={"token": tok2, "password": new, "password_confirm": new}).status_code == 400


def test_admin_creates_staff_via_invite(staff, make_client):
    adm = staff("admin")
    r = adm.post("/v1/admin/staff", json={"full_name": "New Reviewer", "email": "newrev@northbridge.example.com",
                                          "role": "reviewer"})
    assert r.status_code == 201
    x = make_client()
    assert x.post("/v1/auth/staff/login", json={"email": "newrev@northbridge.example.com",
                                                "password": PASSWORD}).status_code == 401  # no password yet
    body = [m for m in adm.get("/v1/dev/emails", params={"to": "newrev@northbridge.example.com"}).json()][0]["body"]
    assert "/staff/reset-password" in body
    tok = _token(body)
    assert x.post("/v1/auth/password-reset/confirm", json={"token": tok, "password": PASSWORD,
                                                           "password_confirm": PASSWORD}).status_code == 200
    assert x.post("/v1/auth/staff/login", json={"email": "newrev@northbridge.example.com",
                                                "password": PASSWORD}).status_code == 200
    rev = staff("reviewer")
    assert rev.post("/v1/admin/staff", json={"full_name": "X Y", "email": "x@northbridge.example.com",
                                             "role": "admin"}).status_code == 403


def test_bootstrap_admin_cli(env, monkeypatch, make_client):
    from app.cli import main

    monkeypatch.setenv("NB_BOOTSTRAP_PASSWORD", "weak")
    assert main(["bootstrap-admin", "--email", "boss@northbridge.example.com", "--name", "Boss Person"]) == 1
    monkeypatch.setenv("NB_BOOTSTRAP_PASSWORD", PASSWORD)
    assert main(["bootstrap-admin", "--email", "boss@northbridge.example.com", "--name", "Boss Person"]) == 0
    x = make_client()
    r = x.post("/v1/auth/staff/login", json={"email": "boss@northbridge.example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "admin"
