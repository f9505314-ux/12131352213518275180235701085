"""
KRMP Support Bot
Тех. поддержка, жалобы и заявки на администратора.
Python 3.10+, aiogram 3.7+
"""
import asyncio
import html
import json
import logging
import os
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
STAFF_CHAT_ID = int(os.getenv("STAFF_CHAT_ID", "0"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
PROJECT = os.getenv("PROJECT_NAME", "KRMP")
RULES_URL = os.getenv("RULES_URL", "")
DB_PATH = os.getenv("DB_PATH", "krmp_bot.db")

# Максимум одновременно открытых обращений от одного пользователя
OPEN_LIMIT = {"support": 3, "complaint": 3, "admin_app": 1}

BTN_SUPPORT = "🛠 Тех. поддержка"
BTN_COMPLAINT = "⚠️ Подать жалобу"
BTN_ADMIN = "👮 Заявка в админы"
BTN_MY = "📂 Мои обращения"
BTN_CANCEL = "❌ Отмена"

KINDS = {
    "support": "🛠 Тех. поддержка",
    "complaint": "⚠️ Жалоба",
    "admin_app": "👮 Заявка в админы",
}
STATUS = {
    "new": "🆕 Новая",
    "work": "🔄 В работе",
    "closed": "✅ Закрыто",
    "accepted": "✅ Принята",
    "rejected": "❌ Отклонена",
}
OPEN_STATUSES = ("new", "work")

# (подпись поля, вопрос пользователю, можно ли прикрепить фото)
FORMS = {
    "support": [
        ("Игровой ник", "Введите ваш игровой ник:", False),
        ("Проблема", "Опишите проблему максимально подробно. Можно прикрепить скриншот:", True),
    ],
    "complaint": [
        ("Ник заявителя", "Введите ваш игровой ник:", False),
        ("Ник нарушителя", "Введите ник игрока, на которого подаёте жалобу:", False),
        ("Дата и время", "Когда произошло нарушение? (дата, время, сервер)", False),
        ("Суть нарушения", "Опишите, что произошло:", False),
        ("Доказательства", "Отправьте ссылку на видео/скриншот (YouTube, Imgur и т.п.) или прикрепите фото:", True),
    ],
    "admin_app": [
        ("Игровой ник", "Введите ваш игровой ник:", False),
        ("Возраст", "Сколько вам лет?", False),
        ("Время на проекте", "Сколько времени вы играете на проекте {project}?", False),
        ("Опыт администрирования", "Был ли у вас опыт администрирования на других проектах? Расскажите.", False),
        ("Online в день", "Сколько часов в день вы готовы уделять проекту?", False),
        ("Мотивация", "Почему вы хотите стать администратором?", False),
    ],
}


# ───────────────────────── База данных ─────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS tickets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                data TEXT NOT NULL,
                photo TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                assignee TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        await db.commit()


async def get_ticket(tid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tickets WHERE id=?", (tid,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def count_open(user_id: int, kind: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM tickets WHERE user_id=? AND kind=? AND status IN ('new','work')",
            (user_id, kind),
        )
        return (await cur.fetchone())[0]


async def set_status(tid: int, status: str, assignee: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if assignee is not None:
            await db.execute("UPDATE tickets SET status=?, assignee=? WHERE id=?", (status, assignee, tid))
        else:
            await db.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid))
        await db.commit()


# ───────────────────────── Оформление ─────────────────────────
def esc(s) -> str:
    return html.escape(str(s))


def render(t: dict) -> str:
    data = json.loads(t["data"])
    user = f'<a href="tg://user?id={t["user_id"]}">{esc(t["full_name"] or "Пользователь")}</a>'
    if t["username"]:
        user += f' (@{esc(t["username"])})'
    lines = [
        f'<b>{KINDS[t["kind"]]} #{t["id"]}</b>',
        f'Статус: {STATUS[t["status"]]}',
        f"От: {user}",
        f'ID: <code>{t["user_id"]}</code>',
    ]
    if t.get("assignee"):
        lines.append(f'Ответственный: {esc(t["assignee"])}')
    lines.append("")
    for k, v in data.items():
        lines.append(f"<b>{esc(k)}:</b> {esc(v)}")
    return "\n".join(lines)


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SUPPORT)],
            [KeyboardButton(text=BTN_COMPLAINT), KeyboardButton(text=BTN_ADMIN)],
            [KeyboardButton(text=BTN_MY)],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)


def staff_kb(t: dict) -> InlineKeyboardMarkup | None:
    tid, st = t["id"], t["status"]
    if st not in OPEN_STATUSES:
        return None
    rows = []
    if t["kind"] == "admin_app":
        rows.append(
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"t:accept:{tid}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"t:reject:{tid}"),
            ]
        )
        rows.append([InlineKeyboardButton(text="✍️ Написать", callback_data=f"t:reply:{tid}")])
    else:
        first = []
        if st == "new":
            first.append(InlineKeyboardButton(text="🔄 Взять", callback_data=f"t:take:{tid}"))
        first.append(InlineKeyboardButton(text="✍️ Ответить", callback_data=f"t:reply:{tid}"))
        rows.append(first)
        rows.append([InlineKeyboardButton(text="✅ Закрыть", callback_data=f"t:close:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_reply_kb(tid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"u:reply:{tid}")]]
    )


def staff_name(u) -> str:
    return f"@{u.username}" if u.username else u.full_name


# ───────────────────────── FSM ─────────────────────────
class Form(StatesGroup):
    filling = State()


class UserReply(StatesGroup):
    text = State()


class StaffReply(StatesGroup):
    text = State()


common = Router()
staff = Router()
user = Router()
user.message.filter(F.chat.type == "private")
user.callback_query.filter(F.message.chat.type == "private")


# ───────────────────────── Общие команды ─────────────────────────
@common.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Chat ID: <code>{m.chat.id}</code>\nВаш ID: <code>{m.from_user.id}</code>")


@common.message(Command("cancel"))
@common.message(F.text == BTN_CANCEL)
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    if m.chat.type == "private":
        await m.answer("Действие отменено.", reply_markup=main_kb())
    else:
        await m.answer("Отменено.")


# ───────────────────────── Пользователь ─────────────────────────
@user.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    text = (
        f"👋 Добро пожаловать в бот проекта <b>{esc(PROJECT)}</b>!\n\n"
        "Здесь вы можете:\n"
        "• обратиться в тех. поддержку;\n"
        "• подать жалобу на игрока;\n"
        "• подать заявку на должность администратора.\n\n"
        "Выберите действие в меню ниже."
    )
    if RULES_URL:
        text += f'\n\n📜 <a href="{esc(RULES_URL)}">Правила проекта</a>'
    await m.answer(text, reply_markup=main_kb(), disable_web_page_preview=True)


async def start_form(m: Message, state: FSMContext, kind: str):
    if await count_open(m.from_user.id, kind) >= OPEN_LIMIT[kind]:
        await m.answer(
            "У вас уже есть открытые обращения этого типа. Дождитесь ответа или проверьте «📂 Мои обращения».",
            reply_markup=main_kb(),
        )
        return
    await state.clear()
    await state.set_state(Form.filling)
    await state.update_data(kind=kind, idx=0, answers={}, photo=None)
    await m.answer(f"<b>{KINDS[kind]}</b>\nЗаполните анкету. Для выхода нажмите «{BTN_CANCEL}».", reply_markup=cancel_kb())
    await ask(m, kind, 0)


async def ask(m: Message, kind: str, idx: int):
    steps = FORMS[kind]
    q = steps[idx][1].format(project=PROJECT)
    await m.answer(f"<b>[{idx + 1}/{len(steps)}]</b> {esc(q)}")


@user.message(F.text == BTN_SUPPORT)
async def b_support(m: Message, state: FSMContext):
    await start_form(m, state, "support")


@user.message(F.text == BTN_COMPLAINT)
async def b_complaint(m: Message, state: FSMContext):
    await start_form(m, state, "complaint")


@user.message(F.text == BTN_ADMIN)
async def b_admin(m: Message, state: FSMContext):
    await start_form(m, state, "admin_app")


@user.message(Form.filling)
async def fill(m: Message, state: FSMContext):
    d = await state.get_data()
    kind, idx = d["kind"], d["idx"]
    steps = FORMS[kind]
    label, _, photo_ok = steps[idx]
    photo = d.get("photo")

    if m.photo and photo_ok:
        photo = m.photo[-1].file_id
        text = (m.caption or "").strip() or "📷 (фото во вложении)"
    elif m.text:
        text = m.text.strip()
    else:
        await m.answer("Пожалуйста, отправьте текстовый ответ.")
        return
    if len(text) > 1000:
        await m.answer("Слишком длинно. Сократите ответ до 1000 символов.")
        return

    answers = d["answers"]
    answers[label] = text
    idx += 1
    if idx < len(steps):
        await state.update_data(idx=idx, answers=answers, photo=photo)
        await ask(m, kind, idx)
        return

    await state.clear()
    tid = await submit(m, kind, answers, photo)
    await m.answer(
        f"✅ Ваше обращение <b>#{tid}</b> принято. Ответ придёт в этот чат.",
        reply_markup=main_kb(),
    )


async def submit(m: Message, kind: str, answers: dict, photo: str | None) -> int:
    u = m.from_user
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tickets(kind,user_id,username,full_name,data,photo,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (kind, u.id, u.username, u.full_name, json.dumps(answers, ensure_ascii=False), photo, "new",
             datetime.now().isoformat(timespec="seconds")),
        )
        await db.commit()
        tid = cur.lastrowid
    t = await get_ticket(tid)
    msg = await m.bot.send_message(STAFF_CHAT_ID, render(t), reply_markup=staff_kb(t))
    if photo:
        await m.bot.send_photo(STAFF_CHAT_ID, photo, reply_to_message_id=msg.message_id)
    return tid


@user.message(F.text == BTN_MY)
async def my_tickets(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, kind, status, created_at FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (m.from_user.id,),
        )
        rows = await cur.fetchall()
    if not rows:
        await m.answer("У вас пока нет обращений.")
        return
    lines = ["<b>Ваши последние обращения:</b>\n"]
    for r in rows:
        lines.append(f'#{r["id"]} — {KINDS[r["kind"]]} — {STATUS[r["status"]]} ({r["created_at"][:10]})')
    await m.answer("\n".join(lines))


# Пользователь отвечает на сообщение персонала
@user.callback_query(F.data.startswith("u:reply:"))
async def u_reply(cb: CallbackQuery, state: FSMContext):
    tid = int(cb.data.split(":")[2])
    t = await get_ticket(tid)
    if not t or t["user_id"] != cb.from_user.id:
        await cb.answer("Обращение не найдено", show_alert=True)
        return
    if t["status"] not in OPEN_STATUSES:
        await cb.answer("Обращение уже закрыто", show_alert=True)
        return
    await state.set_state(UserReply.text)
    await state.update_data(tid=tid)
    await cb.message.answer(f"Напишите сообщение по обращению #{tid}:", reply_markup=cancel_kb())
    await cb.answer()


@user.message(UserReply.text, F.text)
async def u_reply_text(m: Message, state: FSMContext):
    d = await state.get_data()
    tid = d["tid"]
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✍️ Ответить", callback_data=f"t:reply:{tid}")]]
    )
    await m.bot.send_message(
        STAFF_CHAT_ID,
        f"💬 <b>Сообщение от пользователя по #{tid}</b>\n"
        f'<a href="tg://user?id={m.from_user.id}">{esc(m.from_user.full_name)}</a>:\n\n{esc(m.text)}',
        reply_markup=kb,
    )
    await m.answer("✅ Сообщение отправлено.", reply_markup=main_kb())


# ───────────────────────── Персонал ─────────────────────────
@staff.callback_query(F.data.startswith("t:"))
async def staff_action(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("У вас нет доступа.", show_alert=True)
        return
    _, action, tid = cb.data.split(":")
    tid = int(tid)
    t = await get_ticket(tid)
    if not t:
        await cb.answer("Обращение не найдено", show_alert=True)
        return

    if action == "reply":
        await state.set_state(StaffReply.text)
        await state.update_data(tid=tid)
        await cb.message.answer(
            f"✍️ {esc(staff_name(cb.from_user))}, отправьте ответ по обращению #{tid} одним сообщением "
            f"(в группе — ответом на это сообщение). Отмена: /cancel"
        )
        await cb.answer()
        return

    if t["status"] not in OPEN_STATUSES:
        await cb.answer("Обращение уже обработано.", show_alert=True)
        return

    who = staff_name(cb.from_user)
    uid = t["user_id"]
    if action == "take":
        await set_status(tid, "work", who)
        note = f"🔄 Ваше обращение #{tid} взято в работу."
    elif action == "close":
        await set_status(tid, "closed", who)
        note = f"✅ Ваше обращение #{tid} закрыто. Спасибо за обращение!"
    elif action == "accept":
        await set_status(tid, "accepted", who)
        note = f"🎉 Ваша заявка #{tid} на администратора <b>принята</b>! С вами свяжутся."
    elif action == "reject":
        await set_status(tid, "rejected", who)
        note = f"К сожалению, ваша заявка #{tid} на администратора <b>отклонена</b>."
    else:
        await cb.answer()
        return

    t = await get_ticket(tid)
    await cb.message.edit_text(render(t), reply_markup=staff_kb(t))
    try:
        await cb.bot.send_message(uid, note)
    except Exception:
        await cb.message.answer("⚠️ Не удалось уведомить пользователя (возможно, он заблокировал бота).")
    await cb.answer("Готово")


@staff.message(StaffReply.text, F.text)
async def staff_reply_text(m: Message, state: FSMContext):
    if m.from_user.id not in ADMIN_IDS:
        return
    d = await state.get_data()
    tid = d["tid"]
    t = await get_ticket(tid)
    await state.clear()
    if not t:
        await m.answer("Обращение не найдено.")
        return
    try:
        await m.bot.send_message(
            t["user_id"],
            f"💬 <b>Ответ по обращению #{tid}</b>\n\n{esc(m.text)}\n\n— {esc(PROJECT)} Support",
            reply_markup=user_reply_kb(tid) if t["status"] in OPEN_STATUSES else None,
        )
        await m.answer(f"✅ Ответ по #{tid} отправлен.")
    except Exception:
        await m.answer("⚠️ Не удалось отправить сообщение пользователю.")


# ───────────────────────── Запуск ─────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN or not STAFF_CHAT_ID or not ADMIN_IDS:
        raise SystemExit("Заполните BOT_TOKEN, STAFF_CHAT_ID и ADMIN_IDS в файле .env")
    await init_db()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(common, staff, user)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
