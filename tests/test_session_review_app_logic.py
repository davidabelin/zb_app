import importlib.util
from pathlib import Path


def _load_session_review_app_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "training" / "session_review_app.py"
    )
    spec = importlib.util.spec_from_file_location("session_review_app_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


session_review_app = _load_session_review_app_module()


def test_derive_final_evaluation_respects_dual_reviewer_rules():
    assert session_review_app.derive_final_evaluation("Use", "Use") == "Use"
    assert session_review_app.derive_final_evaluation("Reject", "Reject") == "Reject"
    assert session_review_app.derive_final_evaluation("Alter", "Use") == "Alter"
    assert session_review_app.derive_final_evaluation("Use", "Reject") == "Alter"
    assert session_review_app.derive_final_evaluation("Use", "") == ""
    assert session_review_app.derive_final_evaluation("", "") == ""


def test_normalize_source_row_migrates_legacy_evaluation_to_cm():
    row = {
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"source_path": "collected_sessions/web/example.jsonl"},
        "evaluation": "Reject",
    }

    normalized = session_review_app.normalize_source_row(row)

    assert normalized["review_version"] == session_review_app.LEGACY_REVIEW_VERSION
    assert normalized["review_zb"] == ""
    assert normalized["review_cm"] == "Reject"
    assert normalized["evaluation"] == "Reject"


def test_apply_reviewer_decision_upgrades_to_dual_review_and_recomputes_final():
    row = {
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"source_path": "collected_sessions/web/example.jsonl"},
        "evaluation": "Reject",
    }

    updated = session_review_app.apply_reviewer_decision(row, "ZB", "Use")

    assert updated["review_version"] == session_review_app.DUAL_REVIEW_VERSION
    assert updated["review_zb"] == "Use"
    assert updated["review_cm"] == "Reject"
    assert updated["evaluation"] == "Alter"
