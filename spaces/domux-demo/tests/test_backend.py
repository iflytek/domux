import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domux_backend import OpenAIBackend, backend_from_env, run, split_slots  # noqa: E402


def test_split_multi_action_output():
    rows, ok = split_slots(
        "turnOff|Light|*|*|*|Living Room|Ground Floor\n"
        "set|AC|temperature|24|Celsius|Guest Bedroom|*\n"
    )
    assert ok
    assert rows == [
        ["turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"],
        ["set", "AC", "temperature", "24", "Celsius", "Guest Bedroom", "*"],
    ]


def test_ampersand_separator_matches_eval_script():
    rows, ok = split_slots("turnOn|Light|*|*|*|*|*&turnOff|AC|*|*|*|*|*")
    assert ok and len(rows) == 2


def test_malformed_line_is_flagged_and_padded():
    rows, ok = split_slots("turnOn|Light|*")
    assert not ok
    assert rows == [["turnOn", "Light", "*", "", "", "", ""]]


def test_empty_output_is_not_compliant():
    assert split_slots("  \n") == ([], False)


def test_run_rejects_empty_and_long_queries():
    with pytest.raises(ValueError):
        run(lambda q: "", "   ")
    with pytest.raises(ValueError):
        run(lambda q: "", "x" * 1001)


def test_run_strips_query_and_times_call():
    seen = []
    result = run(lambda q: seen.append(q) or "turnOn|Light|*|*|*|Kitchen|*", "  light on  ")
    assert seen == ["light on"]
    assert result.format_ok and result.rows[0][5] == "Kitchen"
    assert result.latency_ms >= 0


def test_openai_backend_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": " set|AC|mode|Cool|*|*|* "}}]}

    backend = OpenAIBackend("http://localhost:8000/v1/", "domux", "token")

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse()

    monkeypatch.setattr(backend._session, "post", fake_post)
    assert backend.generate("cool mode") == "set|AC|mode|Cool|*|*|*"
    assert captured["url"] == "http://localhost:8000/v1/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["json"]["temperature"] == 0.0
    assert captured["json"]["messages"] == [{"role": "user", "content": "cool mode"}]


def test_backend_from_env_prefers_endpoint(monkeypatch):
    monkeypatch.setenv("DOMUX_API_BASE", "http://vllm:8000/v1")
    monkeypatch.setenv("DOMUX_API_MODEL", "domux-served")
    backend = backend_from_env()
    assert isinstance(backend, OpenAIBackend)
    assert backend.model == "domux-served"


@pytest.mark.skipif(
    not os.getenv("DOMUX_API_BASE"), reason="app import test runs with an endpoint backend"
)
def test_app_builds_without_loading_model():
    import app

    assert app.demo is not None
