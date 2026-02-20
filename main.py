import logging
import re
import asyncio
import signal
import io
from datetime import datetime, date, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

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
completed_users = set()  # test topshirgan foydalanuvchilar

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
    "💰 Har sotuvdan 3% 4% 5% gacha daromad"
)

EXCEL_HEADERS = [
    "timestamp",
    "status",
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
    if not EXCEL_PATH.exists():
        wb = Workbook()
        ws = wb.active
        ws.title = "DATA"
        ws.append(EXCEL_HEADERS)
        
        # Stil qo'llash
        for col in range(1, len(EXCEL_HEADERS) + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center")
        
        wb.save(EXCEL_PATH)

def excel_append_row(row: list):
    try:
        ensure_excel_file()
        wb = load_workbook(EXCEL_PATH)
        ws = wb["DATA"] if "DATA" in wb.sheetnames else wb.active
        ws.append(row)
        wb.save(EXCEL_PATH)
        logger.info(f"Excelga yozildi: {row}")
        return True
    except Exception as e:
        logger.exception("Excel append error: %s", e)
        return False

def create_formatted_excel():
    """Admin uchun formatlangan Excel fayl yaratish"""
    wb = Workbook()
    ws = wb.active
    ws.title = "TEST NATIJALARI"
    
    # Sarlavhalar
    headers = [
        "№", "Sana", "Status", "Telegram ID", "Username", 
        "Ism", "Telefon", "Yosh", "Tajriba", "Hudud", 
        "Suhbat sanasi", "Closer ball", "Math ball", "IQ ball"
    ]
    ws.append(headers)
    
    # Sarlavha stillari
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
    
    # Ma'lumotlarni o'qish
    if EXCEL_PATH.exists():
        try:
            source_wb = load_workbook(EXCEL_PATH)
            source_ws = source_wb["DATA"] if "DATA" in source_wb.sheetnames else source_wb.active
            
            for row_idx, row in enumerate(source_ws.iter_rows(min_row=2, values_only=True), start=2):
                ws.append([row_idx - 1] + list(row))
        except Exception as e:
            logger.error(f"Excel o'qish xatosi: {e}")
    
    # Ustun kengliklari
    column_widths = [5, 18, 12, 15, 20, 25, 15, 8, 15, 20, 15, 12, 12, 10]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width
    
    # Chegaralar
    thin_border = Border(
        left=Side(style='thin'), 
        right=Side(style='thin'), 
        top=Side(style='thin'), 
        bottom=Side(style='thin')
    )
    
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=len(headers)):
        for cell in row:
            cell.border = thin_border
            if cell.row > 1:
                cell.alignment = Alignment(horizontal="left")
    
    # Faylni saqlash
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

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

def has_completed(uid: int) -> bool:
    return uid in completed_users

# ===================== TIMER =====================
def cancel_timer(user_data: dict):
    task = user_data.get("timer_task")
    if task and not task.done():
        task.cancel()
    user_data["timer_task"] = None

async def _safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
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
            try:
                if kind == "caption":
                    await context.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=base_text + f"\n\n⏳ Qoldi: {left}s",
                        reply_markup=reply_markup,
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=base_text + f"\n\n⏳ Qoldi: {left}s",
                        reply_markup=reply_markup,
                    )
            except Exception:
                pass
            await asyncio.sleep(TICK_SEC)
            left -= TICK_SEC

        # Time out
        if is_expired(context, uid):
            return

        set_expired(context, uid, True)
        lock_until_tomorrow(context, uid)

        # Delete old message
        await _safe_delete(context, chat_id, message_id)

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

async def send_math_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int, q_index: int, user_data: dict, old_message_id: int = None):
    # Oldingi xabarni o'chirish
    if old_message_id:
        await _safe_delete(context, chat_id, old_message_id)
    
    rm = math_keyboard(q_index)
    base_text = f"📌 Matematika test ({q_index+1}/10)\n\n{MATH_QUESTIONS[q_index]['text']}"
    msg = await context.bot.send_message(chat_id=chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    user_data["math_q"] = q_index
    user_data["last_message_id"] = msg.message_id
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

async def send_iq_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int, q_num: int, user_data: dict, old_message_id: int = None):
    # Oldingi xabarni o'chirish
    if old_message_id:
        await _safe_delete(context, chat_id, old_message_id)
    
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
        user_data["last_message_id"] = msg.message_id
        start_timer(context, user_data, uid, chat_id, msg.message_id, "caption", base_caption, rm)
    else:
        base_text = base_caption + f"\n\n⚠️ Rasm topilmadi: {q_num}sovol.(jpg/png/...)"
        msg = await context.bot.send_message(chat_id=chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
        user_data["iq_q"] = q_num
        user_data["last_message_id"] = msg.message_id
        start_timer(context, user_data, uid, chat_id, msg.message_id, "text", base_text, rm)

# ===================== START =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if has_completed(uid):
        await update.message.reply_text("❌ Siz allaqachon test topshirgansiz. Har bir foydalanuvchi faqat bir marta test topshirishi mumkin.")
        return ConversationHandler.END
    
    keyboard = [[InlineKeyboardButton("🚀 TESTNI BOSHLASH", callback_data="start_test")]]
    await update.message.reply_text(
        ABOUT_TEXT + "\n\n🚀 Testni boshlash uchun tugmani bosing:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END

# ===================== EXCEL COMMAND =====================
async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ Bu komandadan faqat admin foydalanishi mumkin.")
        return
    
    try:
        excel_file = create_formatted_excel()
        await update.message.reply_document(
            document=excel_file,
            filename=f"test_natijalari_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            caption="📊 Test natijalari"
        )
    except Exception as e:
        logger.error(f"Excel yaratish xatosi: {e}")
        await update.message.reply_text("❌ Excel fayl yaratishda xatolik yuz berdi.")

# ===================== CLOSER TEST =====================
async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if has_completed(uid):
        await query.edit_message_text("❌ Siz allaqachon test topshirgansiz. Har bir foydalanuvchi faqat bir marta test topshirishi mumkin.")
        return ConversationHandler.END

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
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
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
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
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
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
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
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
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
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
    return SAVOL5

async def savol5_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if guard_expired(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cancel_timer(context.user_data)

    if query.data == "s5_b":
        # Oldingi xabarni o'chirish
        await _safe_delete(context, query.message.chat_id, query.message.message_id)
        await context.bot.send_message(chat_id=query.message.chat_id, text="❌ Yosh mos emas.")
        return ConversationHandler.END

    # Agar yosh mos bo'lsa, davom etamiz
    rm = interview_date_keyboard()
    base_text = "❓ SAVOL 6/6\n\nSuhbat sanasini tanlang:"
    
    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    msg = await context.bot.send_message(chat_id=query.message.chat_id, text=base_text + f"\n\n⏳ Qoldi: {QUESTION_TIMEOUT_SEC}s", reply_markup=rm)
    context.user_data["last_message_id"] = msg.message_id
    start_timer(context, context.user_data, uid, query.message.chat_id, msg.message_id, "text", base_text, rm)
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

    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📊 Ball: {score}/18\n\nEndi Ism Familiyangizni kiriting:"
    )
    return ISM

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

    # Oldingi xabarni o'chirish
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✅ Ariza qabul qilindi!\n\n📌 Endi Matematika testi (10 ta savol) boshlanadi."
    )
    
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
        await send_math_question(context, query.message.chat_id, uid, q_index + 1, context.user_data, query.message.message_id)
        return MATH

    # Matematika testi tugadi
    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(chat_id=query.message.chat_id, text="✅ Matematika testi tugadi!\n\n🧠 Endi IQ test (15 ta savol) boshlanadi.")
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
        await send_iq_question(context, query.message.chat_id, uid, q_num + 1, context.user_data, query.message.message_id)
        return IQ

    # FINISH - barcha testlar tugadi
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

    # Test muvaffaqiyatli topshirildi
    completed_users.add(uid)

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
        logger.info(f"Admin xabari yuborildi: {ADMIN_ID}")
    except Exception as e:
        logger.exception(f"Admin send error: {e}")

    await _safe_delete(context, query.message.chat_id, query.message.message_id)
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✅ Test tugadi!\n Arizangiz qabul qilindi\n"
             "Tez orada siz bilan bog‘lanishligi mumkin."
    )
    
    # User ma'lumotlarini tozalaymiz
    user_scores.pop(uid, None)
    
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
    app.add_handler(CommandHandler("excel", excel_command))
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
