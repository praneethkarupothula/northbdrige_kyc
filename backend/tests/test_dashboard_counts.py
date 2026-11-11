"""Overview totals agree with the queues linked by each card."""
from sqlalchemy import select

from app.core.db import session_scope
from app.models import ApplicationVersion
from tests.helpers import start


def test_overview_counts_match_queue_filters_after_decisions(applicant, staff):
    ids = [start(applicant())["current"]["id"] for _ in range(8)]
    states = [
        ("SUCCEEDED", "MISMATCH", "PENDING_REVIEW"),
        ("SUCCEEDED", "MISMATCH", "REJECTED"),
        ("SUCCEEDED", "INCOMPLETE", "CHANGES_REQUESTED"),
        ("SUCCEEDED", "MATCH", "APPROVED"),
        ("QUEUED", None, "NOT_READY"),
        ("RUNNING", None, "NOT_READY"),
        ("FAILED", None, "NOT_READY"),
    ]
    with session_scope() as db:
        for vid, (processing, check, review) in zip(ids, states):
            v = db.get(ApplicationVersion, vid)
            v.state = "SUBMITTED"
            v.processing_status, v.check_status, v.review_status = processing, check, review
        db.commit()
    reviewer = staff()
    dashboard = reviewer.get("/v1/staff/dashboard").json()
    cards = {
        "pending_review": {"review_status": "PENDING_REVIEW"},
        "mismatches": {"check_status": "MISMATCH"},
        "incomplete": {"check_status": "INCOMPLETE"},
        "approved": {"review_status": "APPROVED"},
        "rejected": {"review_status": "REJECTED"},
        "changes_requested": {"review_status": "CHANGES_REQUESTED"},
        "processing_failures": {"processing_status": "FAILED"},
        "processing": {"processing_status": "IN_PROGRESS"},
        "unassigned_pending": {"review_status": "PENDING_REVIEW", "assignment": "unassigned"},
    }
    for key, filters in cards.items():
        queue = reviewer.get("/v1/staff/applications", params=filters).json()
        assert dashboard[key] == queue["total"], key
    assert dashboard["mismatches"] == 2  # A rejected mismatch remains a mismatch.
    assert dashboard["incomplete"] == 1  # Drafts aren't an incomplete check result.
    assert dashboard["processing"] == 2
    with session_scope() as db:
        v = db.scalar(select(ApplicationVersion).where(ApplicationVersion.id == ids[0]))
        v.review_status = "REJECTED"
        db.commit()
    updated = reviewer.get("/v1/staff/dashboard").json()
    assert updated["pending_review"] == 0
    assert updated["rejected"] == 2
    assert updated["mismatches"] == 2
    other = staff(org_slug="otherbank")
    assert all(other.get("/v1/staff/dashboard").json()[key] == 0 for key in cards)
