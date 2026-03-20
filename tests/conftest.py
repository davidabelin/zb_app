import os
import sys

import pytest

# Add the project root (one level up) to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name

    def upload_from_string(self, data, content_type=None):
        if isinstance(data, bytes):
            payload = data
        else:
            payload = str(data).encode("utf-8")
        self.bucket.storage[self.name] = payload

    def download_as_text(self):
        return self.bucket.storage[self.name].decode("utf-8")

    def download_as_bytes(self):
        return self.bucket.storage[self.name]

    def download_as_string(self):
        return self.bucket.storage[self.name]

    def exists(self):
        return self.name in self.bucket.storage

    def delete(self):
        self.bucket.storage.pop(self.name, None)


class FakeBucket:
    def __init__(self, initial=None):
        self.storage = {}
        for name, value in (initial or {}).items():
            self.storage[name] = (
                value if isinstance(value, bytes) else str(value).encode("utf-8")
            )

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix=""):
        names = sorted(name for name in self.storage if name.startswith(prefix))
        return [FakeBlob(self, name) for name in names]


@pytest.fixture
def fake_bucket():
    return FakeBucket()
