from test_runner import comment, make


def many(n):
    return [comment(f"c{i}", age=10 + i) for i in range(n)]


def test_chain_when_run_cap_reached(tmp_path):
    bot, st, tg, src = make(tmp_path, many(3), max_replies_per_run=2)
    bot.run()
    assert len(src.replies) == 2
    assert bot.more is True and bot.left == 1


def test_no_chain_when_everything_done(tmp_path):
    bot, st, tg, src = make(tmp_path, many(2), max_replies_per_run=5)
    bot.run()
    assert len(src.replies) == 2
    assert bot.more is False


def test_no_chain_on_gemini_quota(tmp_path):
    def quota(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "QUOTA", "reason": "x"} for c in items}

    bot, st, tg, src = make(tmp_path, many(3), ask=quota, max_replies_per_run=2)
    bot.run()
    assert src.replies == [] and bot.more is False


def test_no_chain_on_daily_limit(tmp_path):
    bot, st, tg, src = make(tmp_path, many(4), max_replies_per_run=3, max_replies_per_day=1)
    bot.run()
    assert len(src.replies) == 1 and bot.more is False


def test_no_chain_without_progress_on_time_out(tmp_path):
    bot, st, tg, src = make(tmp_path, many(3), max_replies_per_run=2, max_run_min=0)
    bot.run()
    assert src.replies == [] and bot.more is False


def test_limit_stops_exactly(tmp_path):
    bot, st, tg, src = make(tmp_path, many(10), limit=4)
    bot.run()
    assert len(src.replies) == 4
    assert bot.more is False and bot.limit_left == 0


def test_limit_larger_than_run_cap_chains_with_rest(tmp_path):
    bot, st, tg, src = make(tmp_path, many(10), limit=7, max_replies_per_run=3)
    bot.run()
    assert len(src.replies) == 3
    assert bot.more is True and bot.limit_left == 4


def test_limit_env_parsing():
    from gm_bot import config
    assert config.load({"SOURCE": "mock", "LIMIT": "250"})["limit"] == 250
    assert config.load({"SOURCE": "mock", "LIMIT": ""})["limit"] == 0
    assert config.load({"SOURCE": "mock", "LIMIT": "abc"})["limit"] == 0
