# tests/test_utils.py
import re
import utilities
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


def test_get_conversation_state_reassigns_retired_model_metadata(monkeypatch):
    monkeypatch.setattr(utilities, "DB", None)
    monkeypatch.setattr(
        utilities,
        "_LOCAL_CONVERSATIONS",
        {
            "cid-1": {
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {
                    "model_name": "mmnk_ble824",
                    "profile": "mid",
                    "training_loss": 1.32,
                    "params": {"model": "ft:retired-model"},
                },
            }
        },
    )
    monkeypatch.setattr(
        utilities.config,
        "MODELS_IN_USE",
        {"set03a-bs5lr05e5": "ft:active-model"},
    )
    monkeypatch.setattr(
        utilities.config,
        "MODEL_ARGS",
        {
            "mid": {
                "temperature": 1.0,
                "max_tokens": 1024,
                "top_p": 0.5,
                "frequency_penalty": 1.0,
                "presence_penalty": 0.5,
            }
        },
    )

    messages, metadata = utilities.get_conversation_state("cid-1")

    assert messages == [{"role": "user", "content": "hello"}]
    assert metadata["model_name"] == "set03a-bs5lr05e5"
    assert metadata["profile"] == "mid"
    assert metadata["params"]["model"] == "ft:active-model"


def test_make_params_maps_legacy_minimal_reasoning_to_low():
    config = utilities.Config(
        MODELS_IN_USE={"set03-bs2lr05e7": "ft:active-model"},
        MODEL_ARGS={
            "live": {
                "temperature": 0.9,
                "max_output_tokens": 900,
                "top_p": 1.0,
                "reasoning_effort": "minimal",
            }
        },
    )

    params = config.make_params("live", model_name="set03-bs2lr05e7")

    assert "reasoning" not in params
    assert params["temperature"] == 0.9
    assert params["top_p"] == 1.0
