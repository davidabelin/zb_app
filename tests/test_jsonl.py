# tests/test_jsonl.py
import json
from utilities import to_jsonl  # once you’ve factored it out

def test_to_jsonl_roundtrip(tmp_path):
    params = {"foo": "bar"}
    messages = [{"role": "user", "content": "Hello"}]
    jsonl = to_jsonl(params, messages)
    # Split lines, parse back to Python objects
    lines = jsonl.splitlines()
    assert json.loads(lines[0]) == params
    assert json.loads(lines[1]) == messages[0]
