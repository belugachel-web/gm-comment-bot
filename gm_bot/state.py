"""Стан бота (state.json): що вже оброблено, зсув Telegram, лічильники, захист від повторних сповіщень."""
import datetime as dt
import json
import time


class State:
    def __init__(self, path, persist=True):
        self.path, self.persist = path, persist
        d = json.loads(path.read_text("utf-8")) if persist and path.exists() else {}
        self.done = d.get("done", {})        # id коментаря -> replied|drafted|rejected|unknown|skip|failed
        self.offset = d.get("offset", 0)     # Telegram getUpdates
        self.alerts = d.get("alerts", {})    # ключ -> час останнього сповіщення
        self.tries = d.get("tries", {})      # id -> кількість невдалих спроб публікації
        self.daily = d.get("daily", {"date": "", "n": 0})
        self.trial = {"ok": 0, "no": 0, "asked": False, "auto": False, **d.get("trial", {})}  # пробний період
        self.replies = d.get("replies", [])  # останні відправлені відповіді (для replies.md)
        self.pcache = d.get("pcache", {})    # id посту -> кількість коментарів на момент останнього повного читання (швидке читання)

    def log_reply(self, c, reply):
        """Запам'ятати відправлену відповідь: показується у replies.md (новіші зверху, останні 300)."""
        self.replies.insert(0, {"t": time.time(), "page": c.get("page", ""), "author": c.get("author", ""),
                                "comment": c.get("text", "")[:500], "reply": reply, "url": c.get("url", "")})
        del self.replies[300:]

    def _render_replies(self):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Europe/Kyiv")
        except Exception:
            tz = dt.timezone.utc  # немає бази часових поясів: показуємо за UTC
        out = ["# Останні відповіді бота", "",
               f"Новіші зверху. Показано останніх: {len(self.replies)}. Кнопка «Відкрити» — коментар на Facebook.", ""]
        for r in self.replies:
            when = dt.datetime.fromtimestamp(r["t"], tz).strftime("%d.%m %H:%M")
            q = lambda s: s.replace("\n", " ")
            out += [f"### {when}" + (f" · {r['page']}" if r.get("page") else ""),
                    f"**Коментар:** {q(r['comment'])}", "",
                    f"**Відповідь:** {q(r['reply'])}", "",
                    f"[Відкрити]({r['url']})" if r.get("url") else "", "---", ""]
        return "\n".join(out)

    def save(self):
        if not self.persist:
            return
        data = {"done": self.done, "offset": self.offset, "alerts": self.alerts,
                "tries": self.tries, "daily": self.daily, "trial": self.trial, "replies": self.replies, "pcache": self.pcache}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        self.path.with_name("replies.md").write_text(self._render_replies(), "utf-8")

    def alert_due(self, key, hours):
        """True не частіше ніж раз на `hours` годин для кожного ключа (щоб не засипати Telegram)."""
        if time.time() - self.alerts.get(key, 0) >= hours * 3600:
            self.alerts[key] = time.time()
            return True
        return False

    def _today(self):
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if self.daily.get("date") != today:
            self.daily = {"date": today, "n": 0}
        return self.daily

    def sent_today(self):
        return self._today()["n"]

    def count_sent(self):
        self._today()["n"] += 1
