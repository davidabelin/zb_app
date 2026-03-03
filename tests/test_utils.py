# tests/test_utils.py
import re
from utilities import get_cid


def test_get_cid_contains_student_and_timestamp():
    cid = get_cid(student="alice")
    assert cid.startswith("zb-alice-")
    # Format: zb-alice-YYYYMMDD-HHMMSS-XXXXXXXX
    suffix = cid.split("zb-alice-")[1]
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", suffix)
