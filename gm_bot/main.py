"""Точка входу: python bot.py [--fresh]   (--fresh = не зберігати стан, можна повторювати)

Кілька сторінок: у settings.json список "pages" (у кожної свій page_id, база знань, токен-секрет і режим).
Стан (state.json) спільний: id коментарів різних сторінок не перетинаються.
Кнопки ✅/❌ знаходять потрібну сторінку за рядком «Сторінка: …» у чернетці.
"""
import json
import os
import re
import sys
import traceback

from . import config
from .facebook import Facebook, Mock
from . import gemini
from .gemini import Keys
from .runner import Bot
from .state import State
from .telegram import Telegram


def page_list(S, env):
    """Сторінки для цього запуску. Без "pages" у settings.json — одна сторінка, як раніше. env PAGE = лише одна зі списку."""
    raw = S.get("pages") or [{"key": "main", "name": "", "page_id": S["page_id"], "business_file": S["business_file"],
                              "auto_page_id": S["auto_page_id"], "token_env": "FB_PAGE_ACCESS_TOKEN"}]
    out = []
    for p in raw:
        P = {**S, "page_id": p.get("page_id", S["page_id"]), "auto_page_id": p.get("auto_page_id", ""),
             "business_file": p.get("business_file", S["business_file"]),
             "page_name": p.get("name", ""), "page_key": p.get("key") or p.get("name", ""),
             "token_env": p.get("token_env", "FB_PAGE_ACCESS_TOKEN"), "skip_topics": p.get("skip_topics", [])}
        # режим: ручний вибір у запуску > режим сторінки > загальний; перегляд завжди draft
        P["mode"] = "draft" if S["preview"] else (env.get("MODE") or p.get("mode") or S["mode"])
        out.append(P)
    want = (env.get("PAGE") or "").strip().lower()
    if want:
        out = [P for P in out if want in (P["page_key"].lower(), P["page_name"].lower())]
        if not out:
            raise SystemExit(f"Сторінку «{want}» не знайдено в settings.json → pages (key або name).")
    return out


def page_for_click(pages, ev):
    """Натискання кнопки → сторінка за рядком «Сторінка: …». Старі чернетки без підпису належать першій сторінці."""
    m = re.search(r"Сторінка: (\S+)", ev.get("text", ""))
    if m:
        for P in pages:
            if P["page_name"].lower() == m.group(1).lower():
                return P
    return pages[0]


def run_summary(bots, more, prev_total, done):
    """Підсумок запуску в Telegram: скільки оброблено, що саме, скільки лишилось і чому зупинилось."""
    def line(s):
        parts = [("📝", "чернеток", s["drafted"]), ("🤖", "надіслано сам", s["sent"]),
                 ("❓", "потрібна ваша відповідь", s["unknown"]), ("⏭", "пропущено", s["skip"]),
                 ("🚫", "не вдалось", s["failed"]), ("⚠️", "збій ШІ, повторю", s["error"])]
        return " · ".join(f"{i} {n} {t}" for i, t, n in parts if n) or "нічого нового"
    total = prev_total + done
    left = sum(b.left for b in bots)
    out = [f"✅ Запуск завершено: оброблено {done}" + (f" (усього у ланцюжку: {total})" if prev_total else "")]
    active = [b for b in bots if b.processed() or b.stats["error"]]
    if len(active) > 1:
        out += [f"{b.label or 'бот'}: {line(b.stats)}" for b in active]
    else:
        out.append(line((active or bots)[0].stats))
    why = ", ".join(dict.fromkeys(b.why for b in bots if b.why))
    if left and more:
        out.append(f"⏳ Лишилось ~{left}. Наступний запуск стартує сам.")
    elif left:
        out.append(f"⏸ Лишилось ~{left}" + (f" · зупинка: {why}" if why else "") + ". Продовжити: /run")
    else:
        out.append("Черга порожня: нових коментарів немає.")
    return "\n".join(out)


def make_source(S, P, env):
    """(джерело, система, база) для сторінки; None, якщо немає токена цієї сторінки."""
    business = (config.ROOT / P["business_file"]).read_text("utf-8")
    system = (config.ROOT / P["prompt_file"]).read_text("utf-8") + "\n\nБАЗА:\n" + business
    if S["source"] == "facebook":
        token = env.get(P["token_env"])
        if not token:
            return None
        return Facebook(P, token), system, business
    return Mock(), system, business


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ
    S = config.load()
    gemini.THINKING = S.get("thinking", "")
    keys = Keys()
    if not keys.keys:
        raise SystemExit("Немає секрету GEMINI_API_KEYS (ключі через кому).")
    persist = not ("--fresh" in argv or S["preview"] or S["source"] == "mock")
    st = State(config.ROOT / "state.json", persist)
    tg = Telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    pages = page_list(S, env)
    if env.get("STRESS"):  # stress-коментарі зараз про нарди: за замовчуванням перша сторінка, інша — через PAGE
        from .stress import run
        P = pages[0]
        business = (config.ROOT / P["business_file"]).read_text("utf-8")
        system = (config.ROOT / P["prompt_file"]).read_text("utf-8") + "\n\nБАЗА:\n" + business
        v = env.get("STRESS", "")
        return run(P, keys, system, business, tg, n=int(v) if v.isdigit() and int(v) > 1 else 130)
    ev = {}
    try:
        ev = json.loads(env.get("TG_EVENT") or "{}")
    except ValueError:
        pass
    if not isinstance(ev, dict):
        ev = {}
    first_error = None
    try:
        if ev.get("type") == "callback":  # натискання з Cloudflare Worker: лише воно, для однієї сторінки
            P = page_for_click(pages, ev)
            made = make_source(S, P, env)
            if not made:
                raise RuntimeError(f"Немає секрету {P['token_env']} для сторінки {P['page_name'] or P['page_key']}.")
            src, system, business = made
            Bot(P, st, src, tg, keys, system, business).click_event(ev)
        else:
            bots = []
            try:
                prev_total = int(ev.get("total") or 0)  # скільки вже оброблено у попередніх запусках ланцюжка
            except (TypeError, ValueError):
                prev_total = 0
            more = False
            lim_left = None
            for P in pages:
                label = P["page_name"] or P["page_key"]
                try:
                    made = make_source(S, P, env)
                    if not made:  # токена ще немає: пропускаємо сторінку, інші працюють
                        print(f"Сторінка {label}: немає секрету {P['token_env']}, пропускаю")
                        if st.alert_due("no_token_" + P["page_key"], 24):
                            tg.send(f"⚠️ Для сторінки {label} немає секрету {P['token_env']} у GitHub. Пропускаю її, решта працюють.")
                        continue
                    src, system, business = made
                    print(f"=== Сторінка: {label} ===")
                    bot = Bot(P, st, src, tg, keys, system, business)
                    bot.run()
                    bots.append(bot)
                    more = more or bot.more
                    if bot.limit_left is not None:
                        lim_left = max(lim_left or 0, bot.limit_left)
                except Exception as e:  # збій однієї сторінки не зупиняє іншу; після циклу все одно падаємо, щоб це було видно
                    traceback.print_exc()
                    where = traceback.extract_tb(e.__traceback__)[-1]
                    tg.send(f"🔍 Причина аварії ({label}): {type(e).__name__}: {str(e)[:300]}\n"
                            f"де: {where.filename.split('/')[-1]}:{where.lineno} ({where.name})")
                    first_error = first_error or e
                    st.save()
            if first_error:
                raise first_error
            done = sum(b.processed() for b in bots)
            if env.get("GITHUB_OUTPUT"):
                with open(env["GITHUB_OUTPUT"], "a") as f:
                    f.write(f"processed={done}\n")  # для лічильника «всього в ланцюжку»
            if more and env.get("GITHUB_OUTPUT"):  # сигнал для workflow: запустити наступний запуск (ланцюжок)
                with open(env["GITHUB_OUTPUT"], "a") as f:
                    f.write("more=1\n")
                    f.write(f"limit={lim_left if lim_left is not None else ''}\n")  # скільки ще обробити у наступному запуску (порожньо = без обмеження)
            if S["report"]:
                pass  # звіт сам є повідомленням
            elif S["preview"]:
                if ev.get("type") == "run":
                    tg.send("✅ Перегляд завершено (нічого не опубліковано).", silent=True)
            elif bots and (ev.get("type") == "run" or done or any(b.stats["error"] for b in bots)):
                tg.send(run_summary(bots, more, prev_total, done), silent=True)
    except Exception as e:  # показуємо причину аварії прямо в Telegram, щоб не шукати в логах
        if first_error is None:
            traceback.print_exc()
            where = traceback.extract_tb(e.__traceback__)[-1]
            tg.send(f"🔍 Причина аварії: {type(e).__name__}: {str(e)[:300]}\nде: {where.filename.split('/')[-1]}:{where.lineno} ({where.name})")
        if ev.get("type") == "callback":  # натискання не оброблено: повертаємо кнопки (їх прибрав Worker), state.json не чіпаємо
            if ev.get("data") in ("ok", "no") and ev.get("message_id"):
                tg.restore(tg.chat_id, ev["message_id"], ev.get("text", ""))
                tg.send("⚠️ Натискання не оброблено, кнопки повернуто — натисніть ще раз.", silent=True)
        else:
            st.save()
        raise
