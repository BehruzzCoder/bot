import logging
import re
import asyncio
import signal
from datetime import datetime, date, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ===================== SETTINGS =====================
BOT_TOKEN = "8302225372:AAE1aLrBi15j066O5eAWPyIb-PkRa_Zw_vQ"
ADMIN_ID = 8013467870

QUESTION_TIMEOUT_SEC = 60   # har savolga 60 sekund
TICK_SEC = 5                # har 5 sekundda timer update (edit)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXCEL_PATH = DATA_DIR / "applications.xlsx"

IQ_IMAGE_DIR = BASE_DIR / "iq_images"   # 1sovol.jpg ... 15sovol.jpg

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===================== STATES =====================
(SAVOL1, SAVOL2, SAVOL3, SAVOL4, SAVOL5, SAVOL6,
 ISM, TEL, YOSH, TAJRIBA, HUDUD,
 MATH, IQ) = range(13)

# ===================== GLOBALS =====================
user_scores = {}  # closer score (0..18)

REGIONS = [
    "Qoraqalpog‘iston R.",
    "Andijon",
    "Buxoro",
    "Farg‘ona",
    "Jizzax",
    "Xorazm",
    "Namangan",
    "Navoiy",
    "Qashqadaryo",
    "Samarqand",
    "Sirdaryo",
    "Surxondaryo",
    "Toshkent viloyati",
    "Toshkent shahri",
]

ABOUT_TEXT = (
    "🔥 BUYUK ZAMON — Sotuv bo‘limi\n\n"
    "💼 High-Ticket Closer testi\n"
    "⏰ 10:00–18:00 | 🍽 13:00–14:00 (o‘z hisobidan)\n"
    "📆 Haftada 6 kun | 🎯 19–38 yosh\n"
    "💰 Har sotuvdan 5% daromad"
)

# ===================== EXCEL =====================
EXCEL_HEADERS = [
    "timestamp",
    "status",               # COMPLETED / TIMEOUT
    "tg_user_id",
    "tg_username",
    "ism",
    "tel",
    "yosh",
    "tajriba",
    "hudud",
    "interview_date",
    "closer_score",
    "math_score",
    "iq_score",
]

def ensure_excel_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if EXCEL_PATH.exists():
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "DATA"
    ws.append(EXCEL_HEADERS)
    wb.save(EXCEL_PATH)

def excel_append_row(row: list):
    try:
        ensure_excel_file()
        wb = load_workbook(EXCEL_PATH)
        ws = wb["DATA"] if "DATA" in wb.sheetnames else wb.active
        ws.append(row)
        wb.save(EXCEL_PATH)
        return True
    except Exception as e:
        logger.exception("Excel append error: %s", e)
        return False

# ===================== LOCK / EXPIRE =====================
def _locks(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("locks", {})

def _expired(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("expired", {})

def is_locked(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    lock_until = _locks(context).get(uid)
    return bool(lock_until and date.today() < lock_until)

def lock_until_tomorrow(context: ContextTypes.DEFAULT_TYPE, uid: int):
    _locks(context)[uid] = date.today() + timedelta(days=1)

def set_expired(context: ContextTypes.DEFAULT_TYPE, uid: int, val: bool):
    _expired(context)[uid] = val

def is_expired(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    return bool(_expired(context).get(uid, False))

def guard_expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return True
    return is_expired(context, uid)

# ===================== TIMER =====================
def cancel_timer(user_data: dict):
    task = user_data.get("timer_task")
    if task and not task.done():
        task.cancel()
    user_data["timer_task"] = None

async def _safe_edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, kind: str, text: str, reply_markup):
    try:
        if kind == "caption":
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=reply_markup,
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
    except Exception:
        pass

async def timer_task_fn(
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    chat_id: int,
    message_id: int,
    kind: str,
    base_text: str,
    reply_markup,
):
    left = QUESTION_TIMEOUT_SEC
    try:
        while left > 0:
            await _safe_edit(
                context,
                chat_id,
                message_id,
                kind,
                base_text + f"\n\n⏳ Qoldi: {left}s",
                reply_markup,
            )
            await asyncio.sleep(TICK_SEC)
            left -= TICK_SEC

        # Time out
        if is_expired(context, uid):
            return

        set_expired(context, uid, True)
        lock_until_tomorrow(context, uid)

        # Buttons off
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass

        # Save TIMEOUT to Excel
        ud = context.application.user_data.get(uid, {}) if hasattr(context.application, "user_data") else {}
        closer_score = user_scores.get(uid, 0)
        math_score = int(ud.get("math_score", 0) or 0)
        iq_score = int(ud.get("iq_score", 0) or 0)

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "TIMEOUT",
            str(uid),
            ud.get("username", ""),
            ud.get("ism", ""),
            ud.get("tel", ""),
            ud.get("yosh", ""),
            ud.get("tajriba", ""),
            ud.get("hudud", ""),
            ud.get("interview_date", ""),
            f"{closer_score}/18",
            f"{math_score}/10",
            f"{iq_score}/15",
        ]
        excel_append_row(row)

        await context.bot.send_message(
            chat_id=chat_id,
            text="⏰ Vaqt tugadi! Test bekor qilindi.\n\nErtaga yana urinib ko‘ring. /start"
        )

    except asyncio.CancelledError:
        return

def start_timer(context: ContextTypes.DEFAULT_TYPE, user_data: dict, uid: int, chat_id: int, message_id: int, kind: str, base_text: str, reply_markup):
    cancel_timer(user_data)
    user_data["timer_task"] = asyncio.create_task(
        timer_task_fn(context, uid, chat_id, message_id, kind, base_text, reply_markup)
    )

# ===================== HELPERS =====================
def build_regions_keyboard():
    rows, row = [], []
    for r in REGIONS:
        row.append(InlineKeyboardButton(r, callback_data=f"reg:{r}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def normalize_uz_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text.strip())
    if digits.startswith("998") and len(digits) == 12:
        return "+" + digits
    if len(digits) == 9:
        return "+998" + digits
    return None

def interview_date_keyboard():
    # keyingi 3 kun
    d1 = date.today() + timedelta(days=1)
    d2 = date.today() + timedelta(days=2)
    d3 = date.today() + timedelta(days=3)
    rows = [
        [InlineKeyboardButton(d1.strftime("%d.%m.%Y"), callback_data=f"int:{d1.isoformat()}")],
        [InlineKeyboardButton(d2.strftime("%d.%m.%Y"), callback_data=f"int:{d2.isoformat()}")],
        [InlineKeyboardButton(d3.strftime("%d.%m.%Y"), callback_data=f"int:{d3.isoformat()}")],
    ]
    return InlineKeyboardMarkup(rows)

# ===================== QUESTIONS =====================
MATH_QUESTIONS = [
    {
        "text": "1) Bir yuk mashinasi birinchi 3 soat 70 km/soat, keyin 2 soat 90 km/soat. Jami masofa?",
        "options": [("A) 400 km", "a"), ("B) 360 km", "b"), ("C) 370 km", "c"), ("D) 390 km", "d")],
        "correct": "d",
    },
    {
        "text": "2) 80 mln so‘m kapital: 30% savdo, 40% ishlab chiqarish. Qolgan qismi boshqa loyiha. Qancha?",
        "options": [("A) 19 mln", "a"), ("B) 22 mln", "b"), ("C) 24 mln", "c"), ("D) 27 mln", "d")],
        "correct": "c",
    },
    {
        "text": "3) Mehmonxona 1 kecha 250,000 so‘m. 7 kun + 10% chegirma. Jami?",
        "options": [("A) 1,560,000", "a"), ("B) 1,575,000", "b"), ("C) 1,610,000", "c"), ("D) 1,728,000", "d")],
        "correct": "b",
    },
    {
        "text": "4) Uy 5 yil oldin 50 mln. Har yili 7% oshgan. Hozir taxminan?",
        "options": [("A) 75 mln", "a"), ("B) 65 mln", "b"), ("C) 70 mln", "c"), ("D) 72 mln", "d")],
        "correct": "c",
    },
    {
        "text": "5) Shahar aholisi 1,200,000. Har yili 5% ortadi. 3 yildan keyin taxminan?",
        "options": [("A) 1,300,138", "a"), ("B) 1,375,145", "b"), ("C) 1,389,150", "c"), ("D) 1,453,050", "d")],
        "correct": "c",
    },
    {
        "text": "6) Mashina 2 soat 60 km/soat, keyin 3 soat 80 km/soat. O‘rtacha tezlik?",
        "options": [("A) 68", "a"), ("B) 70", "b"), ("C) 72", "c"), ("D) 78", "d")],
        "correct": "c",
    },
    {
        "text": "7) 3 haftada 1,800 mahsulot. Haftasiga 600. Keyingi haftadan boshlab kuniga 10% ko‘proq. 4-haftada nechta?",
        "options": [("A) 750", "a"), ("B) 660", "b"), ("C) 700", "c"), ("D) 720", "d")],
        "correct": "b",
    },
    {
        "text": "8) 2 liniya: 1-liniya 3 soatda 120 ta; 2-liniya 2 soatda 90 ta. Ikkalasi 10 soat ishlasa jami?",
        "options": [("A) 800", "a"), ("B) 850", "b"), ("C) 900", "c"), ("D) 950", "d")],
        "correct": "b",
    },
    {
        "text": "9) 25 mln mahsulotning 40% eksport. Eksport qismi 20% foyda keltirsa jami daromad?",
        "options": [("A) 26 mln", "a"), ("B) 27 mln", "b"), ("C) 28 mln", "c"), ("D) 29 mln", "d")],
        "correct": "b",
    },
    {
        "text": "10) Odam yoshi x. 6 yil oldin yoshi hozirgisining 1/3 qismiga teng edi. Hozir yoshi?",
        "options": [("A) 18", "a"), ("B) 11", "b"), ("C) 9", "c"), ("D) 36", "d")],
        "correct": "a",
    },
]

def math_keyboard(q_index: int) -> InlineKeyboardMarkup:
    opts = MATH_QUESTIONS[q_index]["options"]
    rows = [[InlineKeyboardButton(text, callback_data=f"m{q_index+1}_{key}")] for (text, key) in opts]
    return InlineKeyboardMarkup(rows)

async def send_math_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int, q_index: int, user_data: dict):
    rm = math_keyboard(q_index)
    base_text = f"📌 Matematika test ({q_index+1}/10)\n\n{MATH_QUESTIONS[q_index]['text']}"
    msg = await context.bot.send_message(chat_id=chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    user_data["math_q"] = q_index
    start_timer(context, user_data, uid, chat_id, msg.message_id, "text", base_text, rm)

# ---------- IQ ----------
IQ_TOTAL = 15
IQ_CORRECT = {
    1: 6, 2: 4, 3: 4, 4: 3, 5: 6,
    6: 5, 7: 2, 8: 4, 9: 5, 10: 2,
    11: 4, 12: 3, 13: 1, 14: 4, 15: 2,
}
IQ_OPTIONS_COUNT = [6] * IQ_TOTAL

def find_iq_image_path(q_num: int) -> Path | None:
    base = f"{q_num}sovol"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = IQ_IMAGE_DIR / f"{base}{ext}"
        if p.exists():
            return p
    return None

def iq_keyboard(q_num: int) -> InlineKeyboardMarkup:
    count = IQ_OPTIONS_COUNT[q_num - 1]
    rows, row = [], []
    for opt in range(1, count + 1):
        row.append(InlineKeyboardButton(str(opt), callback_data=f"iq{q_num}_{opt}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def send_iq_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int, q_num: int, user_data: dict):
    rm = iq_keyboard(q_num)
    base_caption = f"🧠 IQ TEST ({q_num}/{IQ_TOTAL})\n\nVariantni tanlang:"
    img_path = find_iq_image_path(q_num)

    if img_path:
        with open(img_path, "rb") as f:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=base_caption + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s",
                reply_markup=rm,
            )
        user_data["iq_q"] = q_num
        start_timer(context, user_data, uid, chat_id, msg.message_id, "caption", base_caption, rm)
    else:
        base_text = base_caption + f"\n\n⚠️ Rasm topilmadi: {q_num}sovol.(jpg/png/...)"
        msg = await context.bot.send_message(chat_id=chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
        user_data["iq_q"] = q_num
        start_timer(context, user_data, uid, chat_id, msg.message_id, "text", base_text, rm)

# ===================== START =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🚀 TESTNI BOSHLASH", callback_data="start_test")]]
    await update.message.reply_text(
        ABOUT_TEXT + "\n\n🚀 Testni boshlash uchun tugmani bosing:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END

# ===================== CLOSER TEST =====================
async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if is_locked(context, uid):
        await query.edit_message_text("⛔️ Bugun urinish tugagan.\nErtaga yana urinib ko‘ring. /start")
        return ConversationHandler.END

    # reset user session
    context.user_data.clear()
    context.user_data["username"] = (query.from_user.username or "")
    context.user_data["math_score"] = 0
    context.user_data["iq_score"] = 0
    user_scores[uid] = 0
    cancel_timer(context.user_data)
    set_expired(context, uid, False)

    keyboard = [
        [InlineKeyboardButton("Ha, katta cheklar yopganman", callback_data="s1_a")],
        [InlineKeyboardButton("Kichik cheklar bilan", callback_data="s1_b")],
        [InlineKeyboardButton("O‘rganishga tayyorman", callback_data="s1_c")],
    ]
    rm = InlineKeyboardMarkup(keyboard)
    base_text = "❓ SAVOL 1/6\n\nSotuv qila olasizmi?"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, uid, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL1

async def savol1_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if query.data == "s1_a":
        user_scores[uid] += 3
    elif query.data == "s1_b":
        user_scores[uid] += 2
    elif query.data == "s1_c":
        user_scores[uid] += 1

    keyboard = [
        [InlineKeyboardButton("Nima to‘xtatmoqda?", callback_data="s2_b")],
        [InlineKeyboardButton("Yana urinaman", callback_data="s2_d")],
        [InlineKeyboardButton("Qachon qo‘ng‘iroq qilay?", callback_data="s2_a")],
        [InlineKeyboardButton("Chegirma qilaman", callback_data="s2_c")],
    ]
    rm = InlineKeyboardMarkup(keyboard)
    base_text = "❓ SAVOL 2/6\n\nMijoz “o‘ylab ko‘raman” desa?"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, uid, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL2

async def savol2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if query.data == "s2_b":
        user_scores[uid] += 3
    elif query.data == "s2_d":
        user_scores[uid] += 2
    elif query.data == "s2_a":
        user_scores[uid] += 1

    keyboard = [
        [InlineKeyboardButton("10+ sotuv", callback_data="s3_a")],
        [InlineKeyboardButton("5–10 sotuv", callback_data="s3_b")],
        [InlineKeyboardButton("Hali yo‘q", callback_data="s3_c")],
    ]
    rm = InlineKeyboardMarkup(keyboard)
    base_text = "❓ SAVOL 3/6\n\nOyiga nechta sotuv qilasiz?"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, uid, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL3

async def savol3_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if query.data == "s3_a":
        user_scores[uid] += 3
    elif query.data == "s3_b":
        user_scores[uid] += 2

    keyboard = [
        [InlineKeyboardButton("Ha", callback_data="s4_a")],
        [InlineKeyboardButton("Yo‘q", callback_data="s4_b")],
    ]
    rm = InlineKeyboardMarkup(keyboard)
    base_text = "❓ SAVOL 4/6\n\n10:00–18:00 ish vaqti mosmi?"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, uid, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL4

async def savol4_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if query.data == "s4_a":
        user_scores[uid] += 3
    else:
        user_scores[uid] -= 5

    keyboard = [
        [InlineKeyboardButton("Ha", callback_data="s5_a")],
        [InlineKeyboardButton("Yo‘q", callback_data="s5_b")],
    ]
    rm = InlineKeyboardMarkup(keyboard)
    base_text = "❓ SAVOL 5/6\n\n19–38 yosh oralig‘idamisiz?"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, uid, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL5

async def savol5_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    cancel_timer(context.user_data)

    if query.data == "s5_b":
        await query.edit_message_text("❌ Yosh mos emas.")
        return ConversationHandler.END

    rm = interview_date_keyboard()
    base_text = "❓ SAVOL 6/6\n\nSuhbat sanasini tanlang:"
    await query.edit_message_text(base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    start_timer(context, context.user_data, query.from_user.id, query.message.chat_id, query.message.message_id, "text", base_text, rm)
    return SAVOL6

async def savol6_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if not query.data.startswith("int:"):
        return SAVOL6

    iso = query.data.split("int:", 1)[1].strip()
    try:
        d = date.fromisoformat(iso)
        context.user_data["interview_date"] = d.strftime("%d.%m.%Y")
    except Exception:
        context.user_data["interview_date"] = ""

    user_scores[uid] += 3
    score = user_scores.get(uid, 0)

    if score >= 12:
        await query.edit_message_text(f"🎉 Tabriklaymiz!\n\nBall: {score}/18\n\nIsm Familiyangizni kiriting:")
        return ISM
    elif score >= 8:
        await query.edit_message_text(f"Ball: {score}/18\n\nTelefon raqamingizni kiriting:")
        return TEL
    else:
        await query.edit_message_text(f"❌ Hozircha mos emas.\n\nBall: {score}/18")
        return ConversationHandler.END

# ===================== FORM =====================
async def get_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    context.user_data["ism"] = update.message.text.strip()
    await update.message.reply_text("📞 Telefon raqamingizni kiriting:\nMisol: +998901234567 yoki 901234567")
    return TEL

async def get_tel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    phone = normalize_uz_phone(update.message.text)
    if not phone:
        await update.message.reply_text("❌ Telefon formati xato.\nMisol: +998901234567 yoki 901234567\nQayta yuboring:")
        return TEL
    context.user_data["tel"] = phone
    await update.message.reply_text("🎂 Yoshingizni kiriting (masalan: 23):")
    return YOSH

async def get_yosh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    yosh_txt = update.message.text.strip()
    if not yosh_txt.isdigit():
        await update.message.reply_text("❌ Faqat raqam kiriting. Masalan: 23")
        return YOSH
    context.user_data["yosh"] = yosh_txt
    await update.message.reply_text("💼 Ish tajribangizni yozing (masalan: 0, 6 oy, 1 yil, 2 yil):")
    return TAJRIBA

async def get_tajriba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    context.user_data["tajriba"] = update.message.text.strip()
    await update.message.reply_text("📍 Hududingizni tanlang:", reply_markup=build_regions_keyboard())
    return HUDUD

async def get_hudud_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if not query.data.startswith("reg:"):
        return HUDUD

    region = query.data.split("reg:", 1)[1].strip()
    context.user_data["hudud"] = region

    await query.edit_message_text("✅ Ariza qabul qilindi!\n\n📌 Endi Matematika testi (10 ta savol) boshlanadi.")
    await send_math_question(context, query.message.chat_id, uid, 0, context.user_data)
    return MATH

# ===================== MATH HANDLER =====================
async def math_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    m = re.match(r"^m(\d+)_(a|b|c|d)$", query.data.strip())
    if not m:
        return MATH

    q_num = int(m.group(1))      # 1..10
    choice = m.group(2)
    q_index = q_num - 1

    if 0 <= q_index < 10 and choice == MATH_QUESTIONS[q_index]["correct"]:
        context.user_data["math_score"] = int(context.user_data.get("math_score", 0)) + 1

    if q_num < 10:
        await send_math_question(context, query.message.chat_id, uid, q_index + 1, context.user_data)
        return MATH

    await query.message.chat.send_message("✅ Matematika testi tugadi!\n\n🧠 Endi IQ test (15 ta savol) boshlanadi.")
    await send_iq_question(context, query.message.chat_id, uid, 1, context.user_data)
    return IQ

# ===================== IQ HANDLER =====================
async def iq_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    m = re.match(r"^iq(\d+)_(\d+)$", query.data.strip())
    if not m:
        return IQ

    q_num = int(m.group(1))      # 1..15
    choice = int(m.group(2))

    if IQ_CORRECT.get(q_num) == choice:
        context.user_data["iq_score"] = int(context.user_data.get("iq_score", 0)) + 1

    if q_num < IQ_TOTAL:
        await send_iq_question(context, query.message.chat_id, uid, q_num + 1, context.user_data)
        return IQ

    # FINISH
    closer_score = user_scores.get(uid, 0)
    math_score = int(context.user_data.get("math_score", 0))
    iq_score = int(context.user_data.get("iq_score", 0))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [
        now,
        "COMPLETED",
        str(uid),
        context.user_data.get("username", ""),
        context.user_data.get("ism", ""),
        context.user_data.get("tel", ""),
        context.user_data.get("yosh", ""),
        context.user_data.get("tajriba", ""),
        context.user_data.get("hudud", ""),
        context.user_data.get("interview_date", ""),
        f"{closer_score}/18",
        f"{math_score}/10",
        f"{iq_score}/15",
    ]
    ok = excel_append_row(row)

    admin_msg = (
        "🆕 YANGI ARIZA + TEST NATIJASI\n\n"
        f"⏱ {now}\n"
        f"👤 {context.user_data.get('ism','-')}\n"
        f"📞 {context.user_data.get('tel','-')}\n"
        f"🎂 {context.user_data.get('yosh','-')}\n"
        f"💼 Tajriba: {context.user_data.get('tajriba','-')}\n"
        f"📍 Hudud: {context.user_data.get('hudud','-')}\n"
        f"🗓 Suhbat sana: {context.user_data.get('interview_date','-')}\n"
        f"👤 Username: @{context.user_data.get('username','')}\n\n"
        f"⭐️ Closer: {closer_score}/18\n"
        f"📌 Matematika: {math_score}/10\n"
        f"🧠 IQ: {iq_score}/15\n"
        f"🆔 UserID: {uid}\n\n"
        f"📄 Excel: {'OK' if ok else 'ERROR'}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg)
    except Exception as e:
        logger.exception("Admin send error: %s", e)

    await query.message.chat.send_message(
        "✅ Test tugadi!\n\n"
        f"⭐️ Closer: {closer_score}/18\n"
        f"📌 Matematika: {math_score}/10\n"
        f"🧠 IQ: {iq_score}/15\n\n"
        "Tez orada siz bilan bog‘lanishadi."
    )
    return ConversationHandler.END

# ===================== CANCEL =====================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_timer(context.user_data)
    await update.message.reply_text("Bekor qilindi. /start bosib qaytadan boshlang.")
    return ConversationHandler.END

# ===================== RUN (PTB NEW VERSIONS) =====================
async def _wait_for_stop_signal():
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()

async def main():
    ensure_excel_file()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_test, pattern="^start_test$")],
        states={
            SAVOL1: [CallbackQueryHandler(savol1_handler, pattern="^s1_[a-c]$")],
            SAVOL2: [CallbackQueryHandler(savol2_handler, pattern="^s2_[a-d]$")],
            SAVOL3: [CallbackQueryHandler(savol3_handler, pattern="^s3_[a-c]$")],
            SAVOL4: [CallbackQueryHandler(savol4_handler, pattern="^s4_[ab]$")],
            SAVOL5: [CallbackQueryHandler(savol5_handler, pattern="^s5_[ab]$")],
            SAVOL6: [CallbackQueryHandler(savol6_handler, pattern="^int:")],

            ISM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ism)],
            TEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tel)],
            YOSH: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_yosh)],
            TAJRIBA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tajriba)],
            HUDUD: [CallbackQueryHandler(get_hudud_callback, pattern="^reg:")],

            MATH: [CallbackQueryHandler(math_answer_handler, pattern=r"^m\d+_[a-d]$")],
            IQ:   [CallbackQueryHandler(iq_answer_handler, pattern=r"^iq\d+_\d+$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await _wait_for_stop_signal()

    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
