import main


def test_chat_preflight_allows_same_project_app_engine_origin(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "GOOGLE_CLOUD_PROJECT", "zenbot-434517")
    monkeypatch.setattr(
        main.utipy.config,
        "WEB_APP_ORIGIN",
        "https://zb-chat-api-txk2gvimpq-uc.a.run.app",
    )
    monkeypatch.setattr(
        main.utipy.config,
        "CHAT_API_BASE_URL",
        "https://zb-chat-api-txk2gvimpq-uc.a.run.app",
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
