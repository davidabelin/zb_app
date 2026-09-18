"""Regression checks for the Mumon exemplar context and dokusan prompt shape."""

from __future__ import annotations

from pathlib import Path

import pytest

import utilities


GENERIC_MODEL = "gpt-5.5"
FINETUNED_MODEL = "set03-bs2lr05e7"


@pytest.fixture(autouse=True)
def _clear_exemplar_cache():
    utilities._load_mumon_exemplars_text.cache_clear()
    yield
    utilities._load_mumon_exemplars_text.cache_clear()


def _metadata_for(model_name: str, case_id: str | None = None) -> dict:
    profile = utilities._choose_session_profile({"model_name": model_name})
    return utilities._build_metadata(
        student="tester", case_id=case_id, session_profile=profile
    )


def test_exemplar_asset_renders_as_student_mumon_transcripts():
    text = utilities._load_mumon_exemplars_text(utilities.config.MUMON_EXEMPLARS_PATH)

    assert utilities._mumon_exemplar_count(text) >= 90
    assert text.count("Student: (bows)") >= 85
    assert "You are Mumonbot" not in text
    assert text.startswith("--- Dokusan 1 ---\nStudent: ")
    assert "\nMumon: " in text


def test_generic_model_receives_exemplars_and_closing_rule():
    text = utilities._response_instructions(_metadata_for(GENERIC_MODEL))

    assert text.startswith("Below are ")
    assert "=== MUMON IN DOKUSAN ===" in text
    assert utilities.EXEMPLARS_END_MARKER in text
    assert utilities.DOKUSAN_CLOSING_RULE in text
    assert text.index(utilities.EXEMPLARS_END_MARKER) < text.index(
        utilities.DOKUSAN_CLOSING_RULE
    )


def test_finetuned_model_skips_exemplars_but_keeps_closing_rule():
    text = utilities._response_instructions(_metadata_for(FINETUNED_MODEL))

    assert utilities._is_finetuned_model(FINETUNED_MODEL)
    assert utilities.EXEMPLARS_END_MARKER not in text
    assert "--- Dokusan " not in text
    assert utilities.DOKUSAN_CLOSING_RULE in text
    assert text.startswith("You are Mumonbot conducting dokusan")


def test_exemplars_precede_dynamic_case_focus_line():
    text = utilities._response_instructions(_metadata_for(GENERIC_MODEL, case_id="7"))

    assert text.index(utilities.EXEMPLARS_END_MARKER) < text.index(
        "The current koan focus is case 7."
    )
    assert text.rstrip().endswith("Do not use web search for dokusan or koan dialogue.")


def test_startup_system_prompt_leads_with_preset_description():
    preset = utilities._botling_preset_definition("balanced")
    prompt = utilities._startup_system_prompt({"preset_id": "balanced"})

    assert prompt.startswith(preset["description"])
    assert preset["instruction"].strip() in prompt


def test_missing_exemplar_file_degrades_to_no_exemplars(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        utilities.config, "MUMON_EXEMPLARS_PATH", str(tmp_path / "missing.jsonl")
    )

    text = utilities._response_instructions(_metadata_for(GENERIC_MODEL))

    assert utilities.EXEMPLARS_END_MARKER not in text
    assert utilities.DOKUSAN_CLOSING_RULE in text


def test_exemplars_can_be_disabled_by_config(monkeypatch):
    monkeypatch.setattr(utilities.config, "MUMON_EXEMPLARS_ENABLED", False)

    text = utilities._response_instructions(_metadata_for(GENERIC_MODEL))

    assert utilities.EXEMPLARS_END_MARKER not in text


def test_exemplar_loader_skips_malformed_lines(tmp_path: Path):
    path = tmp_path / "exemplars.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"messages": [{"role": "system", "content": "persona"}, '
                '{"role": "user", "content": "Joshu\'s Dog."}, '
                '{"role": "assistant", "content": "(nods)"}, '
                '{"role": "user", "content": "(bows)"}, '
                '{"role": "assistant", "content": "(bows)"}]}',
                "not json",
                '{"messages": "wrong shape"}',
            ]
        ),
        encoding="utf-8",
    )

    text = utilities._load_mumon_exemplars_text(str(path))

    assert utilities._mumon_exemplar_count(text) == 1
    assert "persona" not in text
    assert text == (
        "--- Dokusan 1 ---\n"
        "Student: Joshu's Dog.\n"
        "Mumon: (nods)\n"
        "Student: (bows)\n"
        "Mumon: (bows)"
    )
