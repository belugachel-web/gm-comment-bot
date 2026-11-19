"""Facebook Graph API (читання коментарів, відповіді) та тестове джерело mock."""
import datetime as dt
import json
import re
import time

import httpx

from .config import ROOT

TOKEN_CODES = {190, 102}                  # токен недійсний / прострочений
LIMIT_CODES = {4, 17, 32, 341, 368, 613}  # ліміти й блокування від Facebook


class FBError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"FB {code}: {msg}")
        self.code, self.msg = code, msg

    @property
    def kind(self):
        if self.code == 10 or 200 <= self.code <= 299 or (self.code == 100 and "endpoint requires" in self.msg.lower()):
            return "token"  # токену бракує дозволу (напр. pages_read_engagement): потрібен новий токен сторінки
        return "token" if self.code in TOKEN_CODES else "limit" if self.code in LIMIT_CODES else "comment"


def _parse_time(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def _links(text):
    """Посилання, що власник вказав у тексті допису (до 3)."""
    return [u.rstrip(".,;:!?)»”\"'") for u in re.findall(r"https?://[^\s]+", text or "")][:3]


class Facebook:
    FIELDS = ("id,message,created_time,comments.limit(100){id,message,created_time,permalink_url,"
              "from{id,name},comments.limit(25){id,message,created_time,permalink_url,from{id,name}}}")
    # легкий список постів: лише id, дата і кількість усіх коментарів (з відповідями) — без самих коментарів
    LIGHT = "id,created_time,comments.filter(stream).summary(true).limit(0)"

    def __init__(self, S, token):
        self.S, self.token, self.pid = S, token, S["page_id"]
        self.base = f"https://graph.facebook.com/{S['graph_version']}"
        self.http = httpx.Client(timeout=40)
        self.cache = {}        # id посту -> кількість коментарів, коли ми його востаннє повністю прочитали (підставляє Bot зі state.json)
        self.read_counts = {}  # id посту -> кількість коментарів у цьому запуску (Bot запише в кеш, коли пост «закрито»)
        self.read = self.seen = 0

    def _check(self, r):
        if r.status_code == 200:
            return r.json()
        try:
            e = r.json().get("error", {})
        except Exception:
            e = {}
        # URL з токеном ніколи не потрапляє в лог: показуємо лише код і текст помилки
        raise FBError(e.get("code", r.status_code), str(e.get("message", r.text))[:400] +
                      f" [subcode={e.get('error_subcode')}, запит={r.request.url.path}]")

    def _get(self, url, params=None):
        for attempt in range(3):
            try:
                return self._check(self.http.get(url, params=params))
            except httpx.TransportError:
                if attempt == 2:
                    raise FBError(0, "мережа Facebook недоступна")
                time.sleep(3 * (attempt + 1))

    def _post_items(self, p, now, include_answered):
        """Коментарі одного повністю прочитаного посту (з відповідями) у форматі бота."""
        S, out = self.S, []
        com = p.get("comments", {})
        items, nxt = list(com.get("data", [])), com.get("paging", {}).get("next")
        while nxt and len(items) < S["max_comments_per_post"]:
            cp = self._get(nxt)
            items += cp.get("data", [])
            nxt = cp.get("paging", {}).get("next")
        for c in items:
            if c.get("from", {}).get("id") == self.pid:
                continue  # власні коментарі сторінки
            rd, rn = list(c.get("comments", {}).get("data", [])), c.get("comments", {}).get("paging", {}).get("next")
            for _ in range(4):  # гілка довша за 25 відповідей: дочитуємо (інакше відповідь сторінки можна не побачити)
                if not rn:
                    break
                rp = self._get(rn)
                rd += rp.get("data", [])
                rn = rp.get("paging", {}).get("next")
            reps = sorted(rd, key=lambda x: x.get("created_time", ""))
            page_msgs = [x for x in reps if x.get("from", {}).get("id") == self.pid]
            if include_answered or not page_msgs:
                out.append({"post_id": p["id"], "id": c["id"], "author": c.get("from", {}).get("name", ""),
                            "text": c.get("message", ""), "post": p.get("message", ""), "post_links": _links(p.get("message", "")),
                            "url": c.get("permalink_url", ""), "answered": bool(page_msgs),
                            "age_min": (now - _parse_time(c["created_time"])).total_seconds() / 60})
            # клієнт написав у гілці після відповіді сторінки: остання відповідь не від сторінки
            if page_msgs and reps and reps[-1].get("from", {}).get("id") != self.pid \
                    and (reps[-1].get("message") or "").strip():
                r = reps[-1]
                ctx = (f"Клієнт спочатку написав: {c.get('message', '')[:200]} | "
                       f"Сторінка відповіла: {page_msgs[-1].get('message', '')[:300]} | Нижче нова репліка клієнта")
                out.append({"post_id": p["id"], "id": r["id"], "author": r.get("from", {}).get("name", ""),
                            "text": r.get("message", ""), "post": p.get("message", ""), "post_links": _links(p.get("message", "")), "context": ctx,
                            "url": r.get("permalink_url") or c.get("permalink_url", ""),
                            "age_min": (now - _parse_time(r["created_time"])).total_seconds() / 60})
        return out

    def _read_posts(self, ids, now, include_answered):
        """Повне читання постів по одному (параметр ids застарілий з Graph v26.0)."""
        out, bad, last = [], 0, None
        for x in ids:
            try:
                p = self._get(f"{self.base}/{x}", {"fields": self.FIELDS, "access_token": self.token})
            except FBError as e:
                if e.code != 100:
                    raise
                bad, last = bad + 1, e  # один пост Facebook не дає прочитати (інший тип/автор посту): пропускаємо
                print(f"Facebook: пост {x} пропущено (FB 100, недоступний для читання)")
                continue
            out += self._post_items(p, now, include_answered)
        if bad and bad == len(ids):
            raise last  # усі пости відмовлено: це не окремі пости, а права токена
        return out

    def comments(self, include_answered=False):
        try:
            return self._comments(include_answered)
        except FBError as e:
            if e.kind == "token":
                self._diagnose(e)
            raise

    def _diagnose(self, e):
        """При помилці токена пояснює, чи це токен саме цієї сторінки (запит /me для токена сторінки повертає саму сторінку)."""
        try:
            me = self._get(f"{self.base}/me", {"fields": "id,name", "access_token": self.token})
        except FBError:
            return
        if str(me.get("id")) != str(self.pid):
            raise FBError(e.code, f"ТОКЕН НЕ СТОРІНКИ: це токен «{me.get('name')}» ({me.get('id')}), а потрібен токен сторінки {self.pid}") from None
        raise FBError(e.code, f"токен сторінки {self.pid} правильний, але бракує прав у застосунку Meta: {e.msg}") from None

    def _comments(self, include_answered=False):
        """Швидке читання: легкий список усіх постів (id, дата, кількість коментарів), потім повністю читаємо лише
        свіжі пости (не старші за вік коментарів + запас) і старі пости, де кількість коментарів змінилась з минулого разу."""
        now = dt.datetime.now(dt.timezone.utc)
        S, seen, nxt = self.S, 0, None
        mh = S.get("max_age_hours") or 0
        cutoff = None  # None = вік не обмежено: читаємо всі пости повністю
        if 0 < mh < 24 * 365 * 3:
            cutoff = now - dt.timedelta(hours=mh + S.get("post_margin_days", 14) * 24)
        self.read_counts, self.aged = {}, False
        to_read = []
        page = self._get(f"{self.base}/{self.pid}/posts",
                         {"fields": self.LIGHT, "limit": 100, "access_token": self.token})
        while page:
            for p in page.get("data", []):
                seen += 1
                n = ((p.get("comments") or {}).get("summary") or {}).get("total_count")
                fresh = cutoff is None or n is None or not p.get("created_time") or _parse_time(p["created_time"]) >= cutoff
                if not fresh:
                    old = self.cache.get(p["id"])
                    if old is None:
                        self.read_counts[p["id"]] = n  # перший раз бачимо старий пост: лише запам'ятовуємо, не читаємо
                        self.aged = True
                        continue
                    if old == n:
                        self.aged = True
                        continue  # нічого нового
                to_read.append(p["id"])
                if n is not None:
                    self.read_counts[p["id"]] = n
            nxt = page.get("paging", {}).get("next")
            page = self._get(nxt) if nxt and seen < S["max_posts"] else None
        out = self._read_posts(to_read, now, include_answered)
        self.read = len(to_read)
        print(f"Facebook: постів у списку = {seen}, повністю прочитано = {self.read}")
        self.seen = seen
        self.cut = bool(nxt) and seen >= S["max_posts"]  # True = є ще старіші пости, яких ми не бачили (ліміт max_posts)
        return out

    def already_replied(self, cid):
        """True, якщо сторінка вже відповіла на цей коментар (захист від подвійного тапу)."""
        d = self._get(f"{self.base}/{cid}/comments", {"fields": "from{id}", "limit": 100, "access_token": self.token})
        return any(x.get("from", {}).get("id") == self.pid for x in d.get("data", []))

    def reply(self, cid, msg):
        """Публікує відповідь. Повертає id нового коментаря або кидає FBError."""
        try:
            r = self.http.post(f"{self.base}/{cid}/comments", data={"message": msg, "access_token": self.token})
        except httpx.TransportError:
            # мережевий збій: відповідь могла дійти до Facebook, але це безпечно — сторінка, що вже відповіла, не береться повторно
            raise FBError(0, "мережа Facebook недоступна")
        return self._check(r).get("id", "")

    def edit(self, rid, msg):
        """Редагує НАШУ раніше опубліковану відповідь (rid = id відповіді, не коментаря)."""
        return self._check(self.http.post(f"{self.base}/{rid}", data={"message": msg, "access_token": self.token}))

    def delete(self, rid):
        """Видаляє НАШУ відповідь."""
        return self._check(self.http.delete(f"{self.base}/{rid}", params={"access_token": self.token}))


class Mock:
    """Вигадані коментарі з mock_comments.json: Facebook не потрібен."""

    def comments(self, include_answered=False):
        data = json.loads((ROOT / "mock_comments.json").read_text("utf-8"))
        return [{"post_id": "mock", "id": c["id"], "author": c["author"], "text": c["text"], "post": c["post"],
                 "url": "https://facebook.com/mock/" + c["id"], "age_min": c["age_min"]} for c in data]

    def reply(self, cid, msg):
        print(f"[MOCK-FB] відповідь на {cid}: {msg}")
        return "mock-reply-" + cid
