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


def test_translit_and_pasted_header_allow_cyrillic_reply():
    assert reply_ok("kak mojno kupit", "Оформить заказ можно на нашем сайте")
    assert reply_ok("Molodets", "Большое спасибо!")
    assert reply_ok("Професійні стамески і різці по дереву STRYI Как Магу Купит В Город Тбилиси",
                    "Оформить заказ можно на нашем сайте")
