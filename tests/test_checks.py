from gm_bot.checks import reply_ok, reply_safe

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
