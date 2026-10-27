import pytest

import main
import utilities


def test_session_options_payload_exposes_defaults_presets_and_models():
    payload = utilities.session_options_payload()
    default_model = utilities.config.MODEL_NAME

    assert payload["settings_version"] == utilities.config.SESSION_SETTINGS_VERSION
    assert payload["defaults"]["preset_id"] == utilities.config.DEFAULT_BOTLING_ID
    assert any(preset["id"] == "balanced" for preset in payload["presets"])
    assert [model["id"] for model in payload["models"]] == [
        "gpt-5.6-sol",
        "gpt-6-sol",
        "gpt-6.1-sol",
    ]
    assert all(model["id"] != "set03-bs2lr05e7" for model in payload["models"])

    model = next(model for model in payload["models"] if model["id"] == default_model)
    assert model["supports_reasoning"] is True
    assert model["supports_sampling_controls"] is True
    assert [model["supports_sampling_controls"] for model in payload["models"]] == [
        True,
        True,
        False,
    ]
    assert payload["models"][0]["reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert payload["models"][1]["reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert payload["models"][2]["reasoning_efforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_resolve_session_settings_rejects_unavailable_finetune():
    with pytest.raises(ValueError, match="Unsupported model_name"):
        utilities.resolve_session_settings({"model_name": "set03-bs2lr05e7"})


@pytest.mark.parametrize("model_name", ["gpt-5.6-sol", "gpt-6-sol", "gpt-6.1-sol"])
def test_sol_session_params_use_reasoning_without_sampling_controls(model_name):
    settings = utilities.resolve_session_settings(
        {"model_name": model_name, "reasoning_effort": "high"}
    )
    params = utilities._params_from_session_settings(settings)

    assert params["model"] == model_name
    assert params["reasoning"]["effort"] == "high"
    assert params["max_output_tokens"] == settings["max_output_tokens"]
    assert "temperature" not in params
    assert "top_p" not in params


@pytest.mark.parametrize("model_name", ["gpt-5.6-sol", "gpt-6-sol"])
def test_none_reasoning_sends_sampling_controls(model_name):
    settings = utilities.resolve_session_settings(
        {
            "model_name": model_name,
            "reasoning_effort": "none",
            "temperature": 0.4,
            "top_p": 0.8,
        }
    )
    params = utilities._params_from_session_settings(settings)

    assert params["reasoning"]["effort"] == "none"
    assert params["temperature"] == 0.4
    assert params["top_p"] == 0.8


def test_sampling_controls_rejected_with_active_reasoning():
    with pytest.raises(ValueError, match="temperature is not supported"):
        utilities.resolve_session_settings(
            {
                "model_name": "gpt-6-sol",
                "reasoning_effort": "medium",
                "temperature": 0.4,
            }
        )


def test_gpt_6_1_sol_rejects_unsupported_none_reasoning():
    with pytest.raises(ValueError, match="Unsupported reasoning_effort"):
        utilities.resolve_session_settings(
            {"model_name": "gpt-6.1-sol", "reasoning_effort": "none"}
        )


def test_config_rejects_unregistered_openai_live_model():
    with pytest.raises(ValueError, match="OPENAI_LIVE_MODEL"):
        utilities.Config(
            MODELS_IN_USE={"set03-bs2lr05e7": "ft:active-model"},
            OPENAI_LIVE_MODEL="gpt-5.4-mini",
        )


def test_session_settings_from_legacy_metadata_reassigns_unavailable_finetune():
    settings = utilities.session_settings_from_metadata(
        {
            "model_name": "set03-bs2lr05e7",
            "profile": "live",
            "botling_id": "fierce_barrier",
            "params": {
                "model": "ft:retired-model",
                "max_output_tokens": 777,
                "temperature": 0.55,
                "top_p": 0.75,
            },
        }
    )

    assert settings["preset_id"] == "balanced"
    assert settings["model_name"] == "gpt-5.6-sol"
    assert settings["max_output_tokens"] == 1000
    assert settings["reasoning_effort"] == "medium"
    assert settings["temperature"] is None
    assert settings["top_p"] is None


def test_chat_and_zb_api_options_routes_return_session_payload(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    client = main.app.test_client()

    browser_response = client.get("/chat/options")
    api_response = client.get("/zb_api/chat/options")

    assert browser_response.status_code == 200
    assert api_response.status_code == 200
    assert browser_response.get_json()["defaults"]["preset_id"] == "balanced"
    assert api_response.get_json()["status"] == "success"
    assert (
        api_response.get_json()["settings_version"]
        == browser_response.get_json()["settings_version"]
    )


def test_chat_rejects_mid_session_settings_change(monkeypatch):
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})

    conversation_id, _messages, metadata = main.utipy.create_conversation(
        student="lock-test",
        settings={"preset_id": "balanced"},
    )

    client = main.app.test_client()
    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
            "settings": {"preset_id": "fierce_barrier"},
        },
    )

    payload = response.get_json()
    assert response.status_code == 409
    assert payload["error"] == "settings_locked"
    assert (
        payload["session_settings"]["preset_id"]
        == metadata["session_settings"]["preset_id"]
    )


def test_chat_first_turn_can_create_case_session_with_selected_settings(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "STREAMING", False)
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy, "get_model_reply", lambda *args, **kwargs: "ok")

    client = main.app.test_client()
    response = client.post(
        "/chat",
        json={
            "message": "What is the barrier?",
            "conversation_id": "",
            "case_id": "1",
            "settings": {"preset_id": "austere_abbot"},
        },
    )
    payload = response.get_json()
    messages, metadata = main.utipy.get_conversation_state(payload["conversation_id"])

    assert response.status_code == 200
    assert payload["session_settings"]["preset_id"] == "austere_abbot"
    assert metadata["case_id"] == "1"
    assert any("Joshu's Dog" in message["content"] for message in messages)


def test_streaming_chat_emits_session_settings_in_start_event(monkeypatch):
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy.config, "STREAMING", True)
    monkeypatch.setattr(
        main.utipy, "get_model_reply", lambda *args, **kwargs: "tool-ready answer"
    )

    def _unexpected_stream(*args, **kwargs):
        raise AssertionError(
            "stream path should not be used when function tools are enabled"
        )

    monkeypatch.setattr(main.utipy, "get_model_stream", _unexpected_stream)

    client = main.app.test_client()
    response = client.post(
        "/chat",
        json={
            "message": "show me the way",
            "settings": {"preset_id": "balanced"},
        },
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert '"event": "start"' in body
    assert '"session_settings"' in body
    assert '"preset_id": "balanced"' in body
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
        json={"settings": {"preset_id": "fierce_barrier"}},
    )

    assert browser_response.status_code == 200
    assert api_response.status_code == 200
    assert (
        browser_response.get_json()["session_settings"]["preset_id"] == "austere_abbot"
    )
    assert api_response.get_json()["session_settings"]["preset_id"] == "fierce_barrier"
