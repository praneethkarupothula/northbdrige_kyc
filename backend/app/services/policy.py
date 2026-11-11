from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Organization, OrgPolicy

DEFAULT_ORG_SLUG = "northbridge"


def default_org(db: Session) -> Organization:
    org = db.scalar(select(Organization).where(Organization.slug == DEFAULT_ORG_SLUG))
    if org is None:
        raise RuntimeError("Organisation 'northbridge' missing - run migrations (alembic upgrade head)")
    return org


def get_policy(db: Session, organization_id: str) -> OrgPolicy:
    pol = db.scalar(select(OrgPolicy).where(OrgPolicy.organization_id == organization_id))
    if pol is None:
        s = get_settings()
        pol = OrgPolicy(
            organization_id=organization_id, pan_policy=s.pan_policy, max_salary_slips=s.max_salary_slips,
            required_salary_slips=s.required_salary_slips,
            retention_days_after_decision=s.retention_days_after_decision,
            max_file_mb=s.max_file_bytes // (1024 * 1024),
        )
        db.add(pol)
        db.flush()
    pol.pan_policy = "required"
    return pol
