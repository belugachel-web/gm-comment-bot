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


def test_model_error_detection():
    assert gemini.model_error("404 NOT_FOUND. models/x is not found for API version v1beta")
    assert gemini.model_error("400 INVALID_ARGUMENT: model x is not supported")
    assert not gemini.model_error("503 UNAVAILABLE. The model is overloaded")
    assert not gemini.model_error("Connection reset")


def test_model_not_found_goes_to_fallback_without_retries(monkeypatch):
    slept, seen = [], []
    monkeypatch.setattr(gemini, "_sleep", lambda s: slept.append(s))

    def call(k, model, *a):
        seen.append(model)
        if model == "m1":
            raise Exception("404 NOT_FOUND. models/m1 is not found for API version v1beta")
        return json.dumps([{"id": "c1", "status": "OK", "reply": "hi"}])
    r = gemini.ask_batch([COMMENT], {"model": "m1", "fallback_model": ["m2"], "temperature": 0.4}, "", gemini.Keys("k1"), call=call)
    assert r["c1"]["status"] == "OK" and seen == ["m1", "m2"] and slept == []  # одна спроба на m1, без сну


def test_review_model_not_found_goes_to_fallback_without_retries(monkeypatch):
    slept, seen = [], []
    monkeypatch.setattr(gemini, "_sleep", lambda s: slept.append(s))

    def call(k, model, *a):
        seen.append(model)
        if model == "m1":
            raise Exception("400 INVALID_ARGUMENT: model m1 is not supported")
        return json.dumps([{"id": "c1", "verdict": "OK", "why": ""}])
    S = {"model": "m1", "review_model": "m1", "fallback_model": ["m2"]}
    r = gemini.review_batch([(COMMENT, "r")], S, "", gemini.Keys("k1"), call=call)
    assert r["c1"] is None and seen == ["m1", "m2"] and slept == []


def test_review_failure_reason_contains_error_text(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)

    def call(*a):
        raise ConnectionError("boom 42")
    r = gemini.review_batch([(COMMENT, "r")], {"model": "m1"}, "", gemini.Keys("k1"), call=call)
    assert "ConnectionError" in r["c1"] and "boom 42" in r["c1"]


def test_review_all_models_not_found_gives_reason_with_model_name():
    def call(*a):
        raise Exception("404 NOT_FOUND model gone")
    r = gemini.review_batch([(COMMENT, "r")], {"model": "m1"}, "", gemini.Keys("k1"), call=call)
    assert "m1" in r["c1"] and "404" in r["c1"]
