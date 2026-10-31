import json

import pytest

import utilities


class AuthenticationFailure(Exception):
    status_code = 401

    def __init__(self):
        super().__init__("401 invalid_organization")


def test_streaming_auth_failure_is_not_retried_or_sent_to_fallback(monkeypatch):
    calls = {"stream": 0, "reply": 0}

    class Responses:
        def stream(self, **kwargs):
            calls["stream"] += 1
            raise AuthenticationFailure()

        def create(self, **kwargs):
            calls["reply"] += 1
            raise AssertionError("Authentication failure must not use fallback")

    monkeypatch.setattr(
        utilities,
        "_get_openai_client",
        lambda: type("Client", (), {"responses": Responses()})(),
    )
    monkeypatch.setattr(utilities.storage_policy, "is_strict", lambda: False)

    events = [
        json.loads(event.removeprefix("data: "))
        for event in utilities.prompt_and_stream([], "hello", {}, "test-conversation")
    ]

    assert calls == {"stream": 1, "reply": 0}
    assert events[-2] == {
        "event": "error",
        "error": "Mumonbot's OpenAI organization access is unavailable. Please contact the site administrator.",
    }
    assert events[-1] == {"response": "[DONE]"}


def test_non_streaming_auth_failure_is_not_retried(monkeypatch):
    calls = 0

    class Responses:
        def create(self, **kwargs):
            nonlocal calls
            calls += 1
            raise AuthenticationFailure()

    monkeypatch.setattr(
        utilities,
        "_get_openai_client",
        lambda: type("Client", (), {"responses": Responses()})(),
    )
    monkeypatch.setattr(utilities.storage_policy, "is_strict", lambda: False)

    with pytest.raises(utilities.ModelAPIError) as exc_info:
        utilities.get_model_reply([{"role": "user", "content": "hello"}], {})

    assert calls == 1
    assert exc_info.value.status_code == 401
