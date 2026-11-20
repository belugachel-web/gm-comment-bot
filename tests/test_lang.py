from gm_bot.checks import reply_ok


def test_russian_comment_ukrainian_reply_rejected():
    assert not reply_ok("Сколько хотите за нарды", "Дякуємо за запитання! Заходьте на наш сайт")
    assert reply_ok("Сколько хотите за нарды", "Спасибо за вопрос! Заходите на наш сайт")


def test_ukrainian_comment_russian_reply_rejected():
    assert not reply_ok("Скільки коштує набір?", "Спасибо, цены смотрите на сайте")
    assert reply_ok("Скільки коштує набір?", "Дякуємо, дивіться на сайті")


def test_ambiguous_cyrillic_comment_accepts_either_language():
    assert reply_ok("Цена?", "Дякуємо! Ціни на сайті")
    assert reply_ok("Цена?", "Спасибо! Цены на сайте")
