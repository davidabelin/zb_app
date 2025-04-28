# tests/test_utils.py
from utilities import get_cid

def test_get_cid_contains_student_and_timestamp():
    cid = get_cid(student="alice")
    assert cid.startswith("zb-alice-")
    # timestamp is 15 chars after the dash: YYYYMMDD-HHMMSS
    ts = cid.split("zb-alice-")[1]
    assert len(ts) == 15
