import os
import sys

import pytest
from google.api_core.exceptions import PreconditionFailed

# Add the project root (one level up) to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name

    @property
    def generation(self):
        return self.bucket.generations.get(self.name)

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if if_generation_match is not None:
            current = self.bucket.generations.get(self.name, 0)
            if current != if_generation_match:
                raise PreconditionFailed("generation mismatch")
        if isinstance(data, bytes):
            payload = data
        else:
            payload = str(data).encode("utf-8")
        self.bucket.storage[self.name] = payload
        self.bucket.generations[self.name] = self.bucket.generations.get(self.name, 0) + 1

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
        self.bucket.generations.pop(self.name, None)


class FakeBucket:
    def __init__(self, initial=None):
        self.storage = {}
        self.generations = {}
        for name, value in (initial or {}).items():
            self.storage[name] = (
                value if isinstance(value, bytes) else str(value).encode("utf-8")
            )
            self.generations[name] = 1

    def blob(self, name):
        return FakeBlob(self, name)

    def get_blob(self, name):
        return FakeBlob(self, name) if name in self.storage else None

    def list_blobs(self, prefix=""):
        names = sorted(name for name in self.storage if name.startswith(prefix))
        return [FakeBlob(self, name) for name in names]


@pytest.fixture
def fake_bucket():
    return FakeBucket()
