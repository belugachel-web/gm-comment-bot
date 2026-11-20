import json

import pytest

from gm_bot import gemini


class T:
    class GenerateContentConfig:
        def __init__(self, **kw):
            self.kw = kw


class Client:
    def __init__(self, fail_on, msg=None):
        self.calls, self.fail_on, self.msg, self.models = [], fail_on, msg, self

    def generate_content(self, model, contents, config):
        self.calls.append(set(config.kw))
        if self.fail_on in config.kw:
            raise Exception(self.msg or f"400 {self.fail_on} is not supported")
        return "ok"


def test_retry_without_temperature_when_model_rejects_it():
    c = Client("temperature")
    assert gemini._generate(c, "m", "p", {"temperature": 0.4, "thinking_config": 1}, T) == "ok"
    assert c.calls == [{"temperature", "thinking_config"}, {"thinking_config"}]


def test_retry_without_thinking_when_model_rejects_it():
    c = Client("thinking_config")
    assert gemini._generate(c, "m", "p", {"temperature": 0.4, "thinking_config": 1}, T) == "ok"
    assert c.calls[-1] == {"temperature"}


def test_rate_limit_error_is_not_swallowed():
    c = Client("temperature", "429 RESOURCE_EXHAUSTED temperature quota")
    with pytest.raises(Exception):
        gemini._generate(c, "m", "p", {"temperature": 0.4}, T)
    assert len(c.calls) == 1


COMMENT = {"id": "c1", "author": "A", "text": "x", "post": "p"}


def test_rate_limited_primary_goes_to_next_model_without_waiting(monkeypatch):
    slept = []
    monkeypatch.setattr(gemini, "_sleep", lambda s: slept.append(s))

    def call(k, model, *a):
        if model == "m1":
            raise Exception("429 RESOURCE_EXHAUSTED per minute")
        return json.dumps([{"id": "c1", "status": "OK", "reply": "hi"}])
    r = gemini.ask_batch([COMMENT], {"model": "m1", "fallback_model": ["m2"], "temperature": 0.4}, "", gemini.Keys("k1"), call=call)
    assert r["c1"]["status"] == "OK" and slept == []


def test_review_rate_limited_goes_to_next_model_without_waiting(monkeypatch):
    slept = []
    monkeypatch.setattr(gemini, "_sleep", lambda s: slept.append(s))

    def call(k, model, *a):
        if model == "m1":
            raise Exception("429 RESOURCE_EXHAUSTED per minute")
        return json.dumps([{"id": "c1", "verdict": "OK", "why": ""}])
    S = {"model": "m1", "review_model": "m1", "fallback_model": ["m2"]}
    r = gemini.review_batch([(COMMENT, "r")], S, "", gemini.Keys("k1"), call=call)
    assert r["c1"] is None and slept == []
