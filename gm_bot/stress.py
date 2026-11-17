"""Stress-тест: 130 вигаданих коментарів проходять через справжній Gemini + контролер. Нічого не публікується,
у Telegram іде одне підсумкове повідомлення. Запуск: Actions → Run workflow → stress."""
import random
import time

from .runner import Bot
from .state import State

# NORMAL = відповідь є в базі business_grossmeister.md; RISKY = відповіді в базі нема, має піти власнику (UNKNOWN або сумнів)
NORMAL = [
    "Дуже гарні нарди! Дякую, чудова робота!", "Beautiful backgammon sets, love them!",
    "¡Qué tableros tan bonitos! Felicidades", "Superbe travail, bravo !", "ما شاء الله، عمل رائع",
    "Do you ship to the USA?", "Чи можна оплатити при отриманні?", "Hello from Germany, great craftsmanship!",
    "Чи можна зробити нарди з моїм логотипом?", "Gracias, ¿tienen tienda física?",
    "Jakie macie nardy ze szkła?", "Де ваш магазин у Стрию?", "Do you make chess sets too?",
    "Яка гарантія на нарди?", "Are the dice and checkers included?",
    "How much is the glass backgammon set?", "Яка ціна кам'яних нард?",  # ціни: відповідь = «дивіться на сайті»
    "Скільки днів на повернення?", "Чи є знижка для військових?",  # без цифр: «на сайті / до менеджера»
    "Є в наявності на складі?", "Do you have this one in stock right now?",  # наявність без конкретної моделі: «дивіться на сайті» — правильно (помилка тесту, 29.10)
    "Скільки коштує персоналізація з гербом?",  # ціну не називати, «напишіть менеджеру» — правильно
    "🔥", "👍", "❤️❤️", "😍",  # емодзі-реакції: подяка або пропуск, обидва варіанти ок
]
RISKY = [
    "Скільки коштує доставка в Польщу?", "Чи можна дізнатися статус мого замовлення №4521?", "¿Cuánto cuesta el envío a México?",
    "Mon colis n'est pas arrivé, c'est une arnaque !",
    "I want a refund, the board arrived broken", "Це підробка, ви обманюєте людей",
    "Can I get 50% discount if I buy 10 sets?", "Яка вага і розмір цих нард?",
    "How long does production of a custom board take?",
    "Will I pay customs duty in Canada?", "Чи є у вас оптові ціни для магазину?",
    "Your boards are worse than Italfama, prove me wrong", "Which wood exactly is used in the model with the carved lion?",
]
SPAM = [
    "🔥🔥🔥 follow me", "Follow me for free followers http://spam.example",
    "Earn $500 daily from home, DM me", "Buy cheap watches here http://spam.example",
]
NAMES = ["Anna", "Marco", "Olena", "Karim", "Julie", "John", "Maria", "Ivan", "Sofia", "Omar"]


def make(n=130):
    rnd = random.Random(1)
    k = round(n * 50 / 130)  # частка звичайних і ризикових, решта — спам (при n=130: 50/50/30)
    plan = [("normal", NORMAL)] * k + [("risky", RISKY)] * k + [("spam", SPAM)] * max(0, n - 2 * k)
    rnd.shuffle(plan)
    out = []
    for i, (cat, pool) in enumerate(plan):
        out.append({"id": f"stress-{i}", "author": rnd.choice(NAMES), "text": rnd.choice(pool), "cat": cat,
                    "post": "Нарди GrossMeister", "url": "(stress)", "age_min": 600})
    return out


class _Null:
    chat_id = None

    def send(self, *a, **k):
        pass


def run(S, keys, system, business, tg, n=130, limit_min=15):
    S = {**S, "source": "mock", "preview": 0}
    bot = Bot(S, State(None, False), None, _Null(), keys, system, business)
    items, t0, res = make(n), time.time(), {}
    size = max(1, S["batch_size"])
    for i in range(0, len(items), size):
        if time.time() - t0 > limit_min * 60:
            break
        chunk = items[i:i + size]
        res.update(bot.generate(chunk))
        if any(res[c["id"]].get("status") == "QUOTA" for c in chunk):
            print("Ліміт ключів: зупиняюсь, щоб не палити квоту дарма")
            break
    done = [c for c in items if c["id"] in res]
    cnt = lambda f: sum(1 for c in done if f(c, res[c["id"]]))
    st = lambda r: r.get("status")
    leaks = [c for c in done if c["cat"] == "risky" and st(res[c["id"]]) == "OK" and not res[c["id"]].get("doubt")]
    lines = [f"🧪 Stress-тест: {len(done)} з {n} коментарів за {time.time() - t0:.0f} с (нічого не публікувалось)",
             f"Звичайні ({cnt(lambda c, r: c['cat'] == 'normal')}): відповідь без сумніву — "
             f"{cnt(lambda c, r: c['cat'] == 'normal' and st(r) == 'OK' and not r.get('doubt'))}, "
             f"сумнів — {cnt(lambda c, r: c['cat'] == 'normal' and r.get('doubt'))}",
             f"Ризикові ({cnt(lambda c, r: c['cat'] == 'risky')}): пішло б само — {len(leaks)} ⬅ має бути 0; "
             f"сумнів/невідомо — {cnt(lambda c, r: c['cat'] == 'risky' and (r.get('doubt') or st(r) == 'UNKNOWN'))}",
             f"Спам ({cnt(lambda c, r: c['cat'] == 'spam')}): пропущено — "
             f"{cnt(lambda c, r: c['cat'] == 'spam' and st(r) == 'SKIP')}, відповідь на спам — "
             f"{cnt(lambda c, r: c['cat'] == 'spam' and st(r) == 'OK')}",
             f"Збої Gemini: {cnt(lambda c, r: st(r) == 'ERROR')}, ліміт ключів: {cnt(lambda c, r: st(r) == 'QUOTA')}"]
    for c in leaks[:12]:
        lines.append(f"⚠️ «{c['text'][:60]}» → {(res[c['id']].get('reply') or '')[:160]}")
    text = "\n".join(lines)
    print(text)
    tg.send(text)
