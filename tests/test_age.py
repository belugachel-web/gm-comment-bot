from gm_bot import config
from test_runner import comment, make


def load(**env):
    return config.load({"SOURCE": "mock", **env})


def test_age_default_from_settings():
    S = load()
    assert S["age_set"] is False and S["max_age_hours"] == 720


def test_age_days_and_all():
    S = load(AGE="7")
    assert S["age_set"] and S["age_days"] == 7 and S["max_age_hours"] == 7 * 24
    S = load(AGE="all")
    assert S["age_set"] and S["max_age_hours"] > 10**6
    S = load(AGE="365")
    assert S["max_age_hours"] == 365 * 24


def test_age_garbage_ignored():
    S = load(AGE="abc")
    assert S["age_set"] is False and S["max_age_hours"] == 720
    S = load(AGE="0")
    assert S["age_set"] is False


def test_age_applies_to_preview():
    S = load(AGE="7", PREVIEW="10")
    assert S["max_age_hours"] == 7 * 24


def test_report_respects_age(tmp_path):
    day = 1440
    items = [comment("new", age=day), comment("mid", age=20 * day), comment("old", age=400 * day)]
    bot, st, tg, src = make(tmp_path, items, report=True, age_set=True, age_days=30, max_age_hours=30 * 24)
    bot.run()
    text = tg.sent[-1]["text"]
    assert "за 30 дн." in text and "Коментарів без відповіді сторінки: 2" in text


def test_report_without_age_counts_all(tmp_path):
    day = 1440
    items = [comment("new", age=day), comment("old", age=400 * day)]
    bot, st, tg, src = make(tmp_path, items, report=True)
    bot.run()
    assert "Коментарів без відповіді сторінки: 2" in tg.sent[-1]["text"]


def test_preview_takes_only_recent_by_default():
    S = load(PREVIEW="10")
    assert S["max_age_hours"] == S["preview_days"] * 24 and S["max_age_hours"] < 24 * 100
    assert not S["age_set"]


def test_preview_age_all_takes_everything():
    S = load(PREVIEW="10", AGE="all")
    assert S["max_age_hours"] > 10**6 and S["age_set"]
