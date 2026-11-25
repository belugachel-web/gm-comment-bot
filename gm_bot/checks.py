"""Перевірки відповіді ШІ ПЕРЕД публікацією (мова, посилання, ціни, телефони)."""
import re

CYR = re.compile(r"[\u0400-\u04FF]")
ARB = re.compile(r"[\u0600-\u06FF]")
LAT = re.compile(r"[A-Za-z\u00C0-\u017F]")


_TRANSLIT_CYR = re.compile(
    r"\b(?:kak|skolko|skolka|kupit|mojno|mozhno|stoit|molodets|spasibo|tsena|zdrastv\w*|privet|"
    r"skilky|skilki|dyakuy\w*|otpravk\w*|dostavk\w*|magu)\b", re.I)
_RU_WORDS = re.compile(r"\b(?:как|могу|купить|купит|сколько|стоит|цена|город|доставка|отправка|заказать)\b", re.I)


def reply_ok(comment, reply):
    """Відповідь тим самим алфавітом, що й коментар, без змішаних літер у словах."""
    def cnt(t):
        return {"cyr": len(CYR.findall(t)), "arb": len(ARB.findall(t)), "lat": len(LAT.findall(t))}
    c, r = cnt(re.sub(r"@\S+", "", comment)), cnt(reply)  # @-згадка сторінки не визначає мову
    if not any(c.values()):
        return True  # лише смайли
    main = max(c, key=c.get)
    if main == "lat" and r["cyr"] > 0 and _TRANSLIT_CYR.search(comment):
        main = "cyr"  # трансліт ru/uk латиницею («kak mojno kupit», «Molodets») → відповідь кирилицею дозволена
    if r[main] == 0:
        return False
    ru_only, uk_only = re.compile("[ыэъёЫЭЪЁ]"), re.compile("[іїєґІЇЄҐ]")  # рос. коментар → укр. відповідь і навпаки (алфавіт той самий)
    if main == "cyr":
        cc = comment
        if (ru_only.search(cc) and not uk_only.search(cc) and uk_only.search(reply)) or \
                (uk_only.search(cc) and not ru_only.search(cc) and not _RU_WORDS.search(cc) and ru_only.search(reply)):
            return False
    if any(k != main and r[k] > 0 for k in ("cyr", "arb")):
        return False
    return not any(sum(1 for p in (CYR, ARB, LAT) if p.search(w)) > 1 for w in re.findall(r"\w+", reply))


def is_noise(text):
    """Коментар без жодної літери («5», «+», «???», «🔥🔥», «100»): відповідати нема на що, Gemini не кличемо.
    Довгі числа (телефон, 5+ цифр) — не шум: людина лишила контакт."""
    t = text or ""
    return not re.search(r"[^\W\d_]", t) and len(re.findall(r"\d", t)) <= 4


def off_topic(topics, *texts):
    """Перше стороннє слово зі списку сторінки (напр. «нард» для STRYI) у тексті допису чи коментаря, інакше None."""
    blob = " ".join(t or "" for t in texts).lower()
    return next((w for w in (topics or []) if w.lower() in blob), None)


_TAIL = r"""[^\s)>\]»”"']*"""
URL_RE = re.compile(
    r"(?:https?://|www\.)" + _TAIL +
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|ua|net|org|pl|de|me|io|ru|info|eu)\b(?:/" + _TAIL + ")?",
    re.I)
PHONE_RE = re.compile(r"\+?\d[\d ().-]{7,}\d")
MASKED_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{0,6}[*•xX]{2,}|\*{3,}\d{2,})")
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
    if re.search(r"(?:дякуємо|спасиб[оі])\s+ви\b", reply, re.I):
        return "граматична помилка (дякуємо Ви)"
    urls = [_norm_url(u) for u in URL_RE.findall(reply)]
    web_urls = [u for u in urls if not u.startswith("t.me/")]
    if len(urls) != len(set(urls)) or len(web_urls) > 1:
        return "повтор посилання у відповіді"
    return None


FOREIGN_MARKET = re.compile(r"woodcarving-tools\.com|(?:^|/)(?:en|pl|de|fr|es|it|pt)(?:/|$)", re.I)

# Країна перебиває мову (20.11, випадок Марокко): якщо в коментарі названа ІНША країна (не Україна), міжнародне посилання
# для укр/рос тексту допустиме. Стеми без закінчень, щоб не ловити «англійська», «німецька» тощо.
_COUNTRIES = (
    # укр/рос
    r"марокк|німеччин|германи[июяе]|польщ|польш|сша|оае|америка|америц|канад|англі[їяю]|англи[июя]|британі|британи|франці[їяю]|франци[июя]|італі[їяю]|итали[июя]|"
    r"іспані[їяю]|испани[июя]|туреччин|турци[июя]|ізраїл|израил|казахстан|грузі[їяю]|грузи[июя]|молдов|литв|латві[їяю]|латви[июя]|естон|чехі[їяю]|чехи[июя]|"
    r"словаччин|словаки|румуні|румыни|болгарі|болгари|угорщин|венгри|австрі[їяю]|австри[июя]|швейцар|нідерланд|голланд|бельгі[їяю]|бельги[июя]|"
    r"ірланд|швеці[їяю]|швеци[июя]|норвег|фінлянд|финлянд|португал|греці[їяю]|греци[июя]|австрал|японі[їяю]|япони[июя]|китай|бразил|мексик|"
    r"єгипет|египет|алжир|туніс|тунис|сауд|катар|дубай|дубаї|аргентин|чилі|колумб|нігері|нигери|кені|кени|"
    r"азербайджан|тбілісі|тбилиси|баку|єреван|ереван|еквадор|"
    # англ
    r"morocco|germany|poland|usa(?!\w)|u\.s\.a?\.?|united states|america|uae(?!\w)|emirates|dubai|canada|england|\buk\b|britain|united kingdom|france|italy|spain|turkey|türkiye|"
    r"israel|australia|japan|china|korea|brazil|mexico|egypt|algeria|tunisia|saudi|qatar|kuwait|india|pakistan|netherlands|belgium|ireland|sweden|norway|finland|denmark|"
    r"portugal|greece|austria|switzerland|czech|slovakia|romania|bulgaria|hungary|lithuania|latvia|estonia|moldova|kazakhstan|nigeria|kenya|south africa|philippines|"
    r"indonesia|malaysia|thailand|vietnam|singapore|new zealand|argentina|chile|colombia|peru|cuba|azerbaijan|georgia|tbilisi|baku|ecuador|"
    # інші мови
    r"marruecos|alemania|polonia|estados unidos|españa|francia|italia|turquía|méxico|maroko|niemcy|polsk|marokko|deutschland|maroc|allemagne|"
    r"المغرب|مصر|السعودية|الإمارات|الجزائر|تونس|قطر|الكويت|الأردن|لبنان|العراق|ليبيا"
)
FOREIGN_COUNTRY = re.compile(r"(?<!\w)(?:[ув](?=[А-ЯІЇЄҐ]))?(?:" + _COUNTRIES + r")", re.I)


def mentions_foreign_country(comment):
    return bool(FOREIGN_COUNTRY.search(comment))


NON_UKRAINIAN_CYR = re.compile(r"[ЈјЋћЂђЉљЊњЏџЅѕЪъЭэЫыЁё]")


def reply_market(comment, reply, allowed=()):
    """Кириличний (укр/рос) коментар → посилання українського сайту, а не міжнародного/іншомовного,
    ХІБА що в коментарі названа інша країна (країна перебиває мову) або це небезпечно інша кирилиця (сербська/болгарська тощо).
    Посилання, яке власник вказав у дописі (allowed), дозволене завжди."""
    if len(CYR.findall(comment)) <= len(LAT.findall(comment)):
        return None
    if mentions_foreign_country(comment) or NON_UKRAINIAN_CYR.search(comment) or NON_UKRAINIAN_CYR.search(reply):
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
    if MASKED_PHONE_RE.search(reply):
        return "замаскований телефон у відповіді"
    ok_phones = {_digits(p)[-9:] for p in PHONE_RE.findall(business)}
    for p in PHONE_RE.findall(reply):
        if _digits(p)[-9:] not in ok_phones:
            return f"телефон не з бази: {p.strip()[:30]}"
    m = PRICE_RE.search(reply)
    if m:
        return f"у відповіді ціна: {m.group(0).strip()[:30]}"
    return None
