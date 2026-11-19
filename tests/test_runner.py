import json

import pytest

from gm_bot import gemini, runner
from gm_bot.config import DEFAULTS
from gm_bot.facebook import FBError
from gm_bot.runner import Bot
from gm_bot.state import State

BUSINESS = "Сайт: https://grossmeister.com.ua/en/ , https://grossmeister.com.ua"


class FakeTG:
    chat_id = "42"

    def __init__(self):
        self.sent, self.edits, self.answers, self.queue, self.restored = [], [], [], [], []

    def send(self, text, buttons=False, silent=False):
        self.sent.append({"text": text, "buttons": buttons, "silent": silent})

    def clicks(self, offset, timeout=0):
        ups, self.queue = self.queue, []
        return ups

    def answer(self, cid, text):
        self.answers.append(text)

    def edit(self, chat_id, message_id, text):
        self.edits.append(text)

    def restore(self, chat_id, message_id, text):
        self.restored.append(message_id)


class FakeSrc:
    def __init__(self, comments=(), fail=None):
        self.items, self.replies, self.fail = list(comments), [], fail

    def comments(self, include_answered=False):
        return [dict(c) for c in self.items]

    def reply(self, cid, msg):
        if self.fail:
            raise self.fail
        self.replies.append((cid, msg))
        return "r-" + cid


def comment(i, text="How much?", age=10):
    return {"post_id": "p", "id": i, "author": "A", "text": text, "post": "post", "url": "u/" + i, "age_min": age}


def make(tmp_path, comments=(), ask=None, fail=None, trial_auto=True, review=None, **over):
    S = {**DEFAULTS, "source": "facebook", "page_id": "P", "auto_page_id": "P", "mode": "auto",
         "gap_sec": [0, 0], "preview": 0, "report": False, **over}
    st = State(tmp_path / "state.json", persist=True)
    st.trial["auto"] = trial_auto  # у тестах пробний період вимкнено, крім тих, де trial_auto=False
    tg, src = FakeTG(), FakeSrc(comments, fail)

    def default_ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "OK", "reply": "See https://grossmeister.com.ua/en/"} for c in items}

    bot = Bot(S, st, src, tg, keys=None, system="", business=BUSINESS, ask=ask or default_ask, review=review)
    bot.clicks_dir = tmp_path / "clicks"  # тести не чіпають справжню папку clicks
    return bot, st, tg, src


def test_auto_publishes_and_marks(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")])
    bot.run()
    assert src.replies == [("c1", "See https://grossmeister.com.ua/en/")]
    assert st.done["c1"] == "replied" and st.sent_today() == 1
    assert json.loads((tmp_path / "state.json").read_text())["done"]["c1"] == "replied"


def test_second_run_does_not_duplicate(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")])
    bot.run()
    bot2 = Bot(bot.S, State(tmp_path / "state.json"), src, tg, None, "", BUSINESS, ask=bot.ask)
    bot2.run()
    assert len(src.replies) == 1


def test_draft_sends_buttons_and_does_not_publish(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], mode="draft", wait_buttons_min=0)
    bot.run()
    assert src.replies == [] and st.done["c1"] == "drafted"
    assert any(m["buttons"] for m in tg.sent)


def click(cid, reply, data="ok", chat=42):
    text = f"📝 Чернетка\nID: {cid}\nАвтор: A\nКоментар: x\nПосилання: u{runner.SEP}{reply}\n——"
    return {"update_id": 7, "callback_query": {"id": "q", "data": data,
            "message": {"text": text, "chat": {"id": chat}, "message_id": 1}}}


def test_button_ok_publishes_once(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="draft")
    tg.queue = [click("c1", "Hello")]
    bot.buttons()
    tg.queue = [click("c1", "Hello")]
    bot.buttons()  # дубль кліку
    assert src.replies == [("c1", "Hello")]
    assert "Вже оброблено" in tg.answers


def test_button_reject_and_foreign_chat(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="draft")
    tg.queue = [click("c1", "Hello", data="no"), click("c2", "Hi", chat=999)]
    bot.buttons()
    assert st.done["c1"] == "rejected" and "c2" not in st.done and src.replies == []


def event(cid, reply, data="ok"):
    """Натискання у форматі, який передає Cloudflare Worker (env TG_EVENT)."""
    q = click(cid, reply, data)["callback_query"]
    return {"type": "callback", "data": data, "cb_id": "cb" + cid, "message_id": 1, "text": q["message"]["text"]}


def test_click_event_publishes_and_writes_file_not_state(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="draft")
    bot.click_event(event("c1", "Hello"))
    assert src.replies == [("c1", "Hello")]
    assert not (tmp_path / "state.json").exists()  # state.json не чіпали (щоб паралельні кліки не конфліктували)
    files = list((tmp_path / "clicks").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text("utf-8"))["done"] == "replied"
    assert any("НАДІСЛАНО" in t for t in tg.edits)


def test_saved_clicks_applied_on_next_run_and_files_removed(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="auto", trial_auto=False)  # пробний період: лічильники рахуються
    assert bot.trial_mode
    bot.click_event(event("c1", "Hello"))
    bot.click_event(event("c2", "Hi", data="no"))
    bot.click_event(event("c3", "Hey"))  # три кліки послідовно: жоден не загубився
    assert len(list((tmp_path / "clicks").glob("*.json"))) == 3
    bot2, st2, tg2, src2 = make(tmp_path, mode="auto", trial_auto=False)
    bot2.run()
    assert st2.done["c1"] == "replied" and st2.done["c2"] == "rejected" and st2.done["c3"] == "replied"
    assert st2.trial["ok"] == 2 and st2.trial["no"] == 1
    assert not list((tmp_path / "clicks").glob("*.json"))
    assert json.loads((tmp_path / "state.json").read_text("utf-8"))["done"]["c3"] == "replied"


def test_auto_on_click_applied_on_next_run(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="auto", trial_auto=False)
    ev = {"type": "callback", "data": "auto_on", "cb_id": "cbA", "message_id": 5, "text": "✅ Схвалено"}
    bot.click_event(ev)
    bot2, st2, tg2, src2 = make(tmp_path, mode="auto", trial_auto=False)
    bot2.run()
    assert st2.trial["auto"] is True and bot2.mode == "auto" and not bot2.trial_mode


def test_click_event_facebook_error_restores_buttons_and_writes_nothing(tmp_path):
    bot, st, tg, src = make(tmp_path, mode="draft", fail=FBError(100, "gone"))
    bot.click_event(event("c1", "Hello"))
    assert tg.restored == [1]
    assert not (tmp_path / "clicks").exists() or not list((tmp_path / "clicks").glob("*.json"))
    assert "c1" not in json.loads(((tmp_path / "state.json").read_text("utf-8")) if (tmp_path / "state.json").exists() else "{}").get("done", {})


def test_transient_gemini_error_is_retried_not_marked_unknown(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "ERROR", "reason": "503"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1")], ask=ask)
    bot.run()
    assert "c1" not in st.done
    assert not any("ПОТРІБНА ВАША ВІДПОВІДЬ" in m["text"] for m in tg.sent)
    assert any("Gemini не відповів" in m["text"] for m in tg.sent)


def test_unknown_goes_to_owner(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "UNKNOWN", "reason": "нема в базі"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1")], ask=ask)
    bot.run()
    assert st.done["c1"] == "unknown" and any("ПОТРІБНА ВАША ВІДПОВІДЬ" in m["text"] for m in tg.sent)


def test_price_in_reply_is_blocked_and_sent_to_owner(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "OK", "reply": "It costs $1 https://grossmeister.com.ua/en/"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1")], ask=ask)
    bot.run()
    assert src.replies == [] and st.done["c1"] == "unknown"
    assert any("не пройшла перевірку" in m["text"] for m in tg.sent)


def test_wrong_language_is_regenerated(tmp_path):
    calls = []

    def ask(items, S, system, keys, temp=None):
        calls.append(temp)
        reply = "Дякуємо за питання" if temp is None else "Thanks for asking"
        return {c["id"]: {"status": "OK", "reply": reply} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1", "How much?")], ask=ask)
    bot.run()
    assert calls == [None, 0.1] and src.replies == [("c1", "Thanks for asking")]


def test_skip_is_remembered(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "SKIP"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1", "spam")], ask=ask)
    bot.run()
    assert st.done["c1"] == "skip" and src.replies == []


def test_bad_comment_marked_failed_after_three_tries(tmp_path):
    err = FBError(100, "Unsupported post request")
    for _ in range(3):
        bot, st, tg, src = make(tmp_path, [comment("c1")], fail=err)
        bot.run()
    assert st.done["c1"] == "failed"


def test_token_error_halts_and_alerts_once(tmp_path):
    err = FBError(190, "expired")
    bot, st, tg, src = make(tmp_path, [comment("c1"), comment("c2")], fail=err)
    bot.run()
    assert "c1" not in st.done and "c2" not in st.done
    assert sum("Токен сторінки" in m["text"] for m in tg.sent) == 1
    bot2 = Bot(bot.S, st, src, tg, None, "", BUSINESS, ask=bot.ask)
    bot2.run()
    assert sum("Токен сторінки" in m["text"] for m in tg.sent) == 1  # не спамить щогодини


def test_token_error_on_fetch_is_graceful(tmp_path):
    bot, st, tg, src = make(tmp_path)
    src.comments = lambda include_answered=False: (_ for _ in ()).throw(FBError(190, "expired"))
    bot.run()
    assert any("Токен сторінки" in m["text"] for m in tg.sent)


def test_daily_cap(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1"), comment("c2", age=20)], max_replies_per_day=1)
    bot.run()
    assert len(src.replies) == 1 and "c2" not in st.done


def test_auto_is_downgraded_on_unapproved_page(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], page_id="REAL", wait_buttons_min=0)
    bot.run()
    assert src.replies == [] and st.done["c1"] == "drafted"
    assert any("auto_page_id" in m["text"] for m in tg.sent)


def test_old_and_too_old_comments(tmp_path):
    items = [comment("new", age=5), comment("old", age=3000), comment("ancient", age=60 * 24 * 40)]
    bot, st, tg, src = make(tmp_path, items, reply_old=False)
    bot.run()
    assert [r[0] for r in src.replies] == ["new"]


def test_preview_publishes_nothing_and_keeps_state(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], preview=3, mode="draft", max_age_hours=10**7, old_after_hours=0)
    bot.run()
    assert src.replies == []
    assert any("ТЕСТ" in m["text"] for m in tg.sent)


# ---------- пробний період ----------
def tclick(data, text="", uid=1):
    return {"update_id": uid, "callback_query": {"id": "q", "data": data,
            "message": {"chat": {"id": 42}, "message_id": 7, "text": text}}}


def draft_text(cid):
    return f"📝 Чернетка\nID: {cid}\nАвтор: A\n{runner.SEP}Hi\n——"


def test_trial_starts_as_draft_and_publishes_nothing(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], trial_auto=False, wait_buttons_min=0)
    bot.run()
    assert bot.mode == "draft" and src.replies == []
    assert st.done["c1"] == "drafted"
    assert any("Пробний період" in m["text"] for m in tg.sent)


def test_trial_asks_for_auto_after_enough_approvals(tmp_path):
    bot, st, tg, src = make(tmp_path, trial_auto=False, trial_n=3)
    tg.queue = [tclick("ok", draft_text(f"c{i}"), uid=i) for i in range(3)]
    bot.buttons()
    assert st.trial["ok"] == 3 and st.trial["asked"]
    assert [m for m in tg.sent if m["buttons"] == "auto"]


def test_trial_low_approval_resets_and_stays_draft(tmp_path):
    bot, st, tg, src = make(tmp_path, trial_auto=False, trial_n=4, trial_min_pct=90)
    tg.queue = [tclick("ok", draft_text("c1"), 1), tclick("no", draft_text("c2"), 2),
                tclick("no", draft_text("c3"), 3), tclick("ok", draft_text("c4"), 4)]
    bot.buttons()
    assert st.trial["ok"] == 0 and st.trial["no"] == 0 and not st.trial["asked"]
    assert not [m for m in tg.sent if m["buttons"] == "auto"]


def test_trial_auto_on_switches_to_auto_next_run(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], trial_auto=False)
    tg.queue = [tclick("auto_on", "Увімкнути авто?")]
    bot.run()
    assert st.trial["auto"] and bot.mode == "auto"
    assert src.replies and st.done["c1"] == "replied"


# ---------- друга перевірка ШІ ----------
def doubt_review(why):
    return lambda pairs, S, business, keys: {c["id"]: why for c, _ in pairs}


def test_review_doubt_goes_to_owner_even_in_auto(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], review=doubt_review("вигадана ціна"))
    bot.run()
    assert src.replies == [] and st.done["c1"] == "drafted"
    assert any(m["buttons"] is True and "Сумнів контролера: вигадана ціна" in m["text"] for m in tg.sent)


def test_review_ok_publishes(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], review=doubt_review(None))
    bot.run()
    assert len(src.replies) == 1


def test_doubt_draft_approved_in_next_auto_run(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], review=doubt_review("сумнів"))
    bot.run()
    tg.queue = [click("c1", "See https://grossmeister.com.ua/en/")]
    Bot(bot.S, State(tmp_path / "state.json"), src, tg, None, "", BUSINESS, ask=bot.ask).run()
    assert src.replies == [("c1", "See https://grossmeister.com.ua/en/")]
    assert json.loads((tmp_path / "state.json").read_text())["done"]["c1"] == "replied"


def test_review_batch_verdicts():
    out = json.dumps([{"id": "c1", "verdict": "OK", "why": ""}, {"id": "c2", "verdict": "DOUBT", "why": "ціна"}])
    pairs = [(comment("c1"), "r1"), (comment("c2"), "r2"), (comment("c3"), "r3")]
    r = gemini.review_batch(pairs, {"model": "m"}, "", gemini.Keys("k1"), call=lambda *a: out)
    assert r["c1"] is None and r["c2"] == "ціна" and r["c3"]  # пропущений контролером = сумнів


def test_review_without_keys_is_doubt():
    r = gemini.review_batch([(comment("c1"), "r")], {"model": "m"}, "", gemini.Keys(""))
    assert r["c1"]


def test_review_garbage_is_doubt(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    r = gemini.review_batch([(comment("c1"), "r")], {"model": "m"}, "", gemini.Keys("k1"), call=lambda *a: "не json")
    assert r["c1"]


# ---------- збої ----------
S_G = {"model": "m", "temperature": 0.4}


def test_gemini_rotates_key_on_429():
    used = []

    def call(k, *a):
        used.append(k)
        if k == "k1":
            raise Exception("429 RESOURCE_EXHAUSTED")
        return json.dumps([{"id": "c1", "status": "OK", "reply": "hi"}])
    r = gemini.ask_batch([comment("c1")], S_G, "", gemini.Keys("k1,k2"), call=call)
    assert used == ["k1", "k2"] and r["c1"]["status"] == "OK"


def test_gemini_all_keys_daily_limit_gives_quota():
    def call(k, *a):
        raise Exception("429 quota exceeded PerDay")
    r = gemini.ask_batch([comment("c1")], S_G, "", gemini.Keys("k1,k2"), call=call)
    assert r["c1"]["status"] == "QUOTA"


@pytest.mark.parametrize("bad", ["не json", "{}", "[]"])
def test_gemini_garbage_or_missing_is_error_not_unknown(monkeypatch, bad):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    r = gemini.ask_batch([comment("c1")], S_G, "", gemini.Keys("k1"), call=lambda *a: bad)
    assert r["c1"]["status"] == "ERROR"


def test_gemini_repeated_exceptions_give_error_after_three_tries(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    n = []

    def call(*a):
        n.append(1)
        raise ConnectionError("net")
    r = gemini.ask_batch([comment("c1")], S_G, "", gemini.Keys("k1"), call=call)
    assert r["c1"]["status"] == "ERROR" and len(n) == 3


def test_quota_halts_run_and_alerts(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "QUOTA", "reason": "x"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1"), comment("c2", age=20)], ask=ask)
    bot.run()
    assert "c1" not in st.done and "c2" not in st.done and src.replies == []
    assert any("вичерпали ліміт" in m["text"] for m in tg.sent)


def test_telegram_down_does_not_stop_auto(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")])
    tg.clicks = lambda offset, timeout=0: None
    bot.run()
    assert len(src.replies) == 1


def test_run_time_limit_defers_rest(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")], max_run_min=-1)
    bot.run()
    assert src.replies == [] and "c1" not in st.done


def test_three_facebook_failures_in_a_row_halt(tmp_path):
    items = [comment(f"c{i}", age=10 + i) for i in range(5)]
    bot, st, tg, src = make(tmp_path, items, fail=FBError(100, "bad"))
    bot.run()
    assert bot.fails == 3 and src.replies == []
    assert any("3 відповіді поспіль" in m["text"] for m in tg.sent)


# ---------- підсумок і прогрес запуску, дрібні виправлення відповідей ----------
def test_stats_count_sent(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1")])
    bot.run()
    assert bot.stats["sent"] == 1 and bot.processed() == 1


def test_progress_message_sent_once(tmp_path):
    bot, st, tg, src = make(tmp_path, [comment("c1"), comment("c2", age=20)])
    bot.run()
    assert sum("оброблено 0 з 2" in m["text"] for m in tg.sent) == 1


def test_summary_text(tmp_path):
    from gm_bot.main import run_summary
    bot, st, tg, src = make(tmp_path, [comment("c1"), comment("c2", age=20)], mode="draft", wait_buttons_min=0)
    bot.run()
    txt = run_summary([bot], False, 0, bot.processed())
    assert "оброблено 2" in txt and "2 чернеток" in txt and "Черга порожня" in txt


def test_emoji_glued_to_link_is_fixed_not_blocked(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "OK", "reply": "See https://grossmeister.com.ua/en/😊"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1")], ask=ask)
    bot.run()
    assert src.replies == [("c1", "See https://grossmeister.com.ua/en/")]


def test_hashtag_reply_is_blocked(tmp_path):
    def ask(items, S, system, keys, temp=None):
        return {c["id"]: {"status": "OK", "reply": "Thanks #sale"} for c in items}
    bot, st, tg, src = make(tmp_path, [comment("c1")], ask=ask)
    bot.run()
    assert src.replies == [] and st.done["c1"] == "unknown"


def test_lowercase_status_is_normalized():
    out = json.dumps([{"id": "c1", "status": " ok ", "reply": "hi"}])
    r = gemini.ask_batch([comment("c1")], S_G, "", gemini.Keys("k1"), call=lambda *a: out)
    assert r["c1"]["status"] == "OK"
