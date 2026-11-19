"""Перевірки відповіді ШІ ПЕРЕД публікацією (мова, посилання, ціни, телефони)."""
import re

CYR = re.compile(r"[\u0400-\u04FF]")
ARB = re.compile(r"[\u0600-\u06FF]")
LAT = re.compile(r"[A-Za-z\u00C0-\u017F]")


def reply_ok(comment, reply):
    """Відповідь тим самим алфавітом, що й коментар, без змішаних літер у словах."""
    def cnt(t):
        return {"cyr": len(CYR.findall(t)), "arb": len(ARB.findall(t)), "lat": len(LAT.findall(t))}
    c, r = cnt(comment), cnt(reply)
    if not any(c.values()):
        return True  # лише смайли
    main = max(c, key=c.get)
    if r[main] == 0:
        return False
    if any(k != main and r[k] > 0 for k in ("cyr", "arb")):
        return False
    return not any(sum(1 for p in (CYR, ARB, LAT) if p.search(w)) > 1 for w in re.findall(r"\w+", reply))


_TAIL = r"""[^\s)>\]»”"']*"""
URL_RE = re.compile(
    r"(?:https?://|www\.)" + _TAIL +
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|ua|net|org|pl|de|me|io|ru|info|eu)\b(?:/" + _TAIL + ")?",
    re.I)
PHONE_RE = re.compile(r"\+?\d[\d ().-]{7,}\d")
PRICE_RE = re.compile(
    r"\d[\d\s.,]*\s*(?:грн|гривн\w*|uah|usd|eur|pln|zł|zl|€|\$|£|₴|dollars?|euros?|долар\w*|доллар\w*|євро|злот\w*|złot\w*)"
    r"(?![A-Za-zА-Яа-яІіЇїЄєҐґ])|[$€£₴]\s*\d", re.I)


def _norm_url(u):
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.lower()).rstrip("/.,;:!?")


EMOJI_RE = re.compile("(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2764])\uFE0F?")


def tidy(reply):
    """Дрібні виправлення без зміни змісту: емодзі/крапка впритул до останнього посилання (ламає лінк),
    нічого після посилання, не більше одного емодзі."""
    r = reply.strip()
    ms = list(URL_RE.finditer(r))
    if ms:
        m = ms[-1]
        if not re.search(r"\w", r[m.end():]):  # після посилання лише розділові знаки/емодзі: прибираємо
            r = r[:m.start()] + re.sub(r"[^A-Za-z0-9/_~#=&%+@-]+$", "", m.group())
    first = EMOJI_RE.search(r)
    if first:
        r = r[:first.end()] + EMOJI_RE.sub("", r[first.end():])
    return r.strip()


def reply_style(reply):
    """None якщо стиль гаразд; інакше причина (хештеги, @-згадки, Markdown, службові символи, надто довго)."""
    if len(reply) > 700:
        return "надто довга відповідь"
    if re.search(r"(?<!\w)#\w", reply):
        return "хештег у відповіді"
    if re.search(r"(?<![\w.])@\w", reply):  # пошта orders@stryi.ua — не згадка
        return "@-згадка у відповіді"
    if re.search(r"\*\*|__|`|^\s*#{1,6}\s|^\s*[-*\u2022]\s", reply, re.M):
        return "розмітка Markdown або список"
    if re.search(r"[{}\[\]]|\bnull\b|\bNone\b", reply):
        return "службові символи у відповіді"
    return None


FOREIGN_MARKET = re.compile(r"woodcarving-tools\.com|(?:^|/)(?:en|pl|de|fr|es|it|pt)(?:/|$)", re.I)


def reply_market(comment, reply, allowed=()):
    """Кириличний (укр/рос) коментар → посилання українського сайту, а не міжнародного/іншомовного.
    Посилання, яке власник вказав у дописі (allowed), дозволене завжди."""
    if len(CYR.findall(comment)) <= len(LAT.findall(comment)):
        return None
    ok = {_norm_url(u) for u in allowed}
    for u in URL_RE.findall(reply):
        n = _norm_url(u)
        if n not in ok and FOREIGN_MARKET.search(n):
            return f"посилання іншого ринку: {u[:60]}"
    return None


def _digits(p):
    return re.sub(r"\D", "", p)


def reply_safe(reply, business):
    """None якщо все гаразд, інакше причина. Блокує: чужі посилання, ціни, чужі телефони."""
    ok_urls = {_norm_url(u) for u in URL_RE.findall(business)}
    for u in URL_RE.findall(reply):
        if _norm_url(u) not in ok_urls:
            return f"посилання не з бази: {u[:60]}"
    ok_phones = {_digits(p)[-9:] for p in PHONE_RE.findall(business)}
    for p in PHONE_RE.findall(reply):
        if _digits(p)[-9:] not in ok_phones:
            return f"телефон не з бази: {p.strip()[:30]}"
    m = PRICE_RE.search(reply)
    if m:
        return f"у відповіді ціна: {m.group(0).strip()[:30]}"
    return None
