"""Кілька сторінок: вибір сторінки для запуску і для натискання кнопки."""
from gm_bot.main import page_for_click, page_list

S = {
    "pages": [
        {"key": "nardy", "name": "GrossMeister", "page_id": "1", "auto_page_id": "1",
         "business_file": "a.md", "token_env": "T1"},
        {"key": "stryi", "name": "STRYI", "page_id": "2", "business_file": "b.md",
         "token_env": "T2", "mode": "draft"},
    ],
    "page_id": "1", "auto_page_id": "1", "business_file": "a.md", "mode": "auto", "preview": 0,
}


def test_two_pages_have_own_settings():
    a, b = page_list(S, {})
    assert (a["page_id"], a["business_file"], a["token_env"], a["mode"]) == ("1", "a.md", "T1", "auto")
    assert (b["page_id"], b["business_file"], b["token_env"], b["mode"]) == ("2", "b.md", "T2", "draft")
    assert b["auto_page_id"] == ""  # на STRYI авто неможливе


def test_preview_is_always_draft():
    ps = page_list({**S, "preview": 5}, {})
    assert [p["mode"] for p in ps] == ["draft", "draft"]


def test_env_page_selects_one():
    ps = page_list(S, {"PAGE": "stryi"})
    assert [p["page_id"] for p in ps] == ["2"]


def test_click_finds_page_by_label():
    ps = page_list(S, {})
    ev = {"text": "📝 Чернетка\nСторінка: STRYI\nID: 5_6\nАвтор: x"}
    assert page_for_click(ps, ev)["page_id"] == "2"


def test_old_draft_without_label_goes_to_first_page():
    ps = page_list(S, {})
    assert page_for_click(ps, {"text": "ID: 5_6\nАвтор: x"})["page_id"] == "1"


def test_single_page_without_pages_list():
    one = {"page_id": "9", "auto_page_id": "", "business_file": "c.md", "mode": "draft", "preview": 0, "pages": []}
    ps = page_list(one, {})
    assert len(ps) == 1 and ps[0]["page_id"] == "9" and ps[0]["page_name"] == ""
