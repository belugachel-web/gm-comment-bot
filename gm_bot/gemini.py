"""Gemini: ротація ключів, один запит на пачку коментарів, повтори при збоях, запасна модель."""
import json
import os
import re
import time

_sleep = time.sleep  # у тестах підміняється
THINKING = ""        # рівень думок (low/medium/high); задається з settings.json у main.py


class Keys:
    def __init__(self, raw=None):
        raw = os.getenv("GEMINI_API_KEYS", "") if raw is None else raw
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        self.i, self.dead = 0, {}

    def mark(self, k, daily, model=None):
        # model=None — основна модель; для інших моделей ліміт рахується окремо
        self.dead[k if model is None else (k, model)] = float("inf") if daily else time.time() + 62

    def get(self, model=None, wait=True):
        while self.keys:
            now = time.time()
            for _ in self.keys:
                k = self.keys[self.i % len(self.keys)]
                self.i += 1
                if self.dead.get(k if model is None else (k, model), 0) <= now:
                    return k
            mine = [self.dead.get(k if model is None else (k, model), 0) for k in self.keys]
            wake = min(mine)
            if wake == float("inf") or not wait:
                return None  # усі ключі вичерпали добу для цієї моделі (або wait=False: не чекаємо хвилину, йдемо на наступну модель)
            _sleep(max(1, wake - now))
        return None


def call_gemini(key, model, system, prompt, temperature):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=180000))  # 3 хв: без цього запит міг висіти годинами
    kw = dict(system_instruction=system, response_mime_type="application/json", temperature=temperature)
    if THINKING and "lite" not in model:  # Flash-Lite обходимось без «думок» (швидше й економніше)
        try:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=THINKING)
        except Exception:
            pass  # стара версія бібліотеки — працюємо без рівня думок
    r = _generate(client, model, prompt, kw, types)
    return r.text


def _generate(client, model, prompt, kw, types):
    """Нові моделі можуть відхилити temperature або рівень думок: при такій помилці повторюємо без цього параметра."""
    try:
        return client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(**kw))
    except Exception as e:
        m = str(e).lower()
        if "429" in m or "resource_exhausted" in m:
            raise
        drop = [k for k, w in (("temperature", "temperature"), ("thinking_config", "thinking")) if w in m and k in kw]
        if not drop:
            raise
        for k in drop:
            kw.pop(k)
        return client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(**kw))


def _mk(S, model):
    """Ключ обліку ліміту: None для основної моделі, назва — для інших."""
    return None if model == S["model"] else model


def model_error(m):
    """400/404: модель не знайдена або не підтримується. Повтори не допоможуть: одразу наступна модель."""
    low = m.lower()
    code = re.search(r"(?<!\d)(?:400|404)(?!\d)", low) or "not_found" in low or "invalid_argument" in low
    return bool(code and ("model" in low or "not found" in low or "not supported" in low))


def _ask(items, S, system, keys, temperature, call, model, prompt, fail, last=True):
    errors = rl = 0
    mk = _mk(S, model)
    while True:
        k = keys.get(mk, wait=last)  # не остання модель: ліміт ключів = одразу наступна модель, без очікування
        if not k:
            return fail("QUOTA", "Усі Gemini-ключі вичерпані")
        try:
            data = json.loads(call(k, model, system, prompt, temperature))
            lst = data if isinstance(data, list) else data.get("results", [])
            got = {str(x.get("id")): x for x in lst if isinstance(x, dict)}
            for x in got.values():  # «ok»/«Skip » → OK/SKIP, щоб дрібна різниця в регістрі не ставала «потрібна ваша відповідь»
                x["status"] = str(x.get("status") or "").strip().upper()
            return {c["id"]: got.get(c["id"], {"status": "ERROR", "reason": "Gemini пропустив коментар"}) for c in items}
        except Exception as e:
            m = str(e)
            if "429" in m or "RESOURCE_EXHAUSTED" in m:
                daily = "PerDay" in m or "per day" in m.lower()
                keys.mark(k, daily, mk) if mk else keys.mark(k, daily)
                rl += 1
                if rl > 3 * len(keys.keys) + 3:  # 429 повторюється вічно (не «на добу»): здаємось, далі запасна модель
                    return fail("QUOTA", "Gemini 429 повторюється: " + m[:100])
                continue
            if model_error(m):  # модель не знайдена / не підтримується: без повторів, одразу запасна модель
                return fail("ERROR", f"модель {model}: {m[:120]}")
            errors += 1
            if errors >= 3:
                return fail("ERROR", f"{type(e).__name__}: {m[:120]}")
            _sleep(3 * errors)


def ask_batch(items, S, system, keys, temp=None, call=None):
    """Повертає {id: {status, reply, reason}}. Службові статуси: QUOTA (ключі вичерпані), ERROR (збій, спробуємо пізніше).
    Якщо основна модель не відповіла (ліміт або збій) і задано fallback_model — одна спроба на запасній."""
    mkp = lambda its: "Коментарі:\n" + json.dumps(
        [{"id": c["id"], "author": c["author"], "text": c["text"], "post": c["post"][:200],
          **({"context": c["context"]} if c.get("context") else {}),
          **({"post_links": c["post_links"]} if c.get("post_links") else {})} for c in its],
        ensure_ascii=False)
    prompt = mkp(items)
    fail = lambda status, why: {c["id"]: {"status": status, "reason": why} for c in items}
    temperature = S["temperature"] if temp is None else temp
    call = call or call_gemini
    fbs = S.get("fallback_model") or []
    if isinstance(fbs, str):
        fbs = [fbs]
    fbs = [fb for fb in fbs if fb and fb != S["model"]]
    res = _ask(items, S, system, keys, temperature, call, S["model"], prompt, fail, last=not fbs)
    for n, fb in enumerate(fbs):  # запасні моделі по черзі, лише для коментарів, що лишились без відповіді
        sub = [c for c in items if res[c["id"]].get("status") in ("QUOTA", "ERROR")]
        if not sub:
            break
        fail2 = lambda status, why, sub=sub: {c["id"]: {"status": status, "reason": why} for c in sub}
        res2 = _ask(sub, S, system, keys, temperature, call, fb, mkp(sub), fail2, last=n == len(fbs) - 1)
        for c in sub:
            if res2.get(c["id"], {}).get("status") not in ("QUOTA", "ERROR", None):
                res[c["id"]] = res2[c["id"]]
    return res


REVIEW_SYSTEM = """Ти — суворий контролер відповідей бізнес-сторінки. На вході JSON-список {id, comment, reply, post?}.
Для КОЖНОГО елемента поверни {"id", "verdict": "OK" або "DOUBT", "why": ""}.
ВАЖЛИВО: якщо відповідь ПРАВИЛЬНА і безпечна — став "verdict": "OK" і ЛИШАЙ "why" ПОРОЖНІМ (""). Став "DOUBT" і заповнюй "why" ТІЛЬКИ якщо реально знайшов порушення або сумнів. Ніколи не пиши позитивний опис із verdict="DOUBT".
Посилання з поля post_links дозволені (це посилання з допису власника), не вважай їх «не з бази».
Якщо назва/тип інструмента вже згадана в полі post (текст допису), назвати його у відповіді — ПРАВИЛЬНО. Не став DOUBT через факти в post, якщо їх немає в reply.
Орієнтуйся на поле post (текст допису): сторінки STRYI (стамески) і GrossMeister (нарди/шахи, grossmeister.com.ua) одного власника; на STRYI бувають дописи про нарди/шахи, тому згадка нард/шахів і посилання grossmeister.com.ua в таких дописах або у відповіді на питання про нарди/шахи — ПРАВИЛЬНО, не вважай це «не тим сайтом» чи «чужою темою».
DOUBT, якщо будь-що з цього: reply називає факти (ціни, наявність, терміни, гарантії, умови, телефони, адреси), яких НЕМАЄ в БАЗІ; обіцяє те, чого немає в БАЗІ; замаскований телефон із зірочками (****);
не відповідає на суть коментаря (з урахуванням post); мова reply інша, ніж у comment (українська ↔ російська теж вважаються різними; сербська/хорватська/болгарська/польська тощо — це НЕ українська, відповідати ними на коментар цією ж мовою — ПРАВИЛЬНО; АЛЕ для трансліту латиницею відповідати кирилицею цієї ж мови і давати український сайт на російський коментар — ПРАВИЛЬНО, не сумнів); тон грубий або недоречний; коментар — скарга, претензія,
юридична або медична тема, провокація; політика, війна, релігія, санкції, доставка до країни-агресора; звернення на «ти» замість «Ви»; граматичні ляпи на кшталт «дякуємо Ви»; підтвердження конкретної породи дерева («підходить для дуба») або слів «чудово/відмінно працює»; посилання у відповіді на банальну подяку чи смайл; сумнів будь-якого роду.
Відповідь — лише JSON-список."""


def review_batch(pairs, S, business, keys, call=None):
    """pairs = [(коментар, відповідь)]. Повертає {id: None (впевнений) | причина сумніву}.
    Контролер може лише знижувати: збій перевірки = сумнів (неперевірене не публікується саме)."""
    ids = [c["id"] for c, _ in pairs]
    prompt = "Перевір:\n" + json.dumps(
        [{"id": c["id"], "comment": c["text"][:1000], "reply": r,
          **({"post": c["post"][:300]} if c.get("post") else {}),
          **({"post_links": c["post_links"]} if c.get("post_links") else {})} for c, r in pairs], ensure_ascii=False)
    system = REVIEW_SYSTEM + "\n\nБАЗА:\n" + business
    call = call or call_gemini
    models = [S.get("review_model") or S["model"]]  # контролер: основна модель, далі ті самі запасні, що й у генерації
    fbs = S.get("fallback_model") or []
    if isinstance(fbs, str):
        fbs = [fbs]
    models += [m for m in fbs if m and m not in models]
    why = "перевірка недоступна"
    for n, model in enumerate(models):
        out, why = _review_try(model, ids, prompt, system, S, keys, call, last=n == len(models) - 1)
        if out is not None:
            return out
    return {i: why for i in ids}  # жодна модель не відповіла = сумнів (власнику)


def _review_try(model, ids, prompt, system, S, keys, call, last=True):
    """Одна модель контролера: (результат, None) або (None, причина збою)."""
    mk = _mk(S, model)
    errors = rl = 0
    while True:
        k = keys.get(mk, wait=last)
        if not k:
            return None, "перевірка недоступна (ліміт ключів)"
        try:
            data = json.loads(call(k, model, system, prompt, 0))
            lst = data if isinstance(data, list) else data.get("results", [])
            got = {str(x.get("id")): x for x in lst if isinstance(x, dict)}
            out = {}
            for i in ids:
                x = got.get(i)
                if x is None:
                    out[i] = "контролер пропустив відповідь"
                elif str(x.get("verdict", "")).strip().upper() == "OK":
                    out[i] = None
                else:
                    out[i] = (str(x.get("why") or "сумнів") .replace("\n", " "))[:150]
            return out, None
        except Exception as e:
            m = str(e)
            if "429" in m or "RESOURCE_EXHAUSTED" in m:
                daily = "PerDay" in m or "per day" in m.lower()
                keys.mark(k, daily, mk) if mk else keys.mark(k, daily)
                rl += 1
                if rl > 3 * len(keys.keys) + 3:
                    return None, "перевірка недоступна (429)"
                continue
            if model_error(m):  # модель не знайдена / не підтримується: одразу наступна модель
                return None, f"перевірка недоступна: модель {model}: {m[:100]}".replace("\n", " ")
            errors += 1
            if errors >= 3:
                return None, f"перевірка недоступна: {type(e).__name__}: {m[:100]}".replace("\n", " ")
            _sleep(3 * errors)
