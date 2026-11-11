"""seed Northbridge Finance organisation and default prototype policy

Revision ID: 0002
Revises: 0001
"""
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ORG_ID = "00000000-0000-4000-8000-000000000001"
POLICY_ID = "00000000-0000-4000-8000-000000000002"


def upgrade() -> None:
    now = datetime.now(UTC)
    orgs = sa.table("organizations", sa.column("id"), sa.column("slug"), sa.column("name"), sa.column("created_at"))
    op.bulk_insert(orgs, [{"id": ORG_ID, "slug": "northbridge", "name": "Northbridge Finance", "created_at": now}])
    pol = sa.table(
        "org_policies", sa.column("id"), sa.column("organization_id"), sa.column("pan_policy"),
        sa.column("max_salary_slips"), sa.column("required_salary_slips"), sa.column("accept_bank_statement"),
        sa.column("max_file_mb"), sa.column("retention_days_after_decision"), sa.column("updated_at"),
    )
    op.bulk_insert(pol, [{
        "id": POLICY_ID, "organization_id": ORG_ID, "pan_policy": "optional", "max_salary_slips": 6,
        "required_salary_slips": 1, "accept_bank_statement": True, "max_file_mb": 10,
        "retention_days_after_decision": 7, "updated_at": now,
    }])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM org_policies WHERE id = :i").bindparams(i=POLICY_ID))
    op.execute(sa.text("DELETE FROM organizations WHERE id = :i").bindparams(i=ORG_ID))
