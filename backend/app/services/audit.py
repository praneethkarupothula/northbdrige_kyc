from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

# Audit details may only carry these keys - never names, identifiers or document content.
_SAFE_DETAIL_KEYS = {
    "decision", "reason_code", "from_status", "to_status", "version_number", "field", "category",
    "document_id", "role", "target_role", "pan_policy", "max_salary_slips", "required_salary_slips",
    "retention_days_after_decision", "max_file_mb", "accept_bank_statement", "resolution", "id_type",
    "claim_id", "count", "portal", "page", "reveal", "outcome", "attempt",
}


def audit(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    actor=None,
    organization_id: str | None = None,
    application_id: str | None = None,
    **details: Any,
) -> None:
    safe = {k: v for k, v in details.items() if k in _SAFE_DETAIL_KEYS and isinstance(v, (str, int, bool, type(None)))}
    db.add(
        AuditEvent(
            actor_id=getattr(actor, "id", None),
            actor_role=getattr(actor, "role", None),
            organization_id=organization_id,
            application_id=application_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=safe,
        )
    )
