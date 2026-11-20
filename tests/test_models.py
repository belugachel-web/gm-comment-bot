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
