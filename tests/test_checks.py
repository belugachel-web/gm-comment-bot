from gm_bot.checks import is_noise, mentions_foreign_country, off_topic, reply_market, reply_ok, reply_safe, reply_style, tidy

BUSINESS = ("Сайти: https://grossmeister.com.ua , https://grossmeister.com.ua/en/ , https://grossmeister.com.ua/pl/\n"
            "Телефон: 073 723 44 55\nViber: +380 73 267-00-70\n8 000 покупців у 88 країнах")


def test_language_match():
    assert reply_ok("How much?", "Please visit our site")
    assert not reply_ok("How much?", "Дякуємо, Ви можете оформити замовлення")
    assert reply_ok("Скільки коштує?", "Актуальні ціни на сайті")
    assert not reply_ok("Скільки коштує?", "Check the website")
    assert reply_ok("🔥🔥", "Дякуємо!")


def test_mixed_letters_in_word_rejected():
    assert not reply_ok("Скільки?", "Дякуємо за GrossМeister")  # латинська й кирилиця в одному слові


def test_allowed_urls_pass():
    assert reply_safe("Ось наш сайт: https://grossmeister.com.ua/en/", BUSINESS) is None
    assert reply_safe("Visit grossmeister.com.ua/pl/.", BUSINESS) is None


def test_foreign_urls_blocked():
    assert reply_safe("Пишіть https://t.me/scam", BUSINESS)
    assert reply_safe("Write to t.me/scam", BUSINESS)
    assert reply_safe("https://grossmeister.com.ua/secret-sale", BUSINESS)


def test_prices_blocked():
    for t in ("Це коштує 1290 грн", "It costs $32", "Only 25 EUR", "Cena 300 zł", "just €45", "≈ 40 dollars"):
        assert reply_safe(t, BUSINESS), t


def test_numbers_that_are_not_prices_pass():
    assert reply_safe("Нас обрали понад 8 000 покупців у 88 країнах", BUSINESS) is None
    assert reply_safe("Гарантія 1 місяць, на аксесуари 14 днів", BUSINESS) is None


def test_phones():
    assert reply_safe("Телефонуйте 073 723 44 55", BUSINESS) is None
    assert reply_safe("Viber +380 73 267-00-70", BUSINESS) is None
    assert reply_safe("Телефонуйте 099 111 22 33", BUSINESS)


def test_tidy_strips_tail_after_last_link():
    assert tidy("Visit https://x.com/en/😊") == "Visit https://x.com/en/"
    assert tidy("See https://x.com/en/. 😊") == "See https://x.com/en/"
    assert tidy("Hello! https://x.com and bye") == "Hello! https://x.com and bye"  # далі є слова: не чіпаємо


def test_tidy_keeps_only_first_emoji():
    assert tidy("Thanks 😊 great 🔥") == "Thanks 😊 great"


def test_style_blocks_hashtags_mentions_markdown_but_not_email():
    assert reply_style("Great #sale")
    assert reply_style("Hi @john")
    assert reply_style("**bold** text")
    assert reply_style("x" * 701)
    assert reply_style("Write orders@stryi.ua") is None
    assert reply_style("Thank you, see you soon") is None


def test_market_cyrillic_comment_gets_no_foreign_link():
    assert reply_market("Скільки коштує?", "Сайт https://woodcarving-tools.com")
    assert reply_market("Скільки коштує?", "Сайт https://grossmeister.com.ua/en/")
    assert reply_market("Скільки коштує?", "Сайт https://stryi.ua") is None
    assert reply_market("How much?", "Site https://woodcarving-tools.com") is None
    assert reply_market("Скільки?", "Сайт https://woodcarving-tools.com", ["https://woodcarving-tools.com"]) is None


def test_market_foreign_country_overrides_language():
    # укр/рос коментар із іншої країни → міжнародний сайт дозволений
    assert reply_market("Я з Марокко, можна замовити?", "Відправляємо по світу https://woodcarving-tools.com") is None
    assert reply_market("Из Германии можно заказать?", "Отправляем по миру https://woodcarving-tools.com") is None
    assert reply_market("Доставляєте в Польщу?", "Так https://woodcarving-tools.com") is None
    # без згадки іншої країни — блокуємо, як раніше
    assert reply_market("Скільки коштує, англійська версія є?", "Сайт https://woodcarving-tools.com")
    assert reply_market("Доставка по Україні?", "Сайт https://woodcarving-tools.com")


def test_mentions_foreign_country():
    assert mentions_foreign_country("I am from Morocco, can I order?")
    assert mentions_foreign_country("Доставка в Німеччину є?")
    assert mentions_foreign_country("هل توصلون إلى المغرب؟")
    assert not mentions_foreign_country("Працює англійською? Німецька сталь?")
    assert not mentions_foreign_country("Доставка по Україні, Львів?")


def test_is_noise():
    for t in ("5", "+", "???", "🔥🔥", "100", "", "1 2 3", "+5!!"):
        assert is_noise(t), t
    for t in ("0501234567", "+380 50 123 45 67", "Ціна?", "好漂亮", "5 грн?", "Price"):
        assert not is_noise(t), t


def test_off_topic():
    topics = ["нард", "chess"]
    assert off_topic(topics, "Нарди з каменю", "Скільки?") == "нард"
    assert off_topic(topics, "post", "Nice CHESS set") == "chess"
    assert off_topic(topics, "Набір стамесок", "Скільки?") is None
    assert off_topic([], "Нарди") is None and off_topic(None, "Нарди") is None
