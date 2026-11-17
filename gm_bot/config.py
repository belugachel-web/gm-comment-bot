"""Налаштування: settings.json + змінні середовища (їх задає workflow)."""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULTS = {
    "source": "mock",            # mock | facebook
    "mode": "draft",             # draft | auto
    "model": "gemini-3.5-flash-lite",
    "graph_version": "v26.0",
    "page_id": "",
    "auto_page_id": "",          # auto дозволений ЛИШЕ для цієї сторінки; інакше бот працює як draft
    "pages": [],                 # кілька сторінок: [{key, name, page_id, business_file, token_env, mode, auto_page_id}]; порожньо = одна (page_id вище)
    "business_file": "business_grossmeister.md",
    "prompt_file": "prompt.md",
    "batch_size": 10,            # скільки коментарів в одному запиті до Gemini
    "temperature": 0.4,
    "posts_limit": 25,           # постів на одну сторінку відповіді Facebook
    "max_posts": 200,
    "max_comments_per_post": 1000,
    "reply_old": True,
    "old_after_hours": 6,        # «старий» коментар
    "max_age_hours": 720,        # старші за це ігноруємо
    "new_wait_min": 0,           # скільки хв чекати перед відповіддю на новий
    "gap_sec": [20, 90],         # пауза між публікаціями (захист від спам-фільтра)
    "max_replies_per_run": 15,
    "max_replies_per_day": 150,  # запобіжник для auto
    "wait_buttons_min": 10,      # у draft: скільки хв чекати натискань після нових чернеток (не працює при tg_webhook)
    "tg_webhook": False,         # True = кнопки приходять через Cloudflare Worker (після setWebhook); getUpdates тоді не використовується
    "max_run_min": 20,
    "notify_auto": True,         # у auto: тиха копія кожної відповіді в Telegram
    "trial_n": 30,               # пробний період: стільки чернеток власник рішає вручну, перш ніж бот запропонує auto
    "skip_trial": False,         # True = без пробного періоду: mode=auto публікує одразу
    "trial_min_pct": 90,         # скільки % має бути схвалено (✅), щоб запропонувати auto
    "review": True,              # друга перевірка ШІ: може лише знизити впевненість (сумнів → власнику)
    "review_model": "",         # порожньо = та сама model; можна вказати іншу для незалежності
    "fallback_model": "",       # запасна модель, якщо основна перевантажена або вичерпала ліміт (порожньо = немає)
    "max_ai_tries": 3,          # скільки запусків пробувати коментар, на якому Gemini падає, потім пропускаємо
    "thinking": "",             # рівень думок основної моделі: low | medium | high (порожньо = за замовчуванням Google)
}


def load(env=None):
    env = os.environ if env is None else env
    raw = json.loads((ROOT / "settings.json").read_text("utf-8"))
    for k in raw:
        if k not in DEFAULTS:
            print(f"⚠️ settings.json: невідомий ключ «{k}» (помилка в написанні?)")
    S = {**DEFAULTS, **raw}
    S["mode"] = env.get("MODE") or S["mode"]
    S["source"] = env.get("SOURCE") or S["source"]
    S["preview"] = int(env.get("PREVIEW") or 0)   # N відповідей у Telegram, нічого не публікується
    S["report"] = bool(env.get("REPORT"))
    if env.get("OLD") == "0":   # з Telegram-меню: «лише нові»
        S["reply_old"] = False
    if S["mode"] not in ("draft", "auto"):
        raise SystemExit(f"settings.json: mode має бути draft або auto, а не «{S['mode']}»")
    if S["source"] not in ("mock", "facebook"):
        raise SystemExit(f"settings.json: source має бути mock або facebook, а не «{S['source']}»")
    if S["source"] == "facebook" and not S["page_id"] and not S["pages"]:
        raise SystemExit("settings.json: не вказано page_id")
    if S["preview"]:
        S.update(mode="draft", reply_old=True, max_age_hours=9_999_999, old_after_hours=0,
                 new_wait_min=0, gap_sec=[0, 0], max_replies_per_run=S["preview"])
    # вік коментарів (з Telegram-меню / ручного запуску): AGE = all | число днів. Діє і на відповіді, і на звіт, і на перегляд
    S["age_set"], S["age_days"] = False, 0
    age = (env.get("AGE") or "").strip().lower()
    if age in ("all", "усі"):
        S.update(max_age_hours=9_999_999, age_set=True)
    elif age.isdigit() and int(age) > 0:
        S.update(max_age_hours=int(age) * 24, age_set=True, age_days=int(age))
    lim = (env.get("LIMIT") or "").strip()  # скільки коментарів обробити всього (з ланцюжком запусків); 0 = без обмеження
    S["limit"] = int(lim) if lim.isdigit() else 0
    return S
