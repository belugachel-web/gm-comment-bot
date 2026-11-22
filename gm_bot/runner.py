"""Головний цикл: коментарі → Gemini → перевірка → чернетка в Telegram або публікація."""
import json
import random
import re
import time

from . import gemini
from .checks import is_noise, off_topic, reply_market, reply_ok, reply_safe, reply_style, tidy
from .config import ROOT
from .facebook import FBError

_sleep = time.sleep  # у тестах підміняється
SEP = "\n—— відповідь ——\n"  # формат повідомлень: за ним кнопки знаходять відповідь, не змінювати


def info(c):
    # рядок «Сторінка: …» потрібен кнопкам ✅/❌: за ним вони знаходять потрібну сторінку й токен (не змінювати формат)
    page = f"Сторінка: {c['page']}\n" if c.get("page") else ""
    if c.get("alt"):
        page += "🎲 База: GrossMeister (нарди/шахи)\n"
    if c.get("answered"):
        page += "↩️ УЖЕ Є відповідь сторінки (лише тест)\n"
    return f"{page}ID: {c['id']}\nАвтор: {c['author']}\nКоментар: {c['text'][:1000]}\nПосилання: {c['url']}"


def validate(c, res, business):
    """None = відповідь придатна; інакше причина. Заодно дрібно виправляє текст (хвіст після посилання, зайві емодзі)."""
    reply = tidy((res.get("reply") or "").strip())
    res["reply"] = reply
    if not reply:
        return "порожня відповідь"
    if not reply_ok(c["text"], reply):
        return "відповідь не тією мовою або з битими літерами"
    why = reply_style(reply) or reply_market(c["text"], reply, c.get("post_links") or [])
    if why:
        return why
    return reply_safe(reply, business + "\n" + "\n".join(c.get("post_links") or []))  # посилання з допису власника дозволені


class Bot:
    def __init__(self, S, st, src, tg, keys, system, business, ask=None, review=None, alt=None):
        self.S, self.st, self.src, self.tg, self.keys = S, st, src, tg, keys
        self.system, self.business = system, business
        self.alt = alt  # {words, system, business}: допис/коментар про нарди/шахи на мультібрендовій сторінці йде зі своєю базою й контролером
        if hasattr(src, "cache"):
            src.cache = st.pcache  # кеш лічильників коментарів: Facebook читає повністю лише змінені старі пости
        self.ask = ask or gemini.ask_batch
        # друга перевірка ШІ: з підміненим ask (тести) вимкнена, якщо не передано review
        self.review = review or (gemini.review_batch if ask is None and S.get("review") else None)
        self.live = S["source"] == "facebook" and not S["preview"]
        self.mode = S["mode"]
        self.clicks_dir = ROOT / "clicks"  # результати натискань з Worker: по одному файлу (паралельні запуски не конфліктують)
        self.t0 = time.time()
        self.deadline = self.t0 + S["max_run_min"] * 60
        self.pending, self.errors = set(), []
        self.pv = []  # збір на аудит (preview ≥ 20): коментарі + відповіді, що підуть у preview.md
        self.halt = self.quota = False
        self.fails = self.sent = 0
        # лічильники запуску для підсумку в Telegram: sent надіслано сам, drafted чернетки, unknown «потрібна ваша відповідь»,
        # skip пропущено, failed не вдалося остаточно, error збій ШІ (повторимо в наступному запуску)
        self.stats = {"sent": 0, "drafted": 0, "unknown": 0, "skip": 0, "failed": 0, "error": 0}
        self.why = ""  # чому запуск зупинився раніше (для підсумку)
        self.label = S.get("page_name") or ""
        self.todo_n, self.prog = 0, None  # прогрес-повідомлення в Telegram: скільки в запуску, id повідомлення (False = не редагуємо)
        self.more, self.left, self.stop, self.backlog, self.limit_left = False, 0, None, 0, None  # ланцюжок запусків: more = лишилась робота, треба ще запуск
        if self.mode == "auto" and self.live and S["page_id"] != S["auto_page_id"]:
            self.mode = "draft"
            if st.alert_due("auto_guard", 24):
                tg.send("⚠️ У settings.json стоїть mode=auto, але ця сторінка не вказана в auto_page_id. "
                        "Працюю як draft (чернетки). Щоб увімкнути auto, впишіть page_id в auto_page_id.")
        self.trial_mode = False  # пробний період: mode=auto, але власник ще не підтвердив авто
        if self.mode == "auto" and self.live and not st.trial["auto"] and not S.get("skip_trial"):
            self.mode, self.trial_mode = "draft", True
            if st.alert_due("trial_start", 24 * 3650):
                tg.send(f"🧪 Пробний період: перші {S['trial_n']} відповідей йдуть вам на ✅/❌. "
                        "Потім бот запропонує одну кнопку «Увімкнути авто».")

    # ---------- пробний період ----------
    def check_trial(self):
        S, t = self.S, self.st.trial
        if not self.trial_mode or t["auto"]:
            return
        n = t["ok"] + t["no"]
        if n < S["trial_n"]:
            return
        pct = 100 * t["ok"] / n
        if pct < S["trial_min_pct"]:
            self.tg.send(f"Схвалено {t['ok']} з {n} ({pct:.0f}%), потрібно ≥{S['trial_min_pct']}%. "
                         "Лишаюся на чернетках, лічильник скинуто.")
            t.update(ok=0, no=0, asked=False)
        elif not t["asked"] or self.st.alert_due("trial_ask", 24):
            t["asked"] = True
            self.st.alert_due("trial_ask", 24)
            self.tg.send(f"✅ Схвалено {t['ok']} з {n}. Увімкнути авто? "
                         "(Сумнівні відповіді й далі йтимуть вам.)", buttons="auto")

    # ---------- помилки Facebook ----------
    def alert_fb(self, e):
        if e.kind == "token" and self.st.alert_due("token", 12):
            self.tg.send("🔑 Токен сторінки Facebook недійсний або прострочений. "
                         "Створіть новий і замініть секрет FB_PAGE_ACCESS_TOKEN (див. README, розділ «Постійний токен»).")
        elif e.kind == "limit" and self.st.alert_due("limit", 6):
            self.tg.send(f"🚨 Facebook обмежив відправку ({e.msg}). Бот зупинився, перевірте сторінку.")

    # ---------- кнопки ✅/❌ ----------
    def buttons(self, timeout=0):
        """Обробляє натискання. False = Telegram недоступний."""
        ups = self.tg.clicks(self.st.offset, timeout)
        if ups is None:
            print("Кнопки: Telegram недоступний")
            return False
        if ups or not timeout:
            print(f"Кнопки: натискань = {len(ups)}")
        for u in ups:
            self.st.offset = u["update_id"] + 1
            self.click(u.get("callback_query"))
        self.check_trial()
        return True

    def click(self, cq, answer=True):
        """Одне натискання (з getUpdates або з Cloudflare Worker; там answer=False — Worker уже відповів)."""
        msg = (cq or {}).get("message") or {}
        if not cq or str(msg.get("chat", {}).get("id")) != str(self.tg.chat_id):
            return  # не кнопка або чужий чат
        ans = self.tg.answer if answer else (lambda *a: None)
        if cq["data"] in ("auto_on", "auto_off"):
            on = cq["data"] == "auto_on"
            self.st.trial["auto"] = on
            if not on:
                self.st.trial.update(ok=0, no=0, asked=False)
            note = "🤖 АВТО УВІМКНЕНО" if on else "📝 Лишаю чернетки, лічильник скинуто"
            ans(cq["id"], note)
            self.tg.edit(msg["chat"]["id"], msg["message_id"], note)
            return {"auto": on}
        txt = msg.get("text", "")
        cid = re.search(r"ID: (\S+)", txt)
        rep = re.search(r"—— відповідь ——\n(.*?)\n——\s*$", txt, re.S)
        if not (cid and rep):
            print("Кнопки: не вдалося розібрати повідомлення")
            return
        cid, rep = cid.group(1), rep.group(1)
        if self.st.done.get(cid) in ("replied", "rejected"):
            ans(cq["id"], "Вже оброблено")
            return  # повторні кліки ігноруємо
        if cq["data"] == "ok":
            try:
                chk = getattr(self.src, "already_replied", None)
                dup = False
                try:
                    dup = bool(chk and chk(cid))  # подвійний тап: Facebook вже має відповідь сторінки
                except FBError:
                    pass  # не змогли перевірити — пробуємо надіслати як раніше
                rid = ""
                if not dup:
                    rid = self.src.reply(cid, rep) or ""
            except FBError as e:
                self.alert_fb(e)
                ans(cq["id"], "Помилка Facebook")
                self.tg.send(f"⚠️ Не вдалося опублікувати для {cid}: {e.msg}")
                self._restore(msg, txt)  # повертаємо кнопки, щоб можна було натиснути ще раз
                return
            self.st.done[cid] = "replied"
            rid_str = rid if isinstance(rid, str) else ""
            pname = self.S.get("page_name", "")
            if rid_str:
                self.st.rids[cid] = rid_str
                if pname:
                    self.st.rpages[cid] = pname
            note = "✅ НАДІСЛАНО на Facebook"
            out = {"cid": cid, "done": "replied", "rid": rid_str, "page": pname,
                   "trial": "ok" if self.trial_mode else None}
            if self.trial_mode:
                self.st.trial["ok"] += 1
        else:
            self.st.done[cid] = "rejected"
            note = "❌ СКАСОВАНО (не надсилати)"
            out = {"cid": cid, "done": "rejected", "trial": "no" if self.trial_mode else None}
            if self.trial_mode:
                self.st.trial["no"] += 1
        self.pending.discard(cid)
        print(f"Кнопки: {cid} -> {note}")
        ans(cq["id"], note)
        self.tg.edit(msg["chat"]["id"], msg["message_id"], note + "\n" + txt.split("\n", 1)[-1])
        return out

    def click_event(self, ev):
        """Натискання з Cloudflare Worker (env TG_EVENT): лише воно, без коментарів і Gemini."""
        cq = {"id": ev.get("cb_id"), "data": ev.get("data"),
              "message": {"chat": {"id": self.tg.chat_id}, "message_id": ev.get("message_id"), "text": ev.get("text", "")}}
        res = self.click(cq, answer=False)
        if res:  # не чіпаємо state.json (паралельні кліки би конфліктували): записуємо результат у окремий файл,
            self.clicks_dir.mkdir(exist_ok=True)  # а наступний звичайний запуск їх підхопить (ingest_clicks)
            name = re.sub(r"\W", "_", str(ev.get("cb_id") or time.time()))
            (self.clicks_dir / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")

    def _restore(self, msg, txt):
        """Повернути кнопки на чернетці (Worker їх миттєво прибрав), якщо обробка не вдалася."""
        f = getattr(self.tg, "restore", None)
        if f:
            f(msg["chat"]["id"], msg["message_id"], txt)

    def ingest_clicks(self):
        """Застосувати результати натискань (clicks/*.json) до стану і видалити файли."""
        if not self.clicks_dir.is_dir():
            return 0
        n = 0
        for f in sorted(self.clicks_dir.glob("*.json")):
            try:
                r = json.loads(f.read_text("utf-8"))
            except ValueError:
                r = {}
            if "auto" in r:
                self.st.trial["auto"] = bool(r["auto"])
                if not r["auto"]:
                    self.st.trial.update(ok=0, no=0, asked=False)
            if r.get("cid") and r.get("done"):
                self.st.done[r["cid"]] = r["done"]
                if r.get("rid"):
                    self.st.rids[r["cid"]] = r["rid"]
                    if r.get("page"):
                        self.st.rpages[r["cid"]] = r["page"]
                if r.get("trial") in ("ok", "no"):
                    self.st.trial[r["trial"]] += 1
            f.unlink()
            n += 1
        if n:
            print(f"Кнопки: підхоплено збережених натискань = {n}")
            self.check_trial()
            self.st.save()
        return n

    # ---------- відбір ----------
    def pick(self, items):
        S, st, todo, off = self.S, self.st, [], []
        for c in sorted(items, key=lambda c: c["age_min"]):  # нові першими
            if c["id"] in st.done or not c["text"].strip():
                continue
            old = c["age_min"] > S["old_after_hours"] * 60
            if c["age_min"] > S["max_age_hours"] * 60 or (old and not S["reply_old"]):
                continue
            if not old and c["age_min"] < S["new_wait_min"]:
                continue  # ще рано, відповімо в наступному запуску
            if is_noise(c["text"]):  # «5», «+», «???», лише емодзі: без Gemini, мовчки пропускаємо
                if not S["preview"]:
                    st.done[c["id"]] = "skip"
                continue
            if self.alt and off_topic(self.alt["words"], c.get("post"), c["text"]):
                c["alt"] = True  # нардова тема на мультібрендовій сторінці: відповідь і контролер — за базою GrossMeister
            else:
                topic = off_topic(S.get("skip_topics"), c.get("post"), c["text"])
                if topic:  # допис/коментар про чужий товар: бот мовчить, власник бачить одне зведення
                    if not S["preview"]:
                        st.done[c["id"]] = "skip"
                        off.append((topic, c))
                    continue
            todo.append(c)
        if off:
            self.tg.send(f"⏭ Пропустив {len(off)} коментарів під чужою темою ({', '.join(sorted({t for t, _ in off}))}). "
                         "Відповісти треба самому:\n" + "\n".join(c["url"] for _, c in off[:10]) +
                         (f"\n…і ще {len(off) - 10}" if len(off) > 10 else ""), silent=True)
        n = S["preview"]
        if not n:
            self.backlog = len(todo)  # скільки всього чекає, до обрізки по max_replies_per_run
            cap = S["max_replies_per_run"]
            if S.get("limit"):  # «скільки обробити» з меню
                cap = min(cap, S["limit"])
            return todo[:cap]
        if len(todo) < n and S["source"] != "mock":  # справжніх коментарів мало: добираємо приклади
            extra = [{"id": "mock-" + m["id"], "author": m["author"], "text": m["text"], "post": m["post"],
                      "url": "(тестовий приклад, не з Facebook)", "age_min": m["age_min"],
                      **({"alt": True} if self.alt and off_topic(self.alt["words"], m.get("post"), m["text"]) else {})}
                     for m in json.loads((ROOT / "mock_comments.json").read_text("utf-8"))]
            random.shuffle(extra)
            todo += extra[:n - len(todo)]
        return random.sample(todo, min(n, len(todo)))

    # ---------- генерація ----------
    def generate(self, chunk):
        """Нардові коментарі (c['alt']) йдуть окремо: зі своєю базою для генерації, перевірки й контролера."""
        res = {}
        for alt in (False, True):
            part = [c for c in chunk if bool(c.get("alt")) == alt]
            if part:
                ctx = (self.alt["system"], self.alt["business"]) if alt else (self.system, self.business)
                res.update(self._generate(part, *ctx))
        return res

    def _generate(self, chunk, system, business):
        res = self.ask(chunk, self.S, system, self.keys)
        bad = [c for c in chunk if res[c["id"]].get("status") == "OK" and validate(c, res[c["id"]], business)]
        if bad:
            print(f"Перевірка не пройдена: перегенерація {len(bad)}")
            r2 = self.ask(bad, self.S, system, self.keys, temp=0.1)
            for c in bad:
                x = r2[c["id"]]
                why = validate(c, x, business) if x.get("status") == "OK" else None
                res[c["id"]] = x if not why else {"status": "UNKNOWN", "reason": f"відповідь ШІ не пройшла перевірку: {why}"}
        if self.review:  # контролер може лише знизити впевненість: сумнів → власнику на ✅/❌
            pairs = [(c, res[c["id"]]["reply"].strip()) for c in chunk
                     if res[c["id"]].get("status") == "OK" and (res[c["id"]].get("reply") or "").strip()]
            if pairs:
                for i, why in self.review(pairs, self.S, business, self.keys).items():
                    if why:
                        res[i]["doubt"] = why
        return res

    # ---------- обробка одного коментаря ----------
    def handle(self, c, res):
        S, st = self.S, self.st
        status, reply = res.get("status"), (res.get("reply") or "").strip()
        if status == "QUOTA":
            self.quota = self.halt = True
            self.why = "ліміт ключів Gemini"
            return
        if status == "ERROR":
            self.errors.append(res.get("reason", ""))
            n = st.tries[c["id"]] = st.tries.get(c["id"], 0) + 1
            if n >= S.get("max_ai_tries", 3):  # Gemini стабільно падає на цьому коментарі: більше не палимо квоту
                st.done[c["id"]] = "failed"
                st.tries.pop(c["id"], None)
                self.stats["failed"] += 1
                self.tg.send(f"⚠️ ШІ не зміг обробити коментар після {n} спроб, пропускаю. Відповісти треба самому: {c['url']}")
            else:
                self.stats["error"] += 1
            return  # поки ліміт не вичерпано — спробуємо в наступному запуску
        if S["preview"] >= 20 and status in ("OK", "SKIP", "UNKNOWN"):  # збір на аудит: у файл, не в Telegram
            self.pv.append((c, status, reply, res.get("doubt") or res.get("reason") or ""))
            return
        if status == "OK" and reply:
            body = f"{info(c)}{SEP}{reply}\n——"
            doubt = res.get("doubt")
            if S["preview"]:
                self.tg.send("🧪 ТЕСТ (нічого не публікується)\n" + (f"🤔 Сумнів: {doubt}\n" if doubt else "") + body, silent=True)
            elif self.mode == "draft" or doubt:
                head = "📝 Чернетка · ⏳ ЧЕКАЄ ВАШОГО РІШЕННЯ\n"
                if doubt:
                    head = f"🤔 Сумнів контролера: {doubt}\n" + head
                self.tg.send(head + body, buttons=True)
                st.done[c["id"]] = "drafted"
                self.stats["drafted"] += 1
                self.pending.add(c["id"])
            else:
                self.publish(c, reply)
        elif status == "SKIP":
            st.done[c["id"]] = "skip"
            self.stats["skip"] += 1
        else:
            self.tg.send(f"❓ ПОТРІБНА ВАША ВІДПОВІДЬ (ШІ не знає)\n{info(c)}\nПричина: {res.get('reason', status)}")
            st.done[c["id"]] = "unknown"
            self.stats["unknown"] += 1
        print(f"[{status}]")  # лише статус: у публічних логах немає імен і текстів коментарів

    def publish(self, c, reply):
        S, st = self.S, self.st
        if st.sent_today() >= S["max_replies_per_day"]:
            self.halt = True
            self.why = "денний ліміт відповідей"
            if st.alert_due("daily", 12):
                self.tg.send(f"⏸ Денний ліміт {S['max_replies_per_day']} відповідей досягнуто. Решта — завтра. Продовжити: /run")
            return
        try:
            rid = self.src.reply(c["id"], reply)
        except FBError as e:
            print(f"Facebook відхилив відповідь на {c['id']}: {e.msg}")
            self.alert_fb(e)
            if e.kind in ("token", "limit") or e.code not in (100, 33):  # будь-яка незвична помилка = миттєвий стоп (щоб не погіршити блок)
                self.halt = True
                self.why = f"помилка Facebook {e.code}"
                if e.kind == "comment" and st.alert_due("fb_stop", 1):
                    self.tg.send(f"🛑 Facebook повернув помилку, бот зупинився: {e.msg}")
                return
            n = st.tries[c["id"]] = st.tries.get(c["id"], 0) + 1
            if n >= 3:  # конкретний коментар не вдається (видалений/закритий): більше не чіпаємо
                st.done[c["id"]] = "failed"
                self.stats["failed"] += 1
                self.tg.send(f"⚠️ Не вдалося відповісти після 3 спроб, пропускаю: {c['url']}\n{e.msg}")
            self.fails += 1
            if self.fails >= 3:
                self.halt = True
                self.why = "3 відмови Facebook поспіль"
                if st.alert_due("fb_fail", 6):
                    self.tg.send("🚨 Facebook відхилив 3 відповіді поспіль (права застосунку або обмеження сторінки). "
                                 "Бот зупинився, перевірте сторінку.")
            return
        st.done[c["id"]] = "replied"
        st.tries.pop(c["id"], None)
        st.count_sent()
        st.log_reply(c, reply, rid if isinstance(rid, str) else "")
        st.save()
        self.fails, self.sent = 0, self.sent + 1
        self.stats["sent"] += 1
        if S["notify_auto"]:
            self.tg.send(f"🤖 Надіслано автоматично\n{info(c)}{SEP}{reply}\n——", silent=True)
        a, b = S["gap_sec"]
        if b and S["source"] != "mock":  # на вигаданих коментарях пауза не потрібна
            _sleep(random.uniform(a, b))

    # ---------- запуск ----------
    def run(self):
        S, st = self.S, self.st
        if S["report"]:
            return self.report()
        if self.live:  # кнопки обробляємо і в auto (сумнівні відповіді чекають рішення власника)
            self.ingest_clicks()  # натискання, що прийшли через Worker
            if not S.get("tg_webhook"):
                self.buttons()
            if self.trial_mode and self.st.trial["auto"]:  # власник щойно увімкнув авто
                self.mode, self.trial_mode = "auto", False
        try:
            items = self.src.comments(include_answered=bool(S["preview"]))
        except FBError as e:
            if e.kind == "comment":
                raise
            print(f"Facebook: {e}")
            self.alert_fb(e)
            st.save()
            return
        for c in items:
            c["page"] = S.get("page_name", "")  # підпис сторінки в чернетках Telegram (порожньо = одна сторінка)
        todo = self.pick(items)
        done0 = len(st.done)
        print(f"Режим={self.mode} джерело={S['source']} знайдено={len(items)} до обробки={len(todo)} (по {S['batch_size']} за запит)")
        if S["preview"]:
            self.tg.send(f"🧪 Перегляд: {len(todo)} відповідей нижче. Нічого не публікується, стан не змінюється.", silent=True)
        self.todo_n = len(todo)
        self.show_progress(0)
        for i in range(0, len(todo), max(1, S["batch_size"])):
            if self.halt:
                break
            chunk = todo[i:i + max(1, S["batch_size"])]
            res = self.generate(chunk)
            for c in chunk:
                if self.halt:
                    break
                if time.time() >= self.deadline:
                    print("Час запуску вичерпано, решту зроблю в наступному запуску")
                    self.halt, self.stop = True, "time"
                    self.why = "час запуску"
                    break
                self.handle(c, res[c["id"]])
            st.save()
            self.show_progress(len(st.done) - done0)
        # ланцюжок: наступний запуск робимо, лише якщо зупинились через час або ліміт запуску (не через помилку/ліміти)
        # і був прогрес (інакше було б нескінченне повторення)
        progress = len(st.done) - done0
        capped = self.backlog > len(todo)
        self.left = max(0, self.backlog - progress)
        self.limit_left = (max(0, S["limit"] - progress) if S.get("limit") else None)  # None = ліміту кількості нема
        if self.limit_left is not None:
            self.left = min(self.left, self.limit_left)
        self.more = bool(self.live and not S["preview"] and progress > 0 and (self.stop == "time" or (capped and not self.halt))
                         and (self.limit_left is None or self.limit_left > 0))
        self.commit_cache(items)
        self.write_preview()
        self.wait_buttons()
        if self.quota and st.alert_due("quota", 6):
            self.tg.send("⚠️ Усі Gemini-ключі вичерпали ліміт. Решта коментарів — у наступному запуску.")
        if self.errors and st.alert_due("gemini_error", 6):
            self.tg.send(f"⚠️ Gemini не відповів для {len(self.errors)} коментарів (повторю в наступному запуску): {self.errors[0]}")
        st.save()
        print(f"Готово за {time.time() - self.t0:.0f} с")

    def write_preview(self):
        """preview ≥ 20: все в preview.md (workflow вантажить у coments-data), у Telegram один рядок."""
        if not self.pv:
            return
        q = lambda s: (s or "").replace("\n", " ")
        out = [f"# Перевірка {len(self.pv)} коментарів (нічого не опубліковано)", ""]
        for i, (c, s, reply, note) in enumerate(self.pv, 1):
            out += [l for l in (f"## {i} · {s} · {c['id']}", f"Сторінка: {c.get('page') or '?'}", "База: GrossMeister (нарди)" if c.get("alt") else "", f"Допис: {q(c.get('post'))[:150]}", f"Коментар: {q(c['text'])[:300]}",
                                f"Відповідь: {q(reply)}" if reply else "", f"Примітка: {q(note)}" if note else "") if l] + [""]
        (ROOT / "preview.md").write_text("\n".join(out), "utf-8")
        self.tg.send(f"📄 Готово: {len(self.pv)} у preview.md (репо coments-data). Дай його Gemini.", silent=True)

    def processed(self):
        """Скільки коментарів закрито цим запуском (збій ШІ «повторю» не рахується)."""
        s = self.stats
        return s["sent"] + s["drafted"] + s["unknown"] + s["skip"] + s["failed"]

    def show_progress(self, n):
        """Одне повідомлення «⚙️ працює: оброблено N з M» в Telegram; після кожної пачки редагується (не спамить)."""
        if not self.live or self.S["preview"] or not self.todo_n or self.prog is False:
            return
        text = f"⏳ {self.label or 'Бот'}: оброблено {n} з {self.todo_n}"
        if self.backlog > self.todo_n:
            text += f" (всього в черзі {self.backlog})"
        if self.prog is None:
            r = self.tg.send(text, silent=True)
            self.prog = r.get("message_id", False) if isinstance(r, dict) else False
        else:
            self.tg.edit(self.tg.chat_id, self.prog, text)

    def commit_cache(self, items):
        """Запам'ятовуємо кількість коментарів посту, лише якщо в ньому не лишилось необроблених коментарів
        (інакше пост читатиметься знову, і нічого не загубиться)."""
        S, st = self.S, self.st
        rc = getattr(self.src, "read_counts", None)
        if not rc or not self.live or S["preview"]:
            return
        mx = S["max_age_hours"] * 60
        open_posts = set()
        for c in items:
            if c["id"] in st.done or not c["text"].strip() or c["age_min"] > mx:
                continue
            if c["age_min"] > S["old_after_hours"] * 60 and not S["reply_old"]:
                continue
            open_posts.add(c["post_id"])
        for pid, n in rc.items():
            if pid not in open_posts:
                st.pcache[pid] = n

    def wait_buttons(self):
        """У draft після нових чернеток чекаємо натискань (в межах часу запуску)."""
        if not (self.mode == "draft" and self.live and self.pending) or self.S.get("tg_webhook"):
            return
        end = min(time.time() + self.S["wait_buttons_min"] * 60, self.deadline + 120)
        print(f"Чекаю натискань кнопок ({len(self.pending)} чернеток)")
        while self.pending and time.time() < end:
            ok = self.buttons(timeout=max(1, min(25, int(end - time.time()))))
            self.st.save()
            if not ok:
                break

    # ---------- звіт ----------
    def report(self):
        """Нічого не публікує: скільки коментарів без відповіді сторінки і де."""
        st = self.st
        items = self.src.comments()
        if self.S.get("age_set"):  # вік обрано в меню/запуску: рахуємо лише коментарі не старші за нього
            items = [c for c in items if c["age_min"] <= self.S["max_age_hours"] * 60]
        d = self.S.get("age_days", 0)
        age_txt = "" if not self.S.get("age_set") else (f" · за {d} дн." if d else " · усі коментарі")
        new = [c for c in items if c["id"] not in st.done]
        waiting = [c for c in items if st.done.get(c["id"]) in ("drafted", "unknown")]
        B = [("до 1 доби", 1440), ("1–7 діб", 10080), ("7–30 діб", 43200), ("30 діб – 1 рік", 525600), ("понад рік", 10**12)]
        bucket = lambda c: next(n for n, m in B if c["age_min"] <= m)
        lines = ["📊 Звіт" + (f" · {self.S['page_name']}" if self.S.get("page_name") else "") + age_txt + " (нічого не надсилано)", f"Коментарів без відповіді сторінки: {len(items)}",
                 *([f"Постів переглянуто: {self.src.seen}" + (" ⚠️ ЛІМІТ max_posts: старіші пости НЕ прочитано" if getattr(self.src, "cut", False) else " (старі пости без нових коментарів не читали, повністю прочитано: %s)" % getattr(self.src, "read", "?") if getattr(self.src, "aged", False) else " (це всі пости сторінки)")]
                   if getattr(self.src, "seen", None) is not None else []),
                 f"Gemini-ключів у роботі: {len(getattr(self.keys, 'keys', []))}",
                 f"— нових, бот ще не чіпав: {len(new)}", f"— чернетки/ШІ не знав (чекають вас): {len(waiting)}", "", "За віком (нові):"]
        for n, _ in B:
            k = sum(1 for c in new if bucket(c) == n)
            if k:
                lines.append(f"— {n}: {k}")
        posts = {}
        for c in items:
            posts.setdefault(c["post_id"], []).append(c)
        lines += ["", "Де (пости з найбільшою кількістю):"]
        for _, cs in sorted(posts.items(), key=lambda x: -len(x[1]))[:10]:
            title = (cs[0]["post"] or "(без тексту)").replace("\n", " ")[:40]
            lines.append(f"— {len(cs)} шт · «{title}» · {cs[0]['url']}")
        print(f"Звіт: без відповіді={len(items)}, нових={len(new)}, чекають вас={len(waiting)}")  # деталі (пости, посилання) лише в Telegram, не в публічні логи
        self.tg.send("\n".join(lines), silent=True)
