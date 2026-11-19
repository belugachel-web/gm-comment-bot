// gm-bot-trigger: Telegram webhook -> GitHub Actions (service.yml, workflow_dispatch)
// Секрети Worker (Settings -> Variables and Secrets): GH_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID, WEBHOOK_SECRET
// GH_TOKEN має право Actions: Read and write (запускати І скасовувати запуски — для кнопки «Стоп»)
const REPO = "belugachel-web/gm-comment-bot";
const WORKFLOW = "service.yml";
const REF = "main";

const PAGE_VAL = { a: "", n: "nardy", s: "stryi" };
const PAGE_LABEL = { a: "Всі сторінки", n: "GrossMeister", s: "STRYI" };
const MODE_VAL = { d: "draft", a: "auto" };
const PV_VAL = { "0": "", "1": "10", "5": "50" };
const AGE_DEFAULT = "30";            // вік за замовчуванням (днів)
const AGE_PRESETS = ["a", "7", "30", "365"];   // a = усі
const AGE_MAX = 3650;
const LIM_PRESETS = ["a", "10", "100", "500"]; // a = без ліміту
const LIM_MAX = 100000;

async function tg(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const ghHeaders = (env) => ({
  authorization: `Bearer ${env.GH_TOKEN}`,
  accept: "application/vnd.github+json",
  "user-agent": "gm-bot-trigger",
  "x-github-api-version": "2022-11-28",
});

async function dispatch(env, tgEvent) {
  const r = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    { method: "POST", headers: ghHeaders(env), body: JSON.stringify({ ref: REF, inputs: { tg_event: JSON.stringify(tgEvent) } }) }
  );
  return r.status === 204;
}

// ---------- СТОП: скасувати всі запуски бота (крім коротких обробок кнопок ✅/❌) ----------
async function activeRuns(env) {
  const out = new Map();
  for (const wf of ["service.yml", "bot.yml"]) {
    for (const status of ["in_progress", "queued"]) {
      try {
        const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${wf}/runs?status=${status}&per_page=50`, { headers: ghHeaders(env) });
        if (!r.ok) continue;
        const d = await r.json();
        for (const run of d.workflow_runs || []) if (run.display_title !== "click") out.set(run.id, run);
      } catch { /* пробуємо далі */ }
    }
  }
  return [...out.values()];
}

async function cancelAll(env) {
  let n = 0;
  for (let round = 0; round < 2; round++) { // другий прохід — якщо ланцюжок встиг запустити наступний
    for (const run of await activeRuns(env)) {
      const r = await fetch(`https://api.github.com/repos/${REPO}/actions/runs/${run.id}/cancel`, { method: "POST", headers: ghHeaders(env) });
      if (r.status === 202) n++;
    }
    if (round === 0) await new Promise((res) => setTimeout(res, 6000));
  }
  return n;
}

const STOP_KB = { inline_keyboard: [[{ text: "⏹ Стоп", callback_data: "ms:stop" }]] };
const STOP_CONFIRM_TEXT =
  "⏹ Точно зупинити бота?\n\nРобота перерветься одразу. Що вже опубліковано — лишається. " +
  "Решту можна буде продовжити новим запуском (/run).";
const STOP_CONFIRM_KB = {
  inline_keyboard: [[{ text: "✅ Так, зупинити", callback_data: "ms:stopyes" }, { text: "↩️ Ні, працює далі", callback_data: "ms:stopno" }]],
};

// ---------- меню запуску ----------
// callback_data: m:<сторінка><режим><перегляд>:<вік>:<кількість>:<дія>
// вік: "a" = усі, або дні. кількість: "a" = без ліміту, або скільки коментарів обробити всього
function normAge(a) {
  if (a === "a") return "a";
  const n = Number(a);
  return /^\d+$/.test(String(a)) && n >= 1 && n <= AGE_MAX ? String(n) : AGE_DEFAULT;
}
function normLim(l) {
  if (l === "a") return "a";
  const n = Number(l);
  return /^\d+$/.test(String(l)) && n >= 1 && n <= LIM_MAX ? String(n) : "a";
}
function parse(st, age, lim) {
  let [P, M, V] = (st || "").split("");
  if (!"ans".includes(P || "?")) P = "a";
  if (!"da".includes(M || "?")) M = "d";
  if (!"015".includes(V || "?")) V = "0";
  return [P, M, V, normAge(age), normLim(lim)];
}

const ageLabel = (A) => (A === "a" ? "усі (і найстаріші)" : `не старші за ${A} дн.`);
const limLabel = (L) => (L === "a" ? "усі, що є" : `${L} шт`);
const ageValue = (A) => (A === "a" ? "all" : A);
const limValue = (L) => (L === "a" ? "" : L);

const mainText = (M, P = "a", A = AGE_DEFAULT, L = "a") =>
  "🤖 БОТ · " + PAGE_LABEL[P] + " · режим: " + (M === "a" ? "⚠️ АВТО (публікує сам)" : "чернетки (✅/❌ вам у Telegram)") + "\n" +
  "Вік коментарів: " + ageLabel(A) + "\n" +
  "Скільки обробити: " + limLabel(L) + "\n" +
  "📋 Звіт і 👁 Перевірка нічого не публікують. ⏹ Стоп зупиняє бота.";

function mainKb(P, M, V, A = AGE_DEFAULT, L = "a") {
  const ck = (on) => (on ? "✅ " : "");
  const b = (text, p, m, v, act = "s", age = A, lim = L) => ({ text, callback_data: `m:${p}${m}${v}:${age}:${lim}:${act}` });
  const customAge = !AGE_PRESETS.includes(A);
  const customLim = !LIM_PRESETS.includes(L);
  return {
    inline_keyboard: [
      [b(ck(P === "a") + "Всі", "a", M, "0"), b(ck(P === "n") + "GrossMeister", "n", M, "0"), b(ck(P === "s") + "STRYI", "s", M, "0")],
      [b(ck(M === "d") + "Чернетки", P, "d", "0"), b(ck(M === "a") + "АВТО", P, "a", "0")],
      // вік коментарів
      [b(ck(A === "a") + "Усі", P, M, "0", "s", "a"), b(ck(A === "7") + "7 дн", P, M, "0", "s", "7"),
       b(ck(A === "30") + "30 дн", P, M, "0", "s", "30"), b(ck(A === "365") + "365 дн", P, M, "0", "s", "365")],
      // скільки обробити
      [b(ck(L === "a") + "Без ліміту", P, M, "0", "s", A, "a"), b(ck(L === "10") + "10 шт", P, M, "0", "s", A, "10"),
       b(ck(L === "100") + "100 шт", P, M, "0", "s", A, "100"), b(ck(L === "500") + "500 шт", P, M, "0", "s", A, "500")],
      [b(customAge ? `✅ ✏️ вік ${A} дн` : "✏️ Свій вік", P, M, "0", "custa"),
       b(customLim ? `✅ ✏️ ${L} шт` : "✏️ Своя кількість", P, M, "0", "custl")],
      [b(M === "a" ? "▶️ ЗАПУСТИТИ АВТО (публікує!)" : "▶️ Запустити (чернетки)", P, M, "0", "go")],
      [b("📋 Звіт", P, M, "0", "rep"), b("👁 Перевірка 10", P, M, "1", "go")],
      [{ text: "⏹ Стоп", callback_data: "ms:stop" }, b("⚙️ Службове", P, M, "0", "svc")],
    ],
  };
}

const SVC_TEXT =
  "⚙️ СЛУЖБОВЕ (не чіпати без потреби)\n\n" +
  "🎭 Mock — 10 відповідей на вигадані коментарі (перевірка без Facebook). Нічого не публікує.\n" +
  "🧪 Тест 30 / 130 — вигадані коментарі нард через Gemini, підсумок у Telegram. " +
  "Витрачає ключі Gemini (30 — дешево). Нічого не публікує.\n\n" +
  "📋 Звіт — у головному меню (враховує сторінку і вік коментарів).";

const SVC_KB = {
  inline_keyboard: [
    [{ text: "🎭 Mock 10", callback_data: "ms:mock" }],
    [{ text: "🧪 Тест 30", callback_data: "ms:t30" }, { text: "🧪 Тест 130", callback_data: "ms:t130" }],
    [{ text: "⬅️ Назад", callback_data: `m:ad0:${AGE_DEFAULT}:a:s` }],
  ],
};

async function showMenu(env, chatId) {
  await tg(env, "sendMessage", { chat_id: chatId, text: mainText("d", "a"), reply_markup: mainKb("a", "d", "0") });
}

async function showStopConfirm(env, chatId) {
  await tg(env, "sendMessage", { chat_id: chatId, text: STOP_CONFIRM_TEXT, reply_markup: STOP_CONFIRM_KB });
}

async function menuCallback(env, cb, chatId) {
  const mid = cb.message.message_id;
  const edit = (text, reply_markup) =>
    tg(env, "editMessageText", { chat_id: chatId, message_id: mid, text, ...(reply_markup ? { reply_markup } : {}) });
  const answer = (text) => tg(env, "answerCallbackQuery", { callback_query_id: cb.id, ...(text ? { text } : {}) });

  if (cb.data.startsWith("ms:")) {
    const act = cb.data.slice(3);
    if (act === "open") { await Promise.all([answer(), edit(SVC_TEXT, SVC_KB)]); return; }
    if (act === "stop") { await Promise.all([answer(), edit(STOP_CONFIRM_TEXT, STOP_CONFIRM_KB)]); return; }
    if (act === "stopno") {
      await Promise.all([answer("Добре"), edit("↩️ Добре, нічого не зупиняю.\nЗупинити: /stop · меню: /run", STOP_KB)]);
      return;
    }
    if (act === "stopyes") {
      await answer("⏳ Зупиняю…");
      await edit("⏳ Зупиняю бота…");
      const n = await cancelAll(env);
      await edit(n > 0
        ? `⏹ Зупинено (скасовано запусків: ${n}).\nЩо вже опубліковано — лишилось. Продовжити: /run`
        : "ℹ️ Зараз нічого не працює (або скасувати не вдалося — перевірте GitHub → Actions).");
      return;
    }
    const ev = { type: "run", page: "", mode: "", preview: "", report: "", stress: "", source: "" };
    let label = "";
    if (act === "mock") { ev.source = "mock"; ev.preview = "10"; label = "🎭 Mock 10"; }
    else if (act === "t30") { ev.stress = "30"; label = "🧪 Тест 30"; }
    else if (act === "t130") { ev.stress = "130"; label = "🧪 Тест 130"; }
    else { await answer(); return; }
    const ok = await dispatch(env, ev);
    await Promise.all([answer(ok ? "⏳ Запускаю…" : "❌ Не вдалося запустити"), edit(ok ? `${label} — запущено…` : "❌ Не вдалося запустити бота", ok ? STOP_KB : undefined)]);
    return;
  }

  // m:<стан>:<вік>:<кількість>:<дія>   (старі формати з меншою кількістю частин теж розуміємо)
  const parts = cb.data.split(":");
  const act = parts[parts.length - 1];
  const [P, M, V, A, L] = parse(parts[1], parts.length >= 5 ? parts[2] : AGE_DEFAULT, parts.length >= 5 ? parts[3] : "a");

  if (act === "svc") { await Promise.all([answer(), edit(SVC_TEXT, SVC_KB)]); return; }

  if (act === "custa" || act === "custl") { // своє число: просимо відповісти числом на це повідомлення
    const isAge = act === "custa";
    await Promise.all([
      answer(),
      tg(env, "sendMessage", {
        chat_id: chatId,
        text: (isAge
          ? `✏️ Наскільки старі коментарі брати? Напишіть число днів від 1 до ${AGE_MAX} (наприклад 14)`
          : `✏️ Скільки коментарів обробити всього? Напишіть число від 1 до ${LIM_MAX} (наприклад 250)`) +
          " у відповідь на це повідомлення.\n\n" + `#m:${P}${M}${V}:${A}:${L}:${mid}:${isAge ? "a" : "l"}`,
        reply_markup: { force_reply: true, input_field_placeholder: isAge ? "днів, наприклад 14" : "штук, наприклад 250", selective: true },
      }),
    ]);
    return;
  }

  if (act === "rep") {
    const ok = await dispatch(env, { type: "run", page: PAGE_VAL[P], report: "1", age: ageValue(A) });
    await Promise.all([
      answer(ok ? "⏳ Роблю звіт…" : "❌ Не вдалося запустити"),
      edit(ok ? `📋 Звіт запущено\nСторінка: ${PAGE_LABEL[P]}\nКоментарі: ${ageLabel(A)}` : "❌ Не вдалося запустити бота"),
    ]);
    return;
  }

  if (act === "go") {
    const preview = V !== "0";
    const ok = await dispatch(env, {
      type: "run", page: PAGE_VAL[P], mode: MODE_VAL[M], preview: PV_VAL[V], age: ageValue(A), limit: preview ? "" : limValue(L),
    });
    const what = preview ? `перегляд ${PV_VAL[V]} (нічого не публікується)` : MODE_VAL[M];
    await Promise.all([
      answer(ok ? "⏳ Запускаю…" : "❌ Не вдалося запустити"),
      edit(
        ok ? `▶️ Запущено\nСторінка: ${PAGE_LABEL[P]}\nРежим: ${what}\nВік коментарів: ${ageLabel(A)}\nСкільки обробити: ${preview ? "—" : limLabel(L)}\n\nЗупинити можна кнопкою нижче або /stop.`
           : "❌ Не вдалося запустити бота",
        ok ? STOP_KB : undefined
      ),
    ]);
    return;
  }
  await Promise.all([answer(), edit(mainText(M, P, A, L), mainKb(P, M, V, A, L))]); // вибір: перемальовуємо меню
}

// відповідь людини числом на запит «Скільки днів / штук?»
async function customReply(env, msg, chatId) {
  const m = /#m:([ans][da][015]):([a\d]+):([a\d]+):(\d+):([al])/.exec(msg.reply_to_message?.text || "");
  if (!m) return false;
  const [, st, age0, lim0, mid, kind] = m;
  const n = Number((msg.text || "").trim());
  const max = kind === "a" ? AGE_MAX : LIM_MAX;
  if (!Number.isInteger(n) || n < 1 || n > max) {
    await tg(env, "sendMessage", { chat_id: chatId, text: `Потрібне ціле число від 1 до ${max}. Натисніть «✏️» ще раз.` });
    return true;
  }
  const [P, M, V, A, L] = parse(st, kind === "a" ? String(n) : age0, kind === "l" ? String(n) : lim0);
  await Promise.all([
    tg(env, "editMessageText", { chat_id: chatId, message_id: Number(mid), text: mainText(M, P, A, L), reply_markup: mainKb(P, M, V, A, L) }),
    tg(env, "deleteMessage", { chat_id: chatId, message_id: msg.reply_to_message.message_id }), // прибираємо запит і відповідь
    tg(env, "deleteMessage", { chat_id: chatId, message_id: msg.message_id }),
  ]);
  return true;
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET)
      return new Response("forbidden", { status: 403 });

    let u;
    try { u = await request.json(); } catch { return new Response("ok"); }

    const cb = u.callback_query;
    const msg = u.message;
    const chatId = String((cb ? cb.message?.chat?.id : msg?.chat?.id) ?? "");
    if (chatId !== String(env.TG_CHAT_ID)) return new Response("ok"); // чужий чат — ігноруємо

    if (cb) {
      if (/^ms?:/.test(String(cb.data))) { // кнопки меню — обробляє сам Worker
        await menuCallback(env, cb, chatId);
        return new Response("ok");
      }
      const ok = await dispatch(env, { type: "callback", data: cb.data, cb_id: cb.id, message_id: cb.message.message_id, text: cb.message.text || "" });
      const jobs = [tg(env, "answerCallbackQuery", {
        callback_query_id: cb.id,
        text: ok ? "⏳ Прийнято, обробляю…" : "❌ Не вдалося запустити бота",
      })];
      if (ok && !String(cb.data).startsWith("auto_")) {  // миттєво прибираємо кнопки; фінальний текст поставить бот
        const rest = (cb.message.text || "").split("\n").slice(1).join("\n");
        jobs.push(tg(env, "editMessageText", {
          chat_id: chatId, message_id: cb.message.message_id,
          text: "⏳ ОБРОБЛЯЮ… (до 20 с)\n" + rest,
        }));
      }
      await Promise.all(jobs);
    } else if (msg?.reply_to_message && msg.text && await customReply(env, msg, chatId)) {
      // це було «своє число» — оброблено
    } else if (msg?.text) {
      const cmd = msg.text.trim().split(/[ @]/)[0];
      if (["/run", "/menu", "/start"].includes(cmd)) await showMenu(env, chatId);
      else if (cmd === "/stop") await showStopConfirm(env, chatId);
    }
    return new Response("ok");
  },
};
