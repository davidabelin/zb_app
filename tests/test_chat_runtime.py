import main


def test_chat_preflight_allows_same_project_app_engine_origin(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "GOOGLE_CLOUD_PROJECT", "zenbot-434517")
    monkeypatch.setattr(
        main.utipy.config,
        "WEB_APP_ORIGIN",
        "https://zenbot-434517.uw.r.appspot.com",
    )
    monkeypatch.setattr(main.utipy.config, "CHAT_ALLOWED_ORIGINS", [])

    client = main.app.test_client()
    response = client.open(
        "/chat",
        method="OPTIONS",
        headers={
            "Origin": "https://zenbot-434517.uw.r.appspot.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 204
    assert (
        response.headers["Access-Control-Allow-Origin"]
        == "https://zenbot-434517.uw.r.appspot.com"
    )
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_chat_response_allows_explicit_extra_origin(monkeypatch):
    monkeypatch.setattr(
        main.utipy.config,
        "CHAT_ALLOWED_ORIGINS",
        ["https://shell.example.com"],
    )

    client = main.app.test_client()
    response = client.post(
        "/save_chat",
        json={},
        headers={"Origin": "https://shell.example.com"},
    )

    assert response.status_code == 400
    assert response.headers["Access-Control-Allow-Origin"] == "https://shell.example.com"


def test_chat_preflight_rejects_unknown_origin(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "GOOGLE_CLOUD_PROJECT", "zenbot-434517")
    monkeypatch.setattr(main.utipy.config, "CHAT_ALLOWED_ORIGINS", [])

    client = main.app.test_client()
    response = client.open(
        "/chat",
        method="OPTIONS",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 204
    assert "Access-Control-Allow-Origin" not in response.headers


def test_entrance_hall_replaces_public_splash():
    client = main.app.test_client()
    response = client.get("/")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Zenbot Zendo Entrance Hall" in body
    assert "/chatter" in body
    assert "Site map seed" in body
    assert "Resource guide placeholder" in body
    assert "zenbot_hall.png" in body


def test_legacy_splash_preserves_previous_public_page():
    client = main.app.test_client()
    response = client.get("/legacy-splash")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Koan Work with a Chatbot Emulation" in body
    assert "Dokusan Session with ZM Mumonbot" in body
    assert "/chatter" in body


def test_chatter_renders_workspace_and_session_settings_in_non_streaming_mode(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "STREAMING", False)

    client = main.app.test_client()
    response = client.get("/chatter")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Sanzen Room" in body
    assert "Session Setup" in body
    assert 'id="sessionSettingsPanel"' in body
    assert 'id="sessionPreset"' in body
    assert 'id="sessionModel"' in body
    assert 'id="sessionEnableFunctionTools"' in body
    assert 'id="chatResults"' in body
    assert 'id="chatInput"' in body
    assert 'id="chatSend"' in body
    assert 'id="chatSave"' in body
    assert 'id="chatEnd"' in body
    assert 'id="chatPrefacePanel"' in body
    assert "window.PUBLIC_SESSION_OPTIONS" in body
    assert "window.CHAT_API_BASE_URL" not in body


def test_chatter_renders_workspace_and_session_settings_in_streaming_mode(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "STREAMING", True)

    client = main.app.test_client()
    response = client.get("/chatter")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Sanzen Room" in body
    assert "Session Setup" in body
    assert 'id="sessionSettingsPanel"' in body
    assert 'id="chatResults"' in body
    assert 'id="chatInput"' in body
    assert "Streaming enabled" in body
    assert "window.CHAT_API_BASE_URL" not in body
