from app import synthetic as sy
from tests.helpers import full_flow


def test_happy_path_pdf(applicant, worker):
    api = applicant()
    aid, cur = full_flow(api, worker, sy.person(0))
    assert cur["processing_status"] == "SUCCEEDED", cur
    assert cur["check_status"] == "MATCH", cur["checks"]
    assert cur["review_status"] == "PENDING_REVIEW"
    assert cur["status"]["headline"] == "Documents match — awaiting Northbridge review."
