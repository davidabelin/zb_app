"""Public sampling preserves whole dialogues while keeping archive identities private."""

import json
from pathlib import Path

import pytest

import main
import sampled_sessions as ss


@pytest.fixture(autouse=True)
def isolated_historical_bucket(monkeypatch):
    """Keep route tests independent of live private cloud storage."""
    monkeypatch.setattr(main.utipy, "SAMPLED_HISTORICAL_BUCKET", None)


def transcript(number=0, metadata=None, messages=None):
    """Build an archive with a distinctive complete ritual and case reading."""
    rows = [
        metadata
        or {
            "model": "test-model",
            "student": "Alice",
            "created_at": "2026-01-02T03:04:05Z",
        }
    ]
    rows += messages or [
        {
            "role": "system",
            "content": "Original intro: You are Zenbot.\nKeep every word.",
        },
        {
            "role": "user",
            "content": f"(enters, bows, sits)\nMy name is Alice. Case {number}",
        },
        {"role": "system", "content": "Case reading: Joshu said Mu.\n\nThe full case."},
        {"role": "assistant", "content": "Alice, listen to Joshu."},
        {"role": "user", "content": "(bows)"},
        {"role": "assistant", "content": "(bows)"},
    ]
    return "\n".join(json.dumps(row) for row in rows)


class Blob:
    """A GCS double with generations, object metadata, and conditional creation."""

    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.metadata = bucket.metadata.get(name, {}).copy()
        self.generation = bucket.versions.get(name)

    def exists(self):
        return self.name in self.bucket.data

    def download_as_bytes(self):
        self.bucket.reads.append(self.name)
        if self.name in self.bucket.fail_reads:
            raise RuntimeError("read failed")
        return self.bucket.data[self.name]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        assert if_generation_match == 0
        assert not self.exists()
        self.bucket.put(self.name, data)
        self.bucket.metadata[self.name] = self.metadata


class Bucket:
    """Versioned object store supporting refresh and importer integration tests."""

    def __init__(self):
        self.data, self.versions, self.metadata = {}, {}, {}
        self.reads = []
        self.fail_reads = set()
        self.fail_listing = False

    def put(self, name, value):
        self.data[name] = value.encode() if isinstance(value, str) else value
        self.versions[name] = self.versions.get(name, 0) + 1

    def blob(self, name):
        return Blob(self, name)

    def list_blobs(self, prefix=""):
        if self.fail_listing:
            raise RuntimeError("listing failed")
        return [
            self.blob(name) for name in sorted(self.data) if name.startswith(prefix)
        ]


def test_public_route_without_auth_preserves_complete_escaped_dialogue(monkeypatch):
    bucket = Bucket()
    bucket.put(
        "zbchats/secret-Alice-id.jsonl",
        transcript(
            messages=[
                {"role": "system", "content": "You are Zenbot.\nCase: Joshu said Mu."},
                {
                    "role": "user",
                    "content": "My name is Alice.\n<script>alert('x')</script>\n  spaced  ",
                },
                {"role": "assistant", "content": "Alice, (bows)"},
            ]
        ),
    )
    monkeypatch.setattr(main.utipy, "BUCKET", bucket)
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "private-admin-token")
    monkeypatch.setattr(ss, "sampler", ss.ArchiveSampler())
    response = main.app.test_client().get("/sampled-sessions")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert "Location" not in response.headers
    assert "You are Zenbot.\nCase: Joshu said Mu." in body
    assert "My name is Student.\n&lt;script&gt;" in body
    assert "\n  spaced  " in body
    assert "Student, (bows)" in body
    assert "<script>alert" not in body
    for private in (
        "Alice",
        "secret-Alice-id",
        "private-admin-token",
        "conversation_id",
        "temperature",
    ):
        assert private not in body
    for label in (">System<", ">Student<", ">Mumonbot<"):
        assert label in body


def test_legacy_parts_remain_in_order_and_have_no_implicit_rewrites():
    parts = ["A\n\n  B  ", {"type": "text", "text": "C\r\nD"}]
    recording = ss.parse_recording(
        transcript(
            messages=[
                {"role": "system", "content": parts},
                {"role": "user", "content": [{"type": "text", "text": "(bows)"}]},
                {"role": "assistant", "content": "(bows)"},
            ]
        ),
        "old-20241115-205910.jsonl",
    )
    public = ss.public_recording(recording)
    assert public["messages"][0]["parts"] == ("A\n\n  B  ", "C\r\nD")
    assert [m["speaker"] for m in public["messages"]] == [
        "System",
        "Student",
        "Mumonbot",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        '{"role":"user","content":"start"}\nnot json',
        '{"role":"user","content":null}',
        '{"role":"tool","content":"hidden"}',
        '{"role":"system","content":[{"type":"image","url":"secret"}]}',
        '{"role":"assistant","content":["good",42]}',
        '{"model":"only metadata"}',
        "[]",
        '{"role":"user","content":"start"}\n{"model":"late metadata"}',
    ],
)
def test_invalid_recording_is_rejected_whole(broken):
    with pytest.raises(ValueError):
        ss.parse_recording(broken, "bad.jsonl")


def test_redaction_uses_name_boundaries_and_keeps_koan_names():
    recording = ss.parse_recording(
        transcript(
            metadata={"student": "Ann", "model": "gpt-test"},
            messages=[
                {"role": "system", "content": "Joshu and Mumon read the Annals."},
                {
                    "role": "user",
                    "content": "My name is Alice Jones. Did you call me a fool?",
                },
                {"role": "assistant", "content": "Ann, ALICE JONES, Joshu says Mu."},
            ],
        ),
        "zb-Ann-mmnk1-20260101-123456.jsonl",
    )
    public = ss.public_recording(recording)
    assert public["messages"][0]["parts"] == ("Joshu and Mumon read the Annals.",)
    assert public["messages"][1]["parts"] == (
        "My name is Student. Did you call me a fool?",
    )
    assert public["messages"][2]["parts"] == ("Student, Student, Joshu says Mu.",)
    koan = ss.parse_recording(
        transcript(
            metadata={"student": "Joshu"},
            messages=[{"role": "system", "content": "Joshu said Mu. Mumon comments."}],
        ),
        "old.jsonl",
    )
    assert ss.public_recording(koan)["messages"][0]["parts"] == (
        "Joshu said Mu. Mumon comments.",
    )


@pytest.mark.parametrize(
    "metadata,name,expected",
    [
        (
            {"created_at": "2026-02-03T04:05:06-07:00"},
            "old-20240101-000000.jsonl",
            "2026-02-03 04:05:06-07:00",
        ),
        ({"created_at": "2026-02-03"}, "old.jsonl", "2026-02-03"),
        (
            {"saved_at": "2026-01-01T00:00:00Z"},
            "zb-Alice-20241115-205910.jsonl",
            "2024-11-15 20:59:10",
        ),
        ({}, "user-20241128-17352.jsonl", "2024-11-28"),
        (
            {"conversation_id": "zb-Alice-20241115-205910"},
            "hash.jsonl",
            "2024-11-15 20:59:10",
        ),
        (
            {"created_at": "invalid", "saved_at": "2026-03-04T05:06:07Z"},
            "unknown.jsonl",
            "2026-03-04 05:06:07 UTC",
        ),
        ({"saved_at": "invalid"}, "20249999.jsonl", "Date unavailable"),
    ],
)
def test_recorded_date_precedence_and_no_guessed_timezone(metadata, name, expected):
    public = ss.public_recording(
        ss.parse_recording(transcript(metadata=metadata or {"other": 1}), name)
    )
    assert public["date"] == expected
    assert public["model"] == "Model unavailable"


def test_uniform_draw_uses_unique_complete_pool_and_fresh_draw(monkeypatch):
    bucket = Bucket()
    for n in range(7):
        bucket.put(f"zbchats/{n}.jsonl", transcript(n))
    bucket.put(ss.HISTORICAL_PREFIX + "copy.jsonl", transcript(0))
    bucket.put("session_reviews/index.jsonl", "must never read")
    draws = []

    def draw(pool, count):
        assert len(pool) == 7 and count == 5
        draws.append(1)
        return list(pool[:5] if len(draws) == 1 else pool[2:7])

    monkeypatch.setattr(ss.random, "sample", draw)
    sampler = ss.ArchiveSampler()
    first = sampler.sample(bucket, historical_bucket=bucket)
    reads = list(bucket.reads)
    second = sampler.sample(bucket, historical_bucket=bucket)
    assert first != second
    assert len(draws) == 2
    assert bucket.reads == reads
    assert len({r["messages"][1]["parts"] for r in first}) == 5
    assert all(name.startswith(ss.ARCHIVE_PREFIXES) for name in reads)


def test_refresh_reads_only_changes_and_observes_additions_deletions(monkeypatch):
    now = [0]
    monkeypatch.setattr(ss.time, "monotonic", lambda: now[0])
    bucket = Bucket()
    bucket.put("zbchats/a.jsonl", transcript(0))
    bucket.put("zbchats/b.jsonl", transcript(1))
    sampler = ss.ArchiveSampler()
    assert len(sampler.sample(bucket)) == 2
    bucket.put("zbchats/a.jsonl", transcript(2))
    bucket.put("zbchats/c.jsonl", transcript(3))
    assert len(sampler.sample(bucket)) == 2  # still inside the five-minute cache
    now[0] = 301
    bucket.reads.clear()
    assert len(sampler.sample(bucket)) == 3
    assert set(bucket.reads) == {"zbchats/a.jsonl", "zbchats/c.jsonl"}
    del bucket.data["zbchats/a.jsonl"]
    now[0] = 602
    bucket.reads.clear()
    sessions = sampler.sample(bucket)
    assert len(sessions) == 2 and bucket.reads == []
    assert not any("Case 2" in s["messages"][1]["parts"][0] for s in sessions)


def test_malformed_skipped_and_failed_refresh_never_serves_stale(monkeypatch):
    bucket = Bucket()
    bucket.put("zbchats/good.jsonl", transcript())
    bucket.put("zbchats/bad.jsonl", "not json")
    sampler = ss.ArchiveSampler(ttl=0)
    assert len(sampler.sample(bucket)) == 1
    bucket.fail_listing = True
    with pytest.raises(ss.ArchiveUnavailable):
        sampler.sample(bucket)
    bucket.fail_listing = False
    assert len(sampler.sample(bucket)) == 1
    bucket.put("zbchats/new.jsonl", transcript(1))
    bucket.fail_reads.add("zbchats/new.jsonl")
    with pytest.raises(ss.ArchiveUnavailable):
        sampler.sample(bucket)


def test_empty_and_unavailable_states(monkeypatch):
    monkeypatch.setattr(ss, "sampler", ss.ArchiveSampler())
    monkeypatch.setattr(main.utipy, "BUCKET", Bucket())
    client = main.app.test_client()
    empty = client.get("/sampled-sessions")
    assert empty.status_code == 200 and b"No sessions are available yet" in empty.data
    monkeypatch.setattr(main.utipy, "BUCKET", None)
    unavailable = client.get("/sampled-sessions")
    assert unavailable.status_code == 503
    assert b"temporarily unavailable" in unavailable.data
    assert unavailable.headers["Retry-After"] == "60"
    assert "no-store" in unavailable.headers["Cache-Control"]


def test_separate_historical_bucket_and_bucket_switch():
    current, historical = Bucket(), Bucket()
    current.put("zbchats/current.jsonl", transcript(1))
    historical.put(ss.HISTORICAL_PREFIX + "old.jsonl", transcript(2))
    sampler = ss.ArchiveSampler()
    assert len(sampler.sample(current, historical_bucket=historical)) == 2
    assert current.reads == ["zbchats/current.jsonl"]
    assert historical.reads == [ss.HISTORICAL_PREFIX + "old.jsonl"]
    assert len(sampler.sample(Bucket(), historical_bucket=historical)) == 1


def test_import_is_idempotent_readonly_by_default_and_preserves_sources(tmp_path):
    paths = [tmp_path / "local", tmp_path / "web"]
    for path in paths:
        path.mkdir()
    original = transcript(metadata={"model": "legacy", "student": "Alice"})
    source = paths[0] / "zb-Alice-20241115-205910.jsonl"
    source.write_text(original, encoding="utf-8")
    (paths[1] / "copy.jsonl").write_text(original, encoding="utf-8")
    (paths[1] / "broken.jsonl").write_text("not json", encoding="utf-8")
    bucket = Bucket()
    bucket.put("zbchats/admin.jsonl", transcript(99))
    before = bucket.data.copy()
    report = ss.import_historical(bucket, paths)
    assert report == {
        "scanned": 3,
        "invalid": 1,
        "duplicates": 1,
        "existing": 0,
        "pending": 1,
        "uploaded": 0,
    }
    assert bucket.data == before
    assert ss.import_historical(bucket, paths, apply=True)["uploaded"] == 1
    assert ss.import_historical(bucket, paths, apply=True)["existing"] == 1
    assert source.read_text(encoding="utf-8") == original
    assert bucket.data["zbchats/admin.jsonl"] == before["zbchats/admin.jsonl"]
    public = ss.ArchiveSampler().sample(bucket, historical_bucket=bucket)
    historical = next(s for s in public if s["model"] == "legacy")
    assert historical["date"] == "2024-11-15 20:59:10"
    assert "Alice" not in json.dumps(historical)


def test_public_navigation(monkeypatch):
    client = main.app.test_client()
    for path in ("/", "/intro-tour", "/gg"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/sampled-sessions"' in response.get_data(as_text=True)


def test_actual_historical_recordings_parse_without_losing_messages():
    root = Path(__file__).resolve().parents[2] / "collected_sessions"
    if not root.is_dir():
        pytest.skip("Historical originals are outside the deployed app checkout")
    paths = sorted(root.glob("*/*.jsonl"))
    assert paths
    for path in paths:
        payload = path.read_bytes()
        recording = ss.parse_recording(payload, path.name)
        rows = [
            json.loads(line)
            for line in payload.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        original_messages = [row for row in rows if "role" in row]
        assert len(recording.messages) == len(original_messages)
        assert recording.messages[0].role == "system"
        for message, original in zip(recording.messages, original_messages):
            content = original["content"]
            expected = (
                (content,)
                if isinstance(content, str)
                else tuple(p if isinstance(p, str) else p["text"] for p in content)
            )
            assert message.parts == expected
