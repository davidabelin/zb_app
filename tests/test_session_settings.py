import main
import utilities


def test_session_options_payload_exposes_defaults_presets_and_models():
    payload = utilities.session_options_payload()

    assert payload["settings_version"] == utilities.config.SESSION_SETTINGS_VERSION
    assert payload["defaults"]["preset_id"] == utilities.config.DEFAULT_BOTLING_ID
    assert any(preset["id"] == "balanced_mumon" for preset in payload["presets"])

    model = next(
        model for model in payload["models"] if model["id"] == "gpt-5.4-mini"
    )
    assert model["supports_reasoning"] is True
    assert model["supports_sampling_controls"] is False


def test_resolve_session_settings_rejects_sampling_controls_for_gpt5():
    try:
        utilities.resolve_session_settings(
            {
                "model_name": "gpt-5.4-mini",
                "temperature": 0.4,
            }
        )
    except ValueError as exc:
        assert "temperature is not supported" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported sampling controls")


def test_session_settings_from_legacy_metadata_backfills_snapshot():
    settings = utilities.session_settings_from_metadata(
        {
            "model_name": "gpt-5.4-mini",
            "profile": "live",
            "botling_id": "fierce_barrier",
            "params": {
                "model": "gpt-5.4-mini",
                "max_output_tokens": 777,
                "reasoning": {"effort": "medium"},
            },
        }
    )

    assert settings["preset_id"] == "fierce_barrier"
    assert settings["model_name"] == "gpt-5.4-mini"
    assert settings["max_output_tokens"] == 777
    assert settings["reasoning_effort"] == "medium"


def test_chat_and_zb_api_options_routes_return_session_payload(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    client = main.app.test_client()

    browser_response = client.get("/chat/options")
    api_response = client.get("/zb_api/chat/options")

    assert browser_response.status_code == 200
    assert api_response.status_code == 200
    assert browser_response.get_json()["defaults"]["preset_id"] == "balanced_mumon"
    assert api_response.get_json()["settings_version"] == browser_response.get_json()[
        "settings_version"
    ]


def test_chat_rejects_mid_session_settings_change(monkeypatch):
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})

    conversation_id, _messages, metadata = main.utipy.create_conversation(
        student="lock-test",
        settings={"preset_id": "balanced_mumon"},
    )

    client = main.app.test_client()
    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
            "settings": {"preset_id": "explanatory_guide"},
        },
    )

    payload = response.get_json()
    assert response.status_code == 409
    assert payload["error"] == "settings_locked"
    assert payload["session_settings"]["preset_id"] == metadata["session_settings"][
        "preset_id"
    ]


def test_streaming_chat_emits_session_settings_in_start_event(monkeypatch):
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy.config, "STREAMING", True)
    monkeypatch.setattr(main.utipy, "get_model_reply", lambda *args, **kwargs: "tool-ready answer")

    def _unexpected_stream(*args, **kwargs):
        raise AssertionError("stream path should not be used when function tools are enabled")

    monkeypatch.setattr(main.utipy, "get_model_stream", _unexpected_stream)

    client = main.app.test_client()
    response = client.post(
        "/chat",
        json={
            "message": "show me the way",
            "settings": {"preset_id": "balanced_mumon"},
        },
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert '"event": "start"' in body
    assert '"session_settings"' in body
    assert '"preset_id": "balanced_mumon"' in body
    assert '"response": "tool-ready answer"' in body


def test_chat_case_and_zb_api_chat_case_return_resolved_settings(monkeypatch):
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    client = main.app.test_client()

    browser_response = client.post(
        "/chat_case/1",
        json={"settings": {"preset_id": "austere_abbot"}},
    )
    api_response = client.post(
        "/zb_api/chat_case/1",
        json={"settings": {"preset_id": "explanatory_guide"}},
    )

    assert browser_response.status_code == 200
    assert api_response.status_code == 200
    assert browser_response.get_json()["session_settings"]["preset_id"] == "austere_abbot"
    assert (
        api_response.get_json()["session_settings"]["preset_id"]
        == "explanatory_guide"
    )
