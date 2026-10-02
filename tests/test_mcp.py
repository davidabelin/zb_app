"""Protocol contracts, workflow parity, authorization, and durable retry failures."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
from threading import Event, RLock, Thread
from types import SimpleNamespace

import jsonschema
import pytest

import main
import session_reviews
import storage_policy
import utilities
from zb_mcp import server as adapter
from zb_mcp.auth import principal
from zb_mcp.operations import OperationStore, STALE_AFTER
from zb_services import ServiceError, conversations

REAL_MODEL_REPLY = utilities.get_model_reply


class MemoryFirestore:
    """Transactional test double supporting both operation and conversation storage."""

    def __init__(self):
        self.data = {}
        self.lock = RLock()

    def collection(self, name):
        return SimpleNamespace(document=lambda key: Document(self, name + "/" + key))

    def transaction(self):
        return Transaction(self)


class Document:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.rsplit("/", 1)[-1]

    def get(self, **kwargs):
        value = deepcopy(self.db.data.get(self.path))
        return SimpleNamespace(exists=value is not None, to_dict=lambda: value)

    def set(self, value, merge=False):
        self.db.data[self.path] = (
            {**self.db.data.get(self.path, {}), **deepcopy(value)}
            if merge
            else deepcopy(value)
        )

    def delete(self):
        self.db.data.pop(self.path, None)


class Transaction:
    def __init__(self, db):
        self.db = db
        self.pending = []

    def set(self, ref, value):
        self.pending.append(lambda: ref.set(value))

    def delete(self, ref):
        self.pending.append(ref.delete)


@pytest.fixture
def db(monkeypatch):
    from zb_mcp import operations

    def transactional(function):
        def call(transaction):
            with transaction.db.lock:
                result = function(transaction)
                for mutation in transaction.pending:
                    mutation()
                return result

        return call

    monkeypatch.setattr(operations.firestore, "transactional", transactional)
    return MemoryFirestore()


@pytest.fixture
def runtime(monkeypatch, fake_bucket, db):
    monkeypatch.setattr(utilities, "DB", db)
    monkeypatch.setattr(utilities, "REDIS", None)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    monkeypatch.setattr(utilities.config, "HOT_STATE_BACKEND", "firestore")
    monkeypatch.setattr(utilities.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(utilities.config, "ACTION_API_TOKEN", "test-token")
    monkeypatch.setattr(utilities, "_utc_now", lambda: "2026-09-23T12:00:00Z")
    monkeypatch.setattr(conversations, "_utc_now", lambda: "2026-09-23T12:00:00Z")
    monkeypatch.setattr(utilities, "get_cid", lambda *args: "new-session")
    monkeypatch.setattr(
        utilities, "get_model_reply", lambda *args, **kwargs: "  Mumon says nothing.\n"
    )
    monkeypatch.setattr(
        utilities, "submit_background_session_critic", lambda *args: "critic-123"
    )
    fake_bucket.blob(utilities.config.MEMORY_LOGBOOK).upload_from_string(
        json.dumps([memory_entry()])
    )
    cid = conversations.start_case("1")["conversation_id"]
    conversations.chat("dog nature", cid)
    conversations.archive(cid)
    rows = session_reviews.load_review_state()
    rows[0] = session_reviews.apply_reviewer_decision(rows[0], "ZB", "Use")
    rows[0] = session_reviews.apply_reviewer_decision(rows[0], "CM", "Use")
    session_reviews.save_review_state(rows)
    conversations.start_case("1")
    token = SimpleNamespace(
        claims={
            "sub": "chief-sub",
            "email": "chief@example.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(adapter, "get_access_token", lambda: token)
    operations = OperationStore(db)
    server = adapter.create_server(operations, frozenset({"chief@example.com"}))
    return SimpleNamespace(
        server=server, operations=operations, bucket=fake_bucket, db=db, token=token
    )


def memory_entry():
    return {
        "date": "2026-09-23",
        "time": "12:00",
        "serial_number": "001",
        "title": "Test memory",
        "koans_used": ["1"],
        "user_problem_or_questions": "dog nature",
        "response_summary": "test",
        "session_evaluations": [],
        "key_insights": [],
        "lessons_learned": [],
        "final_outcome": "test",
        "user_instructions": [],
    }


def call(runtime, name, arguments=None):
    return asyncio.run(runtime.server.call_tool(name, arguments or {}))


PARITY = [
    ("zbApiChatOptions", "get", "/zb_api/chat/options", {}),
    (
        "zbApiChat",
        "post",
        "/zb_api/chat",
        {"message": "dog nature", "conversation_id": "new-session"},
    ),
    ("zbApiChatCase", "post", "/zb_api/chat_case/1", {"case_id": "1"}),
    ("zbApiSaveChat", "post", "/zb_api/save_chat", {"conversation_id": "new-session"}),
    ("listConversations", "get", "/zb_api/conversations/list", {}),
    (
        "getConversation",
        "get",
        "/zb_api/conversations/new-session",
        {"conversation_id": "new-session"},
    ),
    ("searchExemplars", "get", "/zb_api/exemplars/search", {"query": "dog"}),
    ("loadMemoryLogbook", "get", "/zb_api/load_memory_logbook", {}),
    (
        "getMemoryLogbookEntry",
        "get",
        "/zb_api/load_memory_entry/001",
        {"serial_number": "001"},
    ),
    (
        "saveMemoryCandidate",
        "post",
        "/zb_api/memory/candidates",
        {"entry": memory_entry()},
    ),
    (
        "commitMemoryEntry",
        "post",
        "/zb_api/update_memory_logbook",
        {"entry": memory_entry()},
    ),
    ("getRandomKoan", "get", "/zb_api/get_random_koan", {}),
    ("getKoanByTitle", "get", "/zb_api/koans/by-title", {"title": "Joshu's Dog"}),
    ("getKoanById", "get", "/zb_api/koans/1", {"case_id": "1"}),
    ("getSessionEvaluationSummary", "get", "/zb_api/session-evaluations/summary", {}),
    (
        "getSessionEvaluationRecord",
        "get",
        "/zb_api/session-evaluations/record/0",
        {"index": 0},
    ),
    (
        "listSessionEvaluationsNeedingZbReview",
        "get",
        "/zb_api/session-evaluations/needs-zb-review",
        {},
    ),
    (
        "getRandomSessionEvaluationForZbReview",
        "get",
        "/zb_api/session-evaluations/random-zb-review",
        {},
    ),
    (
        "enqueueReview",
        "post",
        "/zb_api/session-evaluations/review-requests",
        {"conversation_id": "new-session"},
    ),
    (
        "setSessionEvaluationDecision",
        "post",
        "/zb_api/session-evaluations/record/0/decision",
        {"index": 0, "conversation_id": "new-session", "evaluation": "Alter"},
    ),
    (
        "getRandomSessionEvaluationSample",
        "get",
        "/zb_api/session-evaluations/random-sample",
        {},
    ),
    ("getNextSessionEvaluationRecord", "get", "/zb_api/session-evaluations/next", {}),
    (
        "reportRuntimeStatus",
        "post",
        "/zb_api/runtime/status-events",
        {"stage": "test", "detail": "private detail"},
    ),
]


@pytest.mark.parametrize("name,method,path,args", PARITY)
def test_every_api_operation_matches_mcp(runtime, name, method, path, args):
    before_db, before_bucket = deepcopy(runtime.db.data), deepcopy(
        runtime.bucket.storage
    )
    random.seed(42)
    response = main.app.test_client().open(
        path,
        method=method.upper(),
        headers={"Authorization": "Bearer test-token"},
        **({"json": args} if method == "post" else {"query_string": args}),
    )
    assert response.status_code == 200
    expected, expected_bucket = response.get_json(), deepcopy(runtime.bucket.storage)
    expected_conversations = {
        k: v for k, v in runtime.db.data.items() if k.startswith("conversations/")
    }
    runtime.db.data, runtime.bucket.storage = before_db, before_bucket
    random.seed(42)
    arguments = {**args, **({"operation_id": "op-parity"} if method == "post" else {})}
    result = call(runtime, name, arguments)
    assert not result.is_error, result
    assert result.structured_content == expected
    jsonschema.validate(result.structured_content, adapter.output_schema(name))
    assert runtime.bucket.storage == expected_bucket
    assert {
        k: v for k, v in runtime.db.data.items() if k.startswith("conversations/")
    } == expected_conversations


def test_discovery_and_schema_snapshot(runtime):
    tools = {tool.name: tool for tool in asyncio.run(runtime.server.list_tools())}
    names = {name for name, _, _, _ in PARITY}
    assert set(tools) == names | {"replaceMemoryLogbook", "getOperationStatus"}
    for name, method, _, _ in PARITY:
        assert tools[name].annotations.read_only_hint == (method == "get")
        if method == "post":
            assert "operation_id" in tools[name].parameters["required"]
    assert tools["replaceMemoryLogbook"].annotations.destructive_hint
    assert tools["zbApiSaveChat"].annotations.destructive_hint
    assert tools["zbApiSaveChat"].annotations.open_world_hint
    assert tools["zbApiChat"].annotations.open_world_hint
    assert (
        "conversation_id"
        in tools["setSessionEvaluationDecision"].parameters["required"]
    )


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": "a", "email": "chief@example.com", "email_verified": False},
        {"sub": "a", "email": "visitor@example.com", "email_verified": True},
    ],
)
def test_unauthorized_calls_have_no_effect(runtime, claims):
    runtime.token.claims = claims
    before = deepcopy(runtime.db.data), deepcopy(runtime.bucket.storage)
    result = call(
        runtime,
        "commitMemoryEntry",
        {"operation_id": "denied", "entry": memory_entry()},
    )
    assert result.is_error and result.structured_content["error"] == "forbidden"
    assert (runtime.db.data, runtime.bucket.storage) == before


def test_empty_allowlist_denies_verified_account(runtime):
    with pytest.raises(ServiceError):
        principal(runtime.token, frozenset())


def test_dokusan_and_duplicate_turn_preserve_exact_text(runtime):
    args = {
        "operation_id": "turn-1",
        "message": "  dog nature\n",
        "conversation_id": "new-session",
    }
    first = call(runtime, "zbApiChat", args)
    second = call(runtime, "zbApiChat", args)
    assert first.structured_content == second.structured_content
    assert first.content[0].text == "  Mumon says nothing.\n"
    messages, _ = utilities.get_conversation_state("new-session")
    assert [m["content"] for m in messages].count(args["message"]) == 1
    call(runtime, "zbApiChat", {**args, "operation_id": "closing", "message": "(bows)"})
    archived = call(
        runtime,
        "zbApiSaveChat",
        {"operation_id": "archive-1", "conversation_id": "new-session"},
    )
    repeated = call(
        runtime,
        "zbApiSaveChat",
        {"operation_id": "archive-1", "conversation_id": "new-session"},
    )
    assert archived.structured_content == repeated.structured_content
    transcript = call(
        runtime, "getConversation", {"conversation_id": "new-session"}
    ).structured_content["messages"]
    assert transcript[-2]["content"] == "(bows)"
    assert not utilities.get_conversation_state("new-session")[1]


@pytest.mark.parametrize(
    "name,args",
    [
        ("zbApiChat", {"operation_id": "blank", "message": " \n"}),
        ("loadMemoryLogbook", {"limit": 26}),
        (
            "setSessionEvaluationDecision",
            {"operation_id": "review", "index": 0, "evaluation": "Use"},
        ),
    ],
)
def test_invalid_tool_inputs_do_not_claim_operations(runtime, name, args):
    before = deepcopy(runtime.db.data)
    with pytest.raises(Exception):
        call(runtime, name, args)
    assert runtime.db.data == before


def test_changed_arguments_reject_reused_id(runtime):
    args = {"operation_id": "commit-1", "entry": memory_entry()}
    assert not call(runtime, "commitMemoryEntry", args).is_error
    result = call(
        runtime,
        "commitMemoryEntry",
        {**args, "entry": {**memory_entry(), "title": "changed"}},
    )
    assert result.structured_content["error"] == "operation_id_conflict"
    assert len(utilities.load_memory_logbook()) == 2


def test_interruption_blocks_replay_and_competing_writes(db):
    clock = [datetime.now(timezone.utc)]
    store = OperationStore(db, clock=lambda: clock[0])
    effects = []

    def interrupted():
        effects.append("external write")
        raise SystemExit("process died")

    with pytest.raises(SystemExit):
        store.execute("chief", "interrupted", "write", {}, ["memory"], interrupted)
    clock[0] += STALE_AFTER + timedelta(seconds=1)
    restarted = OperationStore(db, clock=lambda: clock[0])
    assert restarted.status("chief", "interrupted")["state"] == "uncertain"
    with pytest.raises(ServiceError, match="Inspect"):
        restarted.execute("chief", "interrupted", "write", {}, ["memory"], interrupted)
    with pytest.raises(ServiceError, match="owns this resource"):
        restarted.execute("chief", "new-id", "write", {}, ["memory"], interrupted)
    assert effects == ["external write"]


def test_concurrent_duplicates_execute_once(db):
    store = OperationStore(db)
    entered, finish = Event(), Event()
    effects = []

    def action():
        effects.append("write")
        entered.set()
        assert finish.wait(5)
        return {"status": "success"}

    thread = Thread(
        target=lambda: store.execute(
            "chief", "same-id", "write", {}, ["memory"], action
        )
    )
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ServiceError):
            store.execute("chief", "same-id", "write", {}, ["memory"], action)
    finally:
        finish.set()
        thread.join(5)
    assert effects == ["write"]
    assert store.execute("chief", "same-id", "write", {}, ["memory"], action) == {
        "status": "success"
    }


def test_receipt_failure_does_not_repeat_effects(db, monkeypatch):
    store = OperationStore(db)
    effects = []
    monkeypatch.setattr(
        store,
        "finish",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(ServiceError, match="receipt"):
        store.execute(
            "chief",
            "receipt-failure",
            "write",
            {},
            ["memory"],
            lambda: effects.append(1) or {},
        )
    with pytest.raises(ServiceError):
        store.execute(
            "chief",
            "receipt-failure",
            "write",
            {},
            ["memory"],
            lambda: effects.append(2) or {},
        )
    assert effects == [1]


def test_strict_storage_does_not_change_legacy_fallback(monkeypatch):
    monkeypatch.setattr(utilities, "DB", None)
    monkeypatch.setattr(utilities, "REDIS", None)
    monkeypatch.setattr(utilities, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(utilities.config, "HOT_STATE_BACKEND", "firestore")
    with (
        storage_policy.durable_storage(),
        pytest.raises(storage_policy.StorageUnavailable),
    ):
        utilities.save_messages_to_firestore("strict", [], {})
    assert not utilities._LOCAL_CONVERSATIONS
    utilities.save_messages_to_firestore("legacy", [], {})
    assert "legacy" in utilities._LOCAL_CONVERSATIONS


def test_full_replacement_and_operation_receipt(runtime):
    result = call(
        runtime, "replaceMemoryLogbook", {"operation_id": "replace", "full_logbook": []}
    )
    assert result.structured_content["count"] == 0
    receipt = call(runtime, "getOperationStatus", {"operation_id": "replace"})
    assert receipt.structured_content["state"] == "completed"
    assert receipt.structured_content["result"] == result.structured_content
    jsonschema.validate(
        receipt.structured_content, adapter.output_schema("getOperationStatus")
    )


def test_stale_review_rejection_keeps_error_and_releases_lock(runtime):
    result = call(
        runtime,
        "setSessionEvaluationDecision",
        {
            "operation_id": "stale-index",
            "index": 0,
            "conversation_id": "wrong-conversation",
            "evaluation": "Reject",
        },
    )
    assert result.is_error
    assert result.structured_content["error"] == "review_record_mismatch"
    assert runtime.operations.status("chief-sub", "stale-index")["state"] == "completed"
    assert not call(
        runtime, "reportRuntimeStatus", {"operation_id": "after-error", "stage": "test"}
    ).is_error


def test_streamable_http_oauth_discovery_and_authenticated_tools(runtime, monkeypatch):
    from fastmcp.server.auth import AccessToken
    from fastmcp.server.auth.providers.google import GoogleProvider
    from key_value.aio.stores.memory import MemoryStore
    from starlette.testclient import TestClient

    oauth = GoogleProvider(
        client_id="test-client",
        client_secret="test-secret",
        base_url="http://localhost:8000",
        jwt_signing_key="test-signing-key" * 3,
        client_storage=MemoryStore(),
        required_scopes=["openid", "email"],
    )

    async def verify(token):
        if token != "valid":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
            claims=runtime.token.claims,
        )

    monkeypatch.setattr(oauth, "verify_token", verify)
    # Use the actual request's auth context for this test, not the unit-test token shim.
    from fastmcp.server.dependencies import get_access_token

    monkeypatch.setattr(adapter, "get_access_token", get_access_token)
    server = adapter.create_server(
        runtime.operations, frozenset({"chief@example.com"}), auth=oauth
    )
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "zenbot-test", "version": "1"},
        },
    }
    with TestClient(
        server.http_app(path="/mcp", stateless_http=True, json_response=True)
    ) as client:
        discovery = client.get("/.well-known/oauth-authorization-server")
        assert discovery.status_code == 200
        assert "S256" in discovery.json()["code_challenge_methods_supported"]
        assert client.post("/mcp", headers=headers, json=initialize).status_code == 401
        headers["Authorization"] = "Bearer invalid"
        assert client.post("/mcp", headers=headers, json=initialize).status_code == 401
        headers["Authorization"] = "Bearer valid"
        response = client.post("/mcp", headers=headers, json=initialize)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/json")
        assert "instructions" in response.json()["result"]
        listing = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert len(listing.json()["result"]["tools"]) == 25
        tool = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "getKoanById", "arguments": {"case_id": "1"}},
            },
        )
        assert tool.json()["result"]["structuredContent"]["id"] == 1


def test_contract_export_matches_checked_in_snapshot():
    import runpy

    root = Path(__file__).parents[1]
    build_snapshot = runpy.run_path(str(root / "scripts/export_mcp_output_schemas.py"))[
        "build_snapshot"
    ]
    source = root.parent / "zenbot_knowledge/action_schemas.json"
    if not source.exists():
        pytest.skip("Actions schema lives in the sibling knowledge repository")
    assert build_snapshot(json.loads(source.read_text(encoding="utf-8"))) == json.loads(
        (root / "zb_mcp/output_schemas.json").read_text(encoding="utf-8")
    )


def test_reconciliation_preserves_receipt_and_releases_only_owned_locks(db):
    now = datetime.now(timezone.utc)
    store = OperationStore(db, clock=lambda: now)
    store.claim("chief", "interrupted", "write", {}, ["memory"])
    row = store.reference("chief", "interrupted").get().to_dict()
    assert "expires_at" not in row
    with pytest.raises(ServiceError, match="No operation"):
        store.status("visitor", "interrupted")
    with pytest.raises(ValueError):
        store.reconcile("visitor", "interrupted", {"status": "success"}, "checked")
    result = {
        "status": "failure",
        "error": "operator_reconciled",
        "message": "Verified partial work repaired",
    }
    store.reconcile(
        "chief",
        "interrupted",
        result,
        "Worker stopped; verified archive and repaired review export",
    )
    reconciled = store.reference("chief", "interrupted").get().to_dict()
    assert reconciled["expires_at"] == now + timedelta(days=7)
    assert reconciled["reconciliation"]["previous_state"] == "running"
    assert (
        store.execute(
            "chief",
            "interrupted",
            "write",
            {},
            ["memory"],
            lambda: pytest.fail("replayed"),
        )
        == result
    )
    assert store.claim("chief", "next", "write", {}, ["memory"])[0]
    with pytest.raises(ValueError):
        store.reconcile("chief", "interrupted", result, "again")


def test_missing_recovery_lock_cannot_be_released(db):
    store = OperationStore(db)
    store.claim("chief", "interrupted", "write", {}, ["memory"])
    for key, value in db.data.items():
        if key.startswith("zenbot_mcp_locks/"):
            value["operation"] = "different-operation"
    with pytest.raises(ValueError, match="owned by another"):
        store.reconcile("chief", "interrupted", {"status": "success"}, "checked")
    assert store.status("chief", "interrupted")["state"] == "running"


def test_claim_outage_performs_no_work(runtime, monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("Firestore offline")

    monkeypatch.setattr(runtime.operations, "claim", offline)
    before = deepcopy(runtime.bucket.storage), deepcopy(runtime.db.data)
    result = call(
        runtime,
        "commitMemoryEntry",
        {"operation_id": "outage", "entry": memory_entry()},
    )
    assert result.structured_content["error"] == "storage_unavailable"
    assert (runtime.bucket.storage, runtime.db.data) == before


@pytest.mark.parametrize("backend", ["firestore", "redis"])
@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_strict_conversation_outages_never_fall_back(
    runtime, monkeypatch, backend, operation
):
    def offline(*args, **kwargs):
        raise OSError("Storage unavailable")

    if backend == "firestore":
        monkeypatch.setattr(
            Document,
            {"read": "get", "write": "set", "delete": "delete"}[operation],
            offline,
        )
    else:
        monkeypatch.setattr(utilities.config, "HOT_STATE_BACKEND", "redis")
        monkeypatch.setattr(
            utilities,
            "REDIS",
            SimpleNamespace(get=offline, setex=offline, delete=offline),
        )
    before = deepcopy(runtime.db.data), deepcopy(utilities._LOCAL_CONVERSATIONS)
    action = {
        "read": lambda: utilities.get_conversation_state("new-session"),
        "write": lambda: utilities.save_messages_to_firestore("new-session", [], {}),
        "delete": lambda: utilities.delete_messages_from_firestore("new-session"),
    }[operation]
    with (
        storage_policy.durable_storage(),
        pytest.raises(storage_policy.StorageUnavailable),
    ):
        action()
    assert (runtime.db.data, utilities._LOCAL_CONVERSATIONS) == before


@pytest.mark.parametrize(
    "payload", ["[{broken", '{"title":"first"}\nnot-json\n', "[null]"]
)
def test_corrupt_memory_cannot_be_silently_overwritten(runtime, payload):
    blob = runtime.bucket.blob(utilities.config.MEMORY_LOGBOOK)
    blob.upload_from_string(payload)
    result = call(
        runtime,
        "commitMemoryEntry",
        {"operation_id": "corrupt", "entry": memory_entry()},
    )
    assert (
        result.is_error and result.structured_content["error"] == "operation_uncertain"
    )
    assert blob.download_as_text() == payload


def test_internal_storage_failure_propagates_in_strict_context(runtime, monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(utilities, "_write_jsonl_record", offline)
    with storage_policy.durable_storage(), pytest.raises(OSError):
        utilities._dispatch_function_tool(
            "enqueue_review", '{"conversation_id":"test"}'
        )
    assert not utilities._dispatch_function_tool(
        "enqueue_review", '{"conversation_id":"test"}'
    )["ok"]


def test_model_failure_after_internal_write_is_never_retried(runtime, monkeypatch):
    # Exercise the real tool loop, bypassing only request construction and the model network.
    monkeypatch.setattr(utilities, "get_model_reply", REAL_MODEL_REPLY)
    calls, options = [], []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                id="response-1",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call-1",
                        name="enqueue_review",
                        arguments='{"conversation_id":"new-session"}',
                    )
                ],
            )
        raise TimeoutError("model response lost after queue write")

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kwargs: options.append(kwargs) or client
    monkeypatch.setattr(utilities, "BOTLING", client)
    monkeypatch.setattr(
        utilities, "_response_request_kwargs", lambda *args, **kwargs: {}
    )
    args = {
        "operation_id": "model-interruption",
        "message": "dog nature",
        "conversation_id": "new-session",
    }
    result = call(runtime, "zbApiChat", args)
    assert (
        result.is_error and result.structured_content["error"] == "operation_uncertain"
    )
    assert call(runtime, "zbApiChat", args).is_error
    assert len(calls) == 2 and options == [{"max_retries": 0}]
    queued = runtime.bucket.blob(
        utilities.config.REVIEW_REQUESTS_BLOB
    ).download_as_text()
    assert len(queued.splitlines()) == 1


def test_production_factory_health_and_fail_closed_configuration(runtime, monkeypatch):
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.providers.google import GoogleProvider
    from key_value.aio.stores.memory import MemoryStore
    from starlette.testclient import TestClient
    from zb_mcp import app as production
    from zb_mcp.settings import Settings

    settings = Settings(
        "test-project",
        "http://localhost:8000",
        frozenset({"chief@example.com"}),
        "test-client",
        "test-secret",
        "test-signing-key" * 3,
        Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(Settings, "from_environment", classmethod(lambda cls: settings))
    monkeypatch.setattr(
        production,
        "provider",
        lambda value: GoogleProvider(
            client_id=value.google_client_id,
            client_secret=value.google_client_secret,
            base_url=value.base_url,
            jwt_signing_key=value.signing_key,
            client_storage=MemoryStore(),
        ),
    )
    blob_type = type(runtime.bucket.blob(""))
    monkeypatch.setattr(
        blob_type, "exists", lambda self, **kwargs: self.name in self.bucket.storage
    )
    with TestClient(production.create_app(), base_url=settings.base_url) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        assert client.get("/.well-known/oauth-authorization-server").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        monkeypatch.setattr(
            Document,
            "get",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
        )
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200
    monkeypatch.setattr(utilities, "BUCKET", None)
    with pytest.raises(RuntimeError, match="Firestore and GCS"):
        production.create_app()


@pytest.mark.parametrize(
    "name",
    [
        "MCP_GOOGLE_CLIENT_ID",
        "MCP_GOOGLE_CLIENT_SECRET",
        "MCP_SIGNING_KEY",
        "MCP_ENCRYPTION_KEY",
    ],
)
def test_production_requires_secret_manager(monkeypatch, name):
    from google.cloud import secretmanager
    from zb_mcp.settings import Settings

    monkeypatch.setenv("GAE_ENV", "standard")
    monkeypatch.setenv("MCP_BASE_URL", "https://example.com")
    monkeypatch.setenv(name, "plaintext-not-allowed-in-production")
    for key in (
        "MCP_GOOGLE_CLIENT_ID",
        "MCP_GOOGLE_CLIENT_SECRET",
        "MCP_SIGNING_KEY",
        "MCP_ENCRYPTION_KEY",
    ):
        monkeypatch.setenv(key + "_SECRET_NAME", "test-secret")
    monkeypatch.delenv(name + "_SECRET_NAME", raising=False)
    monkeypatch.setattr(
        secretmanager,
        "SecretManagerServiceClient",
        lambda: SimpleNamespace(
            access_secret_version=lambda **kwargs: SimpleNamespace(
                payload=SimpleNamespace(data=b"test-value")
            ),
        ),
    )
    with pytest.raises(ValueError, match=name + "_SECRET_NAME"):
        Settings.from_environment()


@pytest.mark.parametrize(
    "path,payload,message",
    [
        (
            "/zb_api/memory/candidates",
            {},
            "Memory candidate payload failed validation.",
        ),
        (
            "/zb_api/session-evaluations/review-requests",
            {},
            "Review request payload failed validation.",
        ),
        (
            "/zb_api/runtime/status-events",
            {},
            "Runtime status payload failed validation.",
        ),
    ],
)
def test_rest_validation_messages_remain_unchanged(runtime, path, payload, message):
    response = main.app.test_client().post(
        path, json=payload, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 400
    assert response.get_json()["message"] == message


def test_runtime_status_does_not_log_private_content(runtime, caplog):
    with caplog.at_level("INFO"):
        call(
            runtime,
            "reportRuntimeStatus",
            {
                "operation_id": "status-log",
                "stage": "private-stage",
                "detail": "private-transcript",
            },
        )
    assert "status-log" in caplog.text
    assert (
        "private-stage" not in caplog.text and "private-transcript" not in caplog.text
    )


def test_oauth_state_is_encrypted_namespaced_and_shared_across_instances(monkeypatch):
    from cryptography.fernet import Fernet
    from key_value.aio.stores.memory import MemoryStore
    from zb_mcp import auth
    from zb_mcp.settings import Settings

    backend = MemoryStore()
    monkeypatch.setattr(auth, "FirestoreStore", lambda **kwargs: backend)
    settings = Settings(
        "test-project",
        "https://zenbot.example.com",
        frozenset({"chief@example.com"}),
        "test-client",
        "test-secret",
        "shared-signing-key" * 3,
        Fernet.generate_key().decode(),
    )
    first, second = auth.provider(settings), auth.provider(settings)

    async def exercise():
        sensitive = {"refresh_token": "private-refresh-credential"}
        await first._client_storage.put(
            "oauth-state", sensitive, collection="test-state", ttl=60
        )
        stored = await backend.get(
            "oauth-state", collection="zenbot_mcp_oauth___test-state"
        )
        assert stored and "private-refresh-credential" not in json.dumps(stored)
        assert await backend.get("oauth-state", collection="test-state") is None
        assert (
            await second._client_storage.get("oauth-state", collection="test-state")
            == sensitive
        )

    asyncio.run(exercise())
