"""Applicant-facing status text. Automated result and reviewer decision stay distinct."""

from __future__ import annotations

from app.models import ApplicationVersion, CheckStatus, ProcessingStatus, ReviewStatus, VersionState
from app.services.identity import GENERIC_DUPLICATE_MESSAGE


def applicant_status(v: ApplicationVersion, *, has_identity_conflict: bool = False) -> dict:
    """Return {key, tone, headline, detail}. Never fabricates success or mismatch."""
    if v.review_status == ReviewStatus.APPROVED:
        return _s("VERIFIED", "success", "KYC completed and approved",
                  "Northbridge has reviewed and approved your submitted documents. No further action is needed for this submission. "
                  "This confirms document name and PAN-number consistency only, not government authentication or loan approval.")
    if v.review_status == ReviewStatus.REJECTED:
        return _s("REJECTED", "danger", "Application not approved",
                  v.applicant_message or "Northbridge could not approve this application.")
    if v.review_status == ReviewStatus.CHANGES_REQUESTED:
        return _s("CHANGES_REQUESTED", "warning", "Northbridge requested changes",
                  v.applicant_message or "Please review the requested items and resubmit.")
    if v.state == VersionState.DRAFT:
        return _s("DRAFT", "neutral", "Draft — not submitted yet",
                  "Upload your documents and submit when you are ready.")
    if v.processing_status in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING):
        return _s("PROCESSING", "info", "Processing your documents",
                  "You can follow each step live below. This page updates automatically.")
    if v.processing_status == ProcessingStatus.FAILED:
        return _s("PROCESSING_FAILED", "danger", "Processing could not be completed",
                  "A system error stopped processing. This is not a document mismatch; Northbridge staff will retry.")
    if has_identity_conflict:
        return _s("IDENTITY_CONFLICT", "warning", "We need to check your identity details", GENERIC_DUPLICATE_MESSAGE)
    if v.processing_status == ProcessingStatus.NEEDS_REUPLOAD:
        return _s("NEEDS_REUPLOAD", "warning", "A document needs to be uploaded again",
                  "At least one document could not be read clearly. See the document list for details.")
    if v.check_status == CheckStatus.MATCH:
        return _s("MATCH_AWAITING_REVIEW", "info", "Documents match — awaiting Northbridge review.",
                  "The names on your documents agree. A Northbridge reviewer still has to approve the application.")
    if v.check_status == CheckStatus.MISMATCH:
        return _s("MISMATCH", "warning", "A document needs your attention",
                  "The name on at least one document does not match your Aadhaar. See the highlighted document below. "
                  "A Northbridge reviewer will also look at it.")
    if v.check_status == CheckStatus.INCOMPLETE:
        return _s("INCOMPLETE", "warning", "Some information could not be confirmed",
                  "A required document or name could not be read. A Northbridge reviewer will check it manually "
                  "and may ask you for a clearer copy.")
    return _s("SUBMITTED", "info", "Submitted", "Your application has been received.")


def _s(key: str, tone: str, headline: str, detail: str) -> dict:
    return {"key": key, "tone": tone, "headline": headline, "detail": detail}
