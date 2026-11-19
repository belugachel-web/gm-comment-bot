"""Telegram: сповіщення, чернетки з кнопками ✅/❌, збір натискань."""
import httpx


class Telegram:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, chat_id

    def _call(self, method, http_timeout=30, **p):
        if not self.token:
            print(f"[TG:{method}] (немає токена Telegram)")
            return None
        try:
            r = httpx.post(f"https://api.telegram.org/bot{self.token}/{method}", json=p, timeout=http_timeout)
            data = r.json()
        except Exception as e:
            print(f"Telegram {method}: {type(e).__name__}")
            return None
        if not data.get("ok"):
            print(f"Telegram {method}: {data.get('description')}")
            return None
        return data.get("result")

    def send(self, text, buttons=False, silent=False):
        p = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True,
             "disable_notification": silent}
        if buttons == "auto":
            p["reply_markup"] = {"inline_keyboard": [[
                {"text": "🤖 Увімкнути авто", "callback_data": "auto_on"},
                {"text": "📝 Лишити чернетки", "callback_data": "auto_off"}]]}
        elif buttons:
            p["reply_markup"] = {"inline_keyboard": [[
                {"text": "✅ Опублікувати", "callback_data": "ok"},
                {"text": "❌ Скасувати", "callback_data": "no"}]]}
        return self._call("sendMessage", **p)  # результат (з message_id) потрібен для прогрес-повідомлення, що редагується

    def clicks(self, offset, timeout=0):
        """Натискання кнопок. None = помилка/нема токена."""
        return self._call("getUpdates", http_timeout=timeout + 30, offset=offset,
                          allowed_updates=["callback_query"], timeout=timeout)

    def answer(self, callback_id, text):
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def edit(self, chat_id, message_id, text):
        self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4000])

    def restore(self, chat_id, message_id, text):
        """Повернути текст чернетки і кнопки ✅/❌ (якщо обробка натискання не вдалася)."""
        self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4000],
                   disable_web_page_preview=True,
                   reply_markup={"inline_keyboard": [[
                       {"text": "✅ Опублікувати", "callback_data": "ok"},
                       {"text": "❌ Скасувати", "callback_data": "no"}]]})
