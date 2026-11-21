"""Швидкий preview: лише свіжі пости, читання зупиняється, коли коментарів достатньо."""
import datetime as dt

from gm_bot.config import DEFAULTS
from gm_bot.facebook import Facebook


def ts(hours_ago):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S+0000")


def post(i, days, n):
    return {"id": f"p{i}", "message": "m", "created_time": ts(days * 24),
            "comments": {"data": [{"id": f"p{i}c{j}", "message": "hi", "created_time": ts(days * 24 - 1),
                                   "from": {"id": "u"}} for j in range(n)]}}


class Fake(Facebook):
    """Facebook без мережі: список постів і самі пости беруться з пам'яті."""

    def __init__(self, S, posts, more=False):
        super().__init__({**DEFAULTS, "page_id": "P", **S}, "tok")
        self.posts, self.more, self.fetched = posts, more, []

    def _get(self, url, params=None):
        if url == "NEXT":
            raise AssertionError("наступну сторінку списку постів просити не треба")
        if url.endswith("/posts"):
            data = [{"id": p["id"], "created_time": p["created_time"],
                     "comments": {"summary": {"total_count": len(p["comments"]["data"])}}} for p in self.posts]
            return {"data": data, "paging": {"next": "NEXT"} if self.more else {}}
        pid = url.rsplit("/", 1)[1]
        self.fetched.append(pid)
        return next(p for p in self.posts if p["id"] == pid)


PV = {"preview": 10, "max_age_hours": 45 * 24}


def test_preview_stops_reading_when_enough_comments():
    fb = Fake(PV, [post(i, i, 20) for i in range(1, 6)])  # preview=10 → потрібно 30 коментарів
    out = fb.comments(include_answered=True)
    assert fb.fetched == ["p1", "p2"] and len(out) == 40 and fb.read == 2


def test_without_preview_all_fresh_posts_are_read():
    fb = Fake({"preview": 0, "max_age_hours": 720}, [post(i, i, 20) for i in range(1, 6)])
    fb.comments()
    assert fb.fetched == ["p1", "p2", "p3", "p4", "p5"]


def test_preview_skips_old_posts_by_default_but_age_all_reads_them():
    posts = [post(1, 2, 5), post(2, 200, 5), post(3, 300, 5)]
    fb = Fake(PV, posts)
    assert len(fb.comments(include_answered=True)) == 5 and fb.fetched == ["p1"]
    fb = Fake({"preview": 10, "max_age_hours": 9_999_999}, posts)  # AGE=all
    assert len(fb.comments(include_answered=True)) == 15 and fb.fetched == ["p1", "p2", "p3"]


def test_preview_does_not_page_through_old_posts():
    fb = Fake(PV, [post(1, 100, 5)], more=True)  # на сторінці лише старі пости: наступну сторінку не просимо
    assert fb.comments(include_answered=True) == [] and fb.fetched == []
