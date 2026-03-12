# tests/test_utils.py
import re
from utilities import get_cid, resequence_logbook_entries


def test_get_cid_contains_student_and_timestamp():
    cid = get_cid(student="alice")
    assert cid.startswith("zb-alice-")
    # Format: zb-alice-YYYYMMDD-HHMMSS-XXXXXXXX
    suffix = cid.split("zb-alice-")[1]
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", suffix)


def test_resequence_logbook_entries_assigns_plain_incrementing_serials():
    entries = [
        {
            "serial_number": "2.11",
            "date": "2026-03-01",
            "time": "10:00",
            "title": "First",
            "koans_used": [],
            "user_problem_or_questions": "One",
            "response_summary": "One",
            "session_evaluations": [],
            "key_insights": [],
            "lessons_learned": [],
            "final_outcome": "One",
        },
        {
            "serial_number": "api-test-1",
            "date": "2026-03-02",
            "time": "11:00",
            "title": "Second",
            "koans_used": [],
            "user_problem_or_questions": "Two",
            "response_summary": "Two",
            "session_evaluations": [],
            "key_insights": [],
            "lessons_learned": [],
            "final_outcome": "Two",
        },
    ]

    resequenced = resequence_logbook_entries(entries)

    assert [entry["serial_number"] for entry in resequenced] == ["001", "002"]
