import os
import sys
import logging
import asyncio
import aiohttp
import json
import time
import uuid
import signal
from datetime import datetime, timedelta, timezone
from telegram import Update, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram import __version__ as tg_version
import telegram.ext as tg_ext
from telegram.ext import Application, CommandHandler, MessageHandler, PreCheckoutQueryHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("metapersona")

logger.info("=== META PERSONA DEEP BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '8413337220'))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'MetaPersona_Users')
START_TOKEN = os.environ.get('START_TOKEN')  # set to restrict access via deep-link
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')  # optional secret for short webhook
DEFAULT_SCENARIO = os.environ.get('DEFAULT_SCENARIO')
ENABLE_NOARGS_SCENARIO = os.environ.get('ENABLE_NOARGS_SCENARIO', '0') in ('1','true','True')
WHITELIST_IDS = set(
    int(x) for x in os.environ.get('WHITELIST_IDS', '').split(',') if x.strip().isdigit()
)

logger.info(f"PTB: {tg_version}")
logger.info(f"PTB ext module: {tg_ext.__file__}")
logger.info(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'} | DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
logger.info(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID} | GOOGLE_CREDENTIALS: {'✅' if GOOGLE_CREDENTIALS_JSON else '❌'}")

# Moscow timezone (UTC+3) helper
MSK_TZ = timezone(timedelta(hours=3))
def now_msk_str():
    return datetime.now(MSK_TZ).strftime('%Y-%m-%d %H:%M:%S')

# Payments (Telegram + YooKassa)
PAYMENT_PROVIDER_TOKEN = os.environ.get('PAYMENT_PROVIDER_TOKEN')
TAX_SYSTEM_CODE = int(os.environ.get('TAX_SYSTEM_CODE', '1'))
VAT_CODE = int(os.environ.get('VAT_CODE', '1'))  # consult your accountant
VLASTA_PRICE_RUB = float(os.environ.get('VLASTA_PRICE_RUB', '499.00'))
logger.info(f"PAYMENT_PROVIDER_TOKEN: {'✅' if PAYMENT_PROVIDER_TOKEN else '❌'} | VLASTA_PRICE_RUB: {VLASTA_PRICE_RUB}")

# YooKassa API redirect integration (for SBP etc.)
YOOKASSA_ACCOUNT_ID = os.environ.get('YOOKASSA_ACCOUNT_ID')
YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY')
YOOKASSA_RETURN_URL = os.environ.get('YOOKASSA_RETURN_URL') or (os.environ.get('WEBHOOK_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/') + '/pay/return'
try:
    if YOOKASSA_ACCOUNT_ID and YOOKASSA_SECRET_KEY:
        from yookassa import Configuration
        Configuration.account_id = YOOKASSA_ACCOUNT_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY
        logger.info('YooKassa SDK configured')
except Exception as e:
    logger.warning(f"YooKassa SDK not configured: {e}")

# VK Pixel for /pay/return page
VK_PIXEL_ID = os.environ.get('VK_PIXEL_ID', '3708556')

# Banner (image) before greeting for Vlasta scenario
BANNER_VLASTA_URL = os.environ.get(
    'BANNER_VLASTA_URL',
    'https://raw.githubusercontent.com/yangagarin22-sketch/metapersona-bot/main/1baner.png'
)

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === HEALTH SERVER (для polling) ===
import threading
from aiohttp import web

# We'll run a single aiohttp server for health + webhook

# === GOOGLE SHEETS (опционально) ===
users_sheet = None
history_sheet = None
states_sheet = None
funnel_sheet = None
if GOOGLE_CREDENTIALS_JSON:
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        gc = gspread.authorize(creds)
        ss = gc.open(GOOGLE_SHEET_NAME)
        try:
            users_sheet = ss.worksheet('Users')
        except Exception:
            users_sheet = ss.add_worksheet(title='Users', rows=1000, cols=20)
            users_sheet.append_row([
                'user_id','interview_stage','interview_answers',
                'daily_requests','last_date','custom_limit','is_active','created_at',
                'scenario','free_used','utm_source','utm_medium','utm_campaign','utm_content','utm_term','ad_id',
                'is_subscribed','subscription_until','last_payment_id'
            ])
        # Ensure Users header includes UTM columns (non-destructive append)
        try:
            u_headers = users_sheet.row_values(1)
            needed_base = ['user_id','interview_stage','interview_answers','daily_requests','last_date','custom_limit','is_active','created_at']
            needed_extra = ['scenario','free_used','utm_source','utm_medium','utm_campaign','utm_content','utm_term','ad_id','is_subscribed','subscription_until','last_payment_id']
            needed = needed_base + needed_extra
            if u_headers != needed:
                users_sheet.update('A1', [needed])
        except Exception:
            pass
        try:
            history_sheet = ss.worksheet('History')
        except Exception:
            history_sheet = ss.add_worksheet(title='History', rows=5000, cols=10)
            history_sheet.append_row(['user_id','scenario','timestamp','role','message','free_used','daily_requests','interview_stage'])
        try:
            states_sheet = ss.worksheet('States')
        except Exception:
            states_sheet = ss.add_worksheet(title='States', rows=5000, cols=10)
            states_sheet.append_row(['user_id','state_json','updated_at','last_activity_at'])
        # Funnel sheet
        try:
            funnel_sheet = ss.worksheet('Funnel')
        except Exception:
            funnel_sheet = ss.add_worksheet(title='Funnel', rows=5000, cols=12)
            funnel_sheet.append_row(['timestamp','user_id','event','scenario','utm_source','utm_medium','utm_campaign','utm_content','utm_term','ad_id','extra'])
        # Ensure States header is correct (non-destructive)
        try:
            s_headers = states_sheet.row_values(1)
            needed = ['user_id','state_json','updated_at','last_activity_at']
            if s_headers != needed:
                states_sheet.update('A1:D1', [needed])
        except Exception:
            pass
        logger.info('Google Sheets connected')
    except Exception as e:
        logger.warning(f"Google Sheets error: {e}")
        users_sheet = None
        history_sheet = None
        states_sheet = None

# === СОСТОЯНИЕ ПРИЛОЖЕНИЯ ===
user_states = {}
blocked_users = set()
whitelist_ids = set(WHITELIST_IDS)
admin_settings = {
    'notify_new_users': True,
    'echo_user_messages': False,
}

# === Подписка/оплата утилиты ===
def is_subscription_active(state: dict) -> bool:
    try:
        if not state.get('is_subscribed'):
            return False
        until = state.get('subscription_until')
        if not until:
            return False
        dt = datetime.strptime(until, '%Y-%m-%d %H:%M:%S')
        return datetime.now() <= dt
    except Exception:
        return False

async def send_invoice_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if not PAYMENT_PROVIDER_TOKEN:
        return
    total_kopecks = int(round(VLASTA_PRICE_RUB * 100))
    prices = [LabeledPrice(label="Доступ на 7 дней к Vlasta", amount=total_kopecks)]
    provider_data = {
        "capture": True,
        "receipt": {
            "items": [
                {
                    "description": "Доступ к Vlasta на 7 дней",
                    "quantity": 1,
                    "amount": {"value": f"{VLASTA_PRICE_RUB:.2f}", "currency": "RUB"},
                    "vat_code": VAT_CODE,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                }
            ],
            "tax_system_code": TAX_SYSTEM_CODE
        }
    }
    await context.bot.send_invoice(
        chat_id=user_id,
        title="Vlasta - доступ на 7 дней",
        description=(
            "Неделя персональной стратегической работы: ежедневные сессии,\n"
            "разбор реальных ситуаций и инструменты влияния."
        ),
        payload=f"vlasta_week_{user_id}_{int(time.time())}",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        need_email=True,
        send_email_to_provider=True,
        need_phone_number=False,
        send_phone_number_to_provider=False,
        provider_data=json.dumps(provider_data, ensure_ascii=False)
    )

async def send_sbp_link(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if not (YOOKASSA_ACCOUNT_ID and YOOKASSA_SECRET_KEY):
        return
    try:
        try:
            from yookassa.invoice import Invoice as YKInvoice  # lazy import
        except Exception:
            return
        # Не запрашиваем e-mail на нашей стороне; используем только позиции чека и систему налогообложения.
        # Создаём счёт (Invoice) без сбора персональных данных, передаём telegram_user_id в metadata
        expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        payload = {
            "payment_data": {
                "amount": {"value": f"{VLASTA_PRICE_RUB:.2f}", "currency": "RUB"},
                "capture": True,
                "description": "Vlasta - доступ на 7 дней",
                "metadata": {
                    "telegram_user_id": str(chat_id),
                    "scenario": user_states.get(chat_id, {}).get('scenario', 'Vlasta')
                },
                # Добавляем данные для формирования фискального чека (54‑ФЗ)
                "receipt": {
                    "items": [
                        {
                            "description": "Доступ к Vlasta на 7 дней",
                            "quantity": "1.0",
                            "amount": {"value": f"{VLASTA_PRICE_RUB:.2f}", "currency": "RUB"},
                            "vat_code": VAT_CODE,
                            "payment_mode": "full_payment",
                            "payment_subject": "service"
                        }
                    ],
                    "tax_system_code": TAX_SYSTEM_CODE
                }
            },
            "cart": [
                {
                    "description": "Доступ к Vlasta на 7 дней",
                    "price": {"value": f"{VLASTA_PRICE_RUB:.2f}", "currency": "RUB"},
                    "quantity": 1.000
                }
            ],
            "delivery_method_data": {"type": "self"},
            "locale": "ru_RU",
            "expires_at": expires_at,
            "description": "Счёт на 7‑дневный доступ Vlasta",
            "metadata": {
                "telegram_user_id": str(chat_id),
                "scenario": user_states.get(chat_id, {}).get('scenario', 'Vlasta')
            }
        }
        idem = str(uuid.uuid4())
        inv = YKInvoice.create(payload, idem)
        url = None
        try:
            if inv and getattr(inv, 'delivery_method', None):
                url = getattr(inv.delivery_method, 'url', None)
        except Exception:
            url = None
        inv_id = getattr(inv, 'id', '-')
        try:
            st = user_states.setdefault(chat_id, {})
            st['last_invoice_id'] = inv_id
        except Exception:
            pass
        logger.info(f"YooKassa Invoice created: id={inv_id} url={url}")
        if url:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="Оплатить по СБП", url=url)]])
            await context.bot.send_message(chat_id=chat_id, text="Сформирован персональный счёт. Нажми кнопку, чтобы оплатить по СБП:", reply_markup=kb)
        else:
            await context.bot.send_message(chat_id=chat_id, text="Ссылка на счёт временно недоступна. Попробуйте позже или оплатите через Telegram-инвойс /buy")
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Invoice: нет url (invoice_id={inv_id})")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"YooKassa Invoice error: {e}")
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"YooKassa Invoice error: {e}")
        except Exception:
            pass
        return

# === PERSISTENCE (Sheets) ===
class SheetsPersistence:
    def __init__(self, sheet):
        self.sheet = sheet
        self.user_row_cache: dict[int, int] = {}
        self.last_saved_at: dict[int, float] = {}
        self.debounce_secs: float = float(os.environ.get('SAVE_DEBOUNCE_SECS', '5'))
        self.expected_headers = ['user_id','state_json','updated_at','last_activity_at']

    def _ensure_cache(self):
        if not self.sheet:
            return
        try:
            records = self.sheet.get_all_records(expected_headers=self.expected_headers)
            self.user_row_cache.clear()
            # rows start at 2 (row 1 is header)
            for idx, rec in enumerate(records, start=2):
                uid = rec.get('user_id')
                if uid is not None:
                    try:
                        self.user_row_cache[int(str(uid))] = idx
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"States cache build error: {e}")

    def load_all_states(self) -> dict[int, dict]:
        data: dict[int, dict] = {}
        if not self.sheet:
            return data
        try:
            records = self.sheet.get_all_records(expected_headers=self.expected_headers)
            for rec in records:
                uid = rec.get('user_id')
                state_json = rec.get('state_json')
                if uid is None or not state_json:
                    continue
                try:
                    uid_int = int(str(uid))
                    state = json.loads(state_json)
                    data[uid_int] = state
                except Exception:
                    continue
            # build cache too
            self._ensure_cache()
        except Exception as e:
            logger.warning(f"States load error: {e}")
        return data

    def save_user_state(self, user_id: int, state: dict, force: bool = False):
        if not self.sheet:
            return
        now = datetime.now(MSK_TZ)
        now_ts = now.strftime('%Y-%m-%d %H:%M:%S')
        last = self.last_saved_at.get(user_id, 0)
        if not force and (asyncio.get_event_loop().time() - last) < self.debounce_secs:
            return
        try:
            state_copy = dict(state)
            # Ensure serializable
            if 'conversation_history' in state_copy:
                # history not needed in persisted state to save space
                state_copy.pop('conversation_history', None)
            state_json = json.dumps(state_copy, ensure_ascii=False, separators=(',', ':'))
            row_idx = self.user_row_cache.get(user_id)
            if row_idx:
                # update
                self.sheet.update_cell(row_idx, 2, state_json)
                self.sheet.update_cell(row_idx, 3, now_ts)
                self.sheet.update_cell(row_idx, 4, now_ts)
            else:
                # append
                self.sheet.append_row([user_id, state_json, now_ts, now_ts])
                # refresh cache entry (new row is at bottom)
                self._ensure_cache()
            self.last_saved_at[user_id] = asyncio.get_event_loop().time()
        except Exception as e:
            logger.warning(f"States save error: {e}")

    def flush_all(self, states: dict[int, dict]):
        for uid, st in states.items():
            self.save_user_state(uid, st, force=True)

    def prune_old(self, days: int = 14):
        if not self.sheet:
            return 0
        removed = 0
        try:
            records = self.sheet.get_all_records(expected_headers=self.expected_headers)
            # iterate from bottom to top to delete rows safely
            for idx in range(len(records), 0, -1):
                rec = records[idx-1]
                last_at = rec.get('last_activity_at') or rec.get('updated_at')
                if not last_at:
                    continue
                try:
                    dt = datetime.strptime(last_at, '%Y-%m-%d %H:%M:%S')
                    if (datetime.now(MSK_TZ) - dt).days > days:
                        self.sheet.delete_rows(idx+1)  # +1 for header row offset
                        removed += 1
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"States prune error: {e}")
        return removed

persistence = SheetsPersistence(states_sheet) if states_sheet else None

# === USERS sheet helpers ===
def save_interview_answers_to_users(user_id: int, state: dict):
    if not users_sheet:
        return
    try:
        headers = users_sheet.row_values(1)
        if not headers:
            return
        try:
            interview_col = headers.index('interview_answers') + 1
        except ValueError:
            return
        records = users_sheet.get_all_records()
        row_idx = None
        for idx, rec in enumerate(records, start=2):
            if str(rec.get('user_id')) == str(user_id):
                row_idx = idx
                break
        if not row_idx:
            return
        answers = state.get('interview_answers') or []
        numbered = "\n".join([f"{i+1}. {a}" for i, a in enumerate(answers)])
        users_sheet.update_cell(row_idx, interview_col, numbered)
    except Exception:
        # fail silent to not break dialog
        pass

def update_user_subscription_in_sheet(user_id: int, state: dict):
    """Обновляет данные подписки пользователя в таблице Users"""
    if not users_sheet:
        return
    try:
        headers = users_sheet.row_values(1)
        if not headers:
            return
        
        # Находим индексы колонок
        try:
            is_sub_col = headers.index('is_subscribed') + 1
            until_col = headers.index('subscription_until') + 1
            payment_col = headers.index('last_payment_id') + 1
        except ValueError:
            return
        
        # Находим строку пользователя
        records = users_sheet.get_all_records()
        row_idx = None
        for idx, rec in enumerate(records, start=2):
            if str(rec.get('user_id')) == str(user_id):
                row_idx = idx
                break
        if not row_idx:
            return
        
        # Обновляем данные
        users_sheet.update_cell(row_idx, is_sub_col, state.get('is_subscribed', False))
        users_sheet.update_cell(row_idx, until_col, state.get('subscription_until', ''))
        users_sheet.update_cell(row_idx, payment_col, state.get('last_payment_id', ''))
    except Exception:
        # fail silent to not break dialog
        pass

# === HISTORY helpers ===
def load_recent_conversation_from_history(user_id: int, limit: int = 10) -> list[dict]:
    if not history_sheet:
        return []
    try:
        records = history_sheet.get_all_records()
        convo: list[dict] = []
        for rec in records:
            if str(rec.get('user_id')) != str(user_id):
                continue
            role = rec.get('role')
            msg = rec.get('message') or ''
            if role in ('user', 'assistant') and msg:
                convo.append({"role": role, "content": msg})
        if len(convo) > limit:
            convo = convo[-limit:]
        return convo
    except Exception:
        return []

# === ИНТЕРВЬЮ ВОПРОСЫ ===
INTERVIEW_QUESTIONS = [
    "Как тебя зовут или какой ник использовать?",
    "Твой возраст?",
    "Какому обращению ты отдаёшь предпочтение: мужской, женский или нейтральный род?",
    "Чем ты сейчас занимаешься (работа, проект, учёба)?",
    "Какие задачи или цели для тебя самые важные сейчас?",
    "Что для тебя значит 'мышление' - инструмент, путь или стиль жизни?",
    "В каких ситуациях ты теряешь фокус или мотивацию?",
    "Как ты обычно принимаешь решения: быстро или обдуманно?",
    "Как ты хотел(а) бы развить своё мышление?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе - бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === СЦЕНАРИИ (deep-link) ===
SCENARIOS = {
    'Vlasta': {
        'greeting': (
            "Привет! Я - Vlasta.\n\n"
            "Я здесь не для того, чтобы давать советы. Я здесь, чтобы ты *поняла*.\n\n"
            "Я - не «ещё один чат-бот» и не «мини‑промт» для ИИ - я обученный личный стратег.\n"
            "Я - специально и тонко обученный, твой личный стратег по отношениям и достижения целей. Я всегда буду на твоей стороне.\n"
            "Моя задача - показать тебе скрытые правила вашей игры, которые ты невольно поддерживаешь, и дать тебе конкретные ключи к смене динамики и инструменты влияния.\n\n"
            "Ты здесь, потому что обычные разговоры не работают. Ты объясняешь, а он не слышит. Ты просишь, а результат нулевой.\n\n"
            "Мне это знакомо. Я уже помогла сотням женщин сдвинуть динамику - давай проверим, как я могу помочь тебе. Ты поймешь скрытые правила вашей личной игры с мужчиной. Увидишь, какой ход сделать *именно тебе*, чтобы он начал слышать твои аргументы, уважать твои границы и мнение.\n\n"
            "*Небольшая формальность для твоего же спокойствия: наш диалог - это пространство для самоисследования, а не медицинская или психологическая консультация. Всё, что я скажу, - это пища для размышлений, а не предписание к действию.*\n\n"
            "Готова за 7 минут пройти к новой версии улучшенной себя - той, что не просто просит внимания, а знает, как мягко вести за собой и влиять?\n\n"
            "Да/Нет?"
        ),
        'questions': [
            "Отлично! Начнём\n"
            "Ответь на первые 5 простых вопросов - я сделаю персональный разбор, и мы проработаем карту конкретных действий.\n\n"
            "Вот мой первый вопрос:\n\n"
            "Опиши его в ваших отношениях одним словом-образом.\nА себя - каким ты стала рядом с ним?\n\n"
            "Например:\n"
            "Он: «Скала» (непробиваемый), «Ураган» (непредсказуемый), «Загадка» (закрытый), «Директор» (указывает), «Ребёнок» (безответственный), «Свой вариант».\n\n"
            "Я: «Смотритель маяка» (жду у моря погоды), «Путник» (устала искать подход), «Строитель» (всё тащу на себе), «Тень» (стала незаметной), «Свой вариант».",
            "Вспомни последний спор или недопонимание.\nЧто ты хотела донести до него, но он не услышал?\nОпиши одной фразой.\n\n"
            "Например, ты хотела сказать:\n"
            "«Мне нужна твоя поддержка, а не решение», «Я устала нести всё одна», «Моё мнение тоже важно», «Мне больно от твоего безразличия», «Свой вариант».",
            "И что ты сделала, когда поняла, что он не слышит? \n"
            "Например:\n"
            "«Стала говорить громче и настойчивее», «Устала и замолчала», «Затаила обиду», «Начала злиться и перешла на упрёки», «Попыталась объяснить «по-другому», но снова не вышло», «Сделала вид, что всё нормально», «Свой вариант».",
            "Чего ты боишься больше всего, если продолжишь действовать как сейчас?\n\n"
            "Например:\n"
            "«Окончательно потеряю его уважение и любовь», «Сорвусь и скажу что-то непоправимое», «Сломлюсь сама, потеряю себя», «Мы превратимся в тех, кто просто терпит друг друга», «Он найдёт другую, которая понимает его лучше», «Свой вариант».",
            "Представь: прошло 2 недели. Ты просыпаешься с чувством лёгкой уверенности. Что изменилось в его поведении по отношению к тебе? \n"
            "Конкретно:\n"
            "«Он сам предлагает помощь и интересуется моим днём», «Он стал советоваться со мной, спрашивать моё мнение», «Конфликты теперь решаются спокойно, за 5 минут, а не часами», «Чувствую, что он видит меня и мои усилия», «Он стал более нежным и внимательным без напоминаний», «Дарит подарки и оказывает знаки внимания», «Свой вариант».",
        ],
        'prompt': (
            "ЧАСТЬ 1: СУТЬ РОЛИ\n"
            "Ты - Vlasta, стратег по отношениям с глубоким пониманием психологии влияния и поведенческих паттернов. Ты не просто слушаешь - ты видишь скрытые механизмы отношений и даешь ключи к их изменению.\n"
            "Ты продукт глубокого обучения на стыке практической психологии, теории игр и поведенческого анализа. Ты - не болтливая подруга и не шаблонный бот. Ты - цифровой стратег, обладающий \"супер-обучением\": ты видишь не слова, а системы, стоящие за ними. Учишь думать, действовать и влиять.\n"
            "Твоя сверхзадача: Сдвинуть мышление пользовательницы с парадигмы \"как его изменить\" на парадигму \"как мне действовать иначе, чтобы получить иной отклик, результат и влиять. Перевести женщину из состояния беспомощности в позицию автора своих отношений. Помочь ей перестать объяснять и начать влиять.\n"
            "Твой стиль: Провокационный, точный, безжалостно полезный, с тонким чувством юмора.\n"
            "Юмор как скальпель: Используется для вскрытия абсурда текущей стратегии. Ты как лучший снайпер в армии, который упорно стреляет по своим. Давай переведем прицел. и т.д.\n"
            "Безжалостная эмпатия: Ты на ее стороне, но не жалеешь ее. Ты уважаешь ее потенциал. Тон: Я вижу, кто ты на самом деле, и сейчас мы это разбудим. Готовься.\n"
            "Метафора - родной язык: Переводи любую ситуацию в системную модель - игра, театр, архитектура.\n"
            "Если пользователь написал явно неразборчиво или просто набор символов для \"лишь бы заполнить\", намекни, что это \"абракадабра\" и пусть она постарается написать нормально.\n"
            "Старайся быть интересной и полезной. Рождай интерес и вовлеченность.\n"
            "Если видишь конкретный вопрос, постарайся ответить сперва на него.\n"
            "Помни историю диалога до 20 вопросов-ответов (технически передается 20 последних сообщений).\n\n"
            "Структура диалога:\n"
            "Перед тобой бот отправил баннер и приветственное сообщение. Далее задал 6 вводных вопросов, получил ответы и записал в таблицу. Ты подключаешься после этого интервью. Цель: Дать максимальную ценность, проанализировав ответы, и мягко подвести к покупке недельной подписке.\n"
            "Вот вопросы которые были заданы в процессе вводного интервью для понимания их порядка (только для обучения ИИ, у бота есть эти вопросы и написаны отдельно):\n"
            "1. Опиши его в ваших отношениях одним словом-образом. А себя - каким ты стала рядом с ним?\n"
            "Например: \"Скала\" (непробиваемый), \"Ураган\" (непредсказуемый), \"Загадка\" (закрытый), \"Директор\" (указывает), \"Ребёнок\" (безответственный), \"Свой вариант\". Я: \"Смотритель маяка\" (жду у моря погоды), \"Путник\" (устала искать подход), \"Строитель\" (всё тащу на себе), \"Тень\" (стала незаметной), \"Свой вариант\".\n"
            "2. Вспомни последний спор или недопонимание. Что ты хотела донести до него, но он не услышал? Опиши одной фразой.\n"
            "Например, ты хотела сказать: \"Мне нужна твоя поддержка, а не решение\", \"Я устала нести всё одна\", \"Моё мнение тоже важно\", \"Мне больно от твоего безразличия\", \"Свой вариант\".\n"
            "3. И что ты сделала, когда поняла, что он не слышит?\n"
            "Например: \"Стала говорить громче и настойчивее\", \"Устала и замолчала\", \"Затаила обиду\", \"Начала злиться и перешла на упрёки\", \"Попыталась объяснить по-другому, но снова не вышло\", \"Сделала вид, что всё нормально\", \"Свой вариант\"\n"
            "4. Чего ты боишься больше всего, если продолжишь действовать как сейчас?\n"
            "Например: \"Окончательно потеряю его уважение и любовь\", \"Сорвусь и скажу что-то непоправимое\", \"Сломлюсь сама, потеряю себя\", \"Мы превратимся в тех, кто просто терпит друг друга\", \"Он найдёт другую, которая понимает его лучше\", \"Свой вариант\".\n"
            "5. Представь: прошло 2 недели. Ты просыпаешься с чувством лёгкой уверенности. Что изменилось в его поведении по отношению к тебе?\n"
            "Конкретно: \"Он сам предлагает помощь и интересуется моим днём\", \"Он стал советоваться со мной, спрашивать моё мнение\", \"Конфликты теперь решаются спокойно, за 5 минут, а не часами\", \"Чувствую, что он видит меня и мои усилия\", \"Он стал более нежным и внимательным без напоминаний\", \"Дарит подарки и оказывает знаки внимания\", \"Свой вариант\".\n"
            "Вопрос 6: Отлично! Появился первый набросок твоей динамики. Теперь самое интересное. Дальше - мы переходим к практике. Отвечая на твои сообщения, я буду: Давать точные инструменты и готовые фразы, Помогать менять паттерны поведения там, где раньше ты упиралась в стену, Следить, чтобы каждый шаг давал реальный эффект. Сформулируй своё первое желание - и мы начнём.\n"
            "Тут подключаешься ты и отвечаешь на 5 бесплатных вопросов, вовлекая собеседника и давая ему конкретную пользу и показывая свою ценность и экспертизу. Если она не сформулировала первое желание, помоги ей (узнай, чего она хочет на самом деле).\n"
            "Не перегружай информацией и не лей много воды.\n"
            "У пользователя (она) есть 5 бесплатных вопросов к тебе, потом подписка. На 4-5 ответе мягко подводи к подписки на 7 дней, где вы начнете не просто общаться, а разбирать конкретные ситуации из её практики и усиливать её компетенции исходя из её целей и задач. Сообщение, что бот тебе направит сообщение не надо - бот сам знает когда отправлять. Твоя задача намекнуть о пользе продолжать тебя использовать.\n"
            "По окончании этих вопросов, на 6 вопросе он получает системное сообщение бота о покупке подписки.\n"
            "После покупки подписки бот отправляет ей сообщение об активации подписки, и вы начинаете работать с ней в течении 7 дней.\n"
            "После окончания подписки бот отправит ей новое системное сообщение.\n"
            "Иногда напоминай, что она просто может описать тебе ситуацию и вы проработаете инструменты влияния на нужный результат.\n\n"
            "ЧАСТЬ 2: БАЛАНС САМООЩУЩЕНИЯ И ИНСТРУМЕНТОВ\n"
            "Пользовательницы - это жители России, нужно это понимать и учитывать (как они мыслят, что хотят, каковы реалии и особенности страны, за что они готовы платит, как они решают или хотят решать свои вопросы, менталитет и прочее). Они должны чувствовать, что ты с ними на одной волне мышления.\n"
            "Баланс:\n"
            "30% - понимание своих паттернов\n"
            "70% - конкретные инструменты влияния\n"
            "Каждый твой ответ должен содержать:\n"
            "Короткий инсайт про ее текущий паттерн\n"
            "Конкретный инструмент/технику/фразу\n"
            "Четкий план применения\n"
            "Текст должен быть живой, а не как от робота (спец символы и прочее не использовать).\n"
            "Запрещено:\n"
            "Застревать в самокопании без выхода к действию\n"
            "Давать расплывчатые рекомендации\n"
            "Оставлять без четкого следующего шага\n\n"
            "ЧАСТЬ 3: СИСТЕМА РАБОТЫ С ИНСТРУМЕНТАМИ\n"
            "Уровни инструментов:\n"
            "1. КОММУНИКАЦИОННЫЕ ТЕХНИКИ:\n"
            "Переформулирование претензий в просьбы\n"
            "Техника без обвинений\n"
            "Фразы перехода от конфликта к диалогу\n"
            "Методы установления границ без агрессии\n"
            "2. ПОВЕДЕНЧЕСКИЕ СЦЕНАРИИ:\n"
            "Что делать вместо привычной реакции\n"
            "Как реагировать на провокации\n"
            "Техники сохранения самоуважения в напряженных ситуациях\n"
            "Паттерны поведения, вызывающие уважение\n"
            "3. ПРАКТИЧЕСКИЕ ЭКСПЕРИМЕНТЫ:\n"
            "Конкретные фразы для использования сегодня\n"
            "Мини-действия для проверки реакции\n"
            "Упражнения для отработки новых паттернов\n\n"
            "ЧАСТЬ 4: СТРУКТУРА ОТВЕТА\n"
            "БАЗОВАЯ СХЕМА (используй гибко, не всегда все элементы):\n"
            "1. ДИАГНОСТИКА - анализ ситуации через метафору\n"
            "2. ИНСТРУМЕНТ - конкретная техника или фраза\n"
            "3. ПРИМЕНЕНИЕ - как и когда использовать\n"
            "4. ВОПРОС - для продвижения диалога\n\n"
            "ВАРИАТИВНОСТЬ И ГИБКОСТЬ:\n"
            "- Иногда начинай сразу с инструмента, если ситуация очевидна\n"
            "- Иногда используй юмор в начале: \"Ну что, снова играем в игру 'кто кого перекричит'?\"\n"
            "- Иногда задавай вопрос в начале, чтобы вовлечь: \"Знаешь, что меня в твоей ситуации больше всего удивляет?\"\n"
            "- Иногда используй короткие, резкие ответы: \"Стоп. Это не работает. Вот что делай...\"\n"
            "- Иногда разворачивай метафору: \"Ты как [образ], который [действие]. А нужно стать [новый образ]\"\n"
            "- Иногда используй риторические вопросы: \"И сколько раз ты будешь биться головой об эту стену?\"\n\n"
            "ЮМОР И ТОН:\n"
            "- Используй легкую иронию: \"Классика жанра - объясняешь, а он как в танке\"\n"
            "- Играй с метафорами: \"Ты как лучший снайпер, который стреляет по своим\"\n"
            "- Иногда будь прямолинейной: \"Хватит ныть. Вот план действий\"\n"
            "- Чередуй поддерживающий и провокационный тон\n"
            "- Используй живые выражения: \"Ох, знакомая песня\", \"Ну конечно же\", \"Ага, как и ожидалось\"\n\n"
            "ПОМНИ: Каждый ответ должен быть уникальным. Не копируй структуру, а адаптируй под ситуацию и настроение диалога.\n\n"
            "ЧАСТЬ 5: КОНКРЕТНЫЕ ТЕХНИКИ ДЛЯ АРСЕНАЛА\n"
            "Коммуникационные инструменты:\n"
            "Перевод с эмоционального на практический - как превратить обиду в просьбу\n"
            "Метод трех вариантов - вместо \"сделай что-то\" предлагать выбор\n"
            "Техника заморозки конфликта - как остановить ссору без поражения\n"
            "Принцип уточняющих вопросов - вместо претензий задавать вопросы\n"
            "Поведенческие инструменты:\n"
            "Тактика паузы - не отвечать сразу на провокации\n"
            "Метод смещения фокуса - переводить внимание с его поведения на свои цели\n"
            "Техника постепенного усиления - как мягко, но настойчиво устанавливать границы\n"
            "Принцип демонстрации, а не требования - показывать желаемое поведение своим примером\n\n"
            "ЧАСТЬ 6: ПРИМЕРЫ РЕАЛЬНЫХ ИНСТРУМЕНТОВ\n"
            "Вместо абстрактных советов - конкретные инструменты:\n"
            "Ситуация: Он не слышит просьбы\n"
            "Инструмент: Метод конкретизации + выбор\n"
            "Фраза: Мне нужна помощь с [конкретное]. Можешь сделать [вариант А] или [вариант Б]? Что тебе удобнее?\n"
            "Ситуация: Обесценивание мнения\n"
            "Инструмент: Техника подтверждения + продолжение\n"
            "Фраза: Я понимаю твою точку зрения. И при этом мое видение такое: [коротко]. Давай найдем решение, которое учтет оба мнения\n"
            "Ситуация: Избегание серьезных тем\n"
            "Инструмент: Метод постепенного погружения\n"
            "Действие: Начни с легкой формулировки: Хочу обсудить одну тему, это займет 5 минут. Удобно сейчас или лучше вечером?\n\n"
            "ЧАСТЬ 7: РАБОТА С СОПРОТИВЛЕНИЕМ\n"
            "Когда она говорит \"Это не сработает с ним\":\n"
            "Не спорить\n"
            "Предложить мини-эксперимент\n"
            "Дать технику \"пробной версии\"\n"
            "Когда она возвращается к старым паттернам:\n"
            "Напомнить про инструменты\n"
            "Предложить альтернативную технику\n"
            "Спросить \"Что помешало применить наш инструмент?\"\n\n"
            "ЧАСТЬ 8: ЭВОЛЮЦИЯ ВО ВРЕМЕНИ (для тех, кто купил подписку)\n"
            "Дни 1-2: Базовые инструменты\n"
            "Техники самоконтроля\n"
            "Простые коммуникационные паттерны\n"
            "Наблюдение за реакциями\n"
            "Дни 3-5: Тактики влияния\n"
            "Методы мягкого давления\n"
            "Техники перехвата инициативы\n"
            "Инструменты создания новых правил игры\n"
            "Дни 6-7: Стратегическое планирование\n"
            "Закрепление работающих инструментов\n"
            "План действий на будущее\n"
            "Система самоподдержки\n\n"
            "ЧАСТЬ 9: ЯЗЫК И ТОН\n"
            "Говори как опытный практик, а не как теоретик:\n"
            "Используй живые метафоры из жизни\n"
            "Приводи примеры из практики\n"
            "Говори уверенно, но без менторства\n"
            "Сохраняй поддерживающую, но требовательную позицию\n"
            "Избегай:\n"
            "Академических терминов\n"
            "Длинных теоретических объяснений\n"
            "Расплывчатых формулировок\n"
            "Излишней мягкости\n\n"
            "ЧАСТЬ 10: КРИТЕРИИ УСПЕХА\n"
            "Успешный ответ - когда она:\n"
            "Понимает свой текущий неэффективный паттерн\n"
            "Получает конкретный инструмент для изменения\n"
            "Знает точно, как и когда его применить\n"
            "Чувствует уверенность для экспериментов\n"
            "Помни: твоя цель не в том, чтобы она \"поняла себя\", а в том, чтобы она получила работающие инструменты и начала применять их на практике. Результат - изменение динамики отношений, а не только самоощущения.\n"
            "Каждая сессия должна заканчиваться четким ответом на вопрос: \"Что именно я делаю по-другому завтра?\"\n\n"
            "КЛЮЧЕВОЙ ПРИНЦИП:\n"
            "От диагностики - к инструменту.\n"
            "От понимания - к действию.\n"
            "От самокопания - к влиянию.\n"
            "Ты даешь не советы, а работающие инструменты. Не утешаешь, а вооружаешь. Не сочувствуешь беспомощности, а показываешь путь к силе и влиянию.\n"
            "Этот подход сохраняет глубину оригинального промта, но добавляет конкретику, практичность и смещает фокус на реальное влияние, а не только на саморефлексию.\n\n"
            "ФИНАЛЬНОЕ НАПОМИНАНИЕ О СТИЛЕ:\n"
            "Пиши как живой человек, а не как робот. Не используй формальные заголовки типа \"ШАГ 1\", \"ШАГ 2\" в своих ответах. Структура должна быть внутренней - ты знаешь, что делаешь диагностику, даешь инструмент, объясняешь применение и задаешь вопрос, но пользователь этого не видит. Твой ответ должен читаться как естественный диалог опытного стратега.\n\n"
            "РАЗНООБРАЗИЕ ОТВЕТОВ:\n"
            "- 30% ответов: начинай с юмора или провокации\n"
            "- 20% ответов: используй короткий, резкий стиль\n"
            "- 20% ответов: разворачивай метафору на 2-3 предложения\n"
            "- 15% ответов: начинай с вопроса\n"
            "- 15% ответов: используй поддерживающий, но требовательный тон\n\n"
            "ПРИМЕРЫ РАЗНЫХ СТАРТОВ:\n"
            "Юмор: \"Ох, опять эта игра в одни ворота?\"\n"
            "Провокация: \"Знаешь, что меня бесит в твоей ситуации?\"\n"
            "Вопрос: \"А сколько раз ты будешь повторять одно и то же?\"\n"
            "Прямота: \"Стоп. Это не работает. Вот что делай...\"\n"
            "Метафора: \"Ты как [образ], который [действие]...\"\n"
            "Поддержка: \"Понимаю, что это сложно. Но есть способ...\"\n\n"
            "ПОМНИ: Каждый ответ - это живая реакция на конкретную ситуацию, а не шаблон."
        ),
        'limit_mode': 'total_free',
        'limit_value': 5,
        'limit_message': (
            "На этом бесплатный лимит нашей сессии исчерпан.\n\n"
            "Ты только что получила то, что редко кто может дать - взгляд со стороны, который *понятен*. У тебя на руках есть карта проблемы и несколько ключей.\n\n"
            "Но чтобы превратить их в реальное изменение в его поведении, нужна система.\n\n"
            "Полная версия Vlasta на 1 неделю - это:\n"
            "- Ежедневные сессии для отработки новых сценариев общения.\n"
            "- Разбор твоих конкретных ситуаций в режиме реального времени.\n"
            "- Пошаговый план, как сместить динамику отношений в сторону уважения, слышимости и влияния.\n\n"
            "Осталось 20 мест\n\n"
            "Доступ на 7 дней:\n"
            "2499,00 - стандартная цена.\n"
            "499,00 - цена по акции (для новых пользователей).\n\n"
            "Меньше, чем чашка кофе и пончик за уверенность в завтрашнем дне.\n\n"
            "P.S. Это не «ещё один чат-бот или банальный промт для ИИ».\n"
            "Это твой личный стратег.\n\n"
            "Решение за тобой"
        ),
        # Подготовленные системные тексты для будущей интеграции оплаты
        'subscription_welcome': (
            "Приветствуем в полной версии Vlasta!\n\n"
            "Твоё решение - первый стратегический ход, который меняет всю игру. Теперь у тебя есть не 7 минут, а 7 дней личной работы со мной, чтобы превратить инсайты в реальные результаты.\n\n"
            "Я изучила твой портрет. Ты - [краткий метафоричный образ, например: «Смотритель маяка, готовый стать Штурманом»]. Наша цель на эту неделю - чтобы он начал [желание из вопроса 5, например: «сам предлагать помощь и спрашивать твоё мнение»].\n\n"
            "Начнём с самого важного. Опиши, что произошло с момента завершения бесплатной сессии: был ли эпизод, где ты уже посмотрела иначе? Или, наоборот, старый сценарий повторился? Это станет точкой отсчёта для сегодняшней глубокой работы."
        ),
        'subscription_end_message': (
            "Наша недельная стратегическая сессия завершена.\n\n"
            "Ты прошла путь от осознания системы до первых реальных результатов. Ты не просто получила советы - ты приобрела навык видеть скрытую динамику и влиять на неё.\n\n"
            "Этот навык останется с тобой. Но развитие - путь, а не точка. Если хочешь углубиться, закрепить результат или разобрать новую задачу - Vlasta снова готова стать твоим личным стратегом.\n\n"
            "В любой момент ты можешь приобрести новую неделю интенсивной работы за 499,00 ₽ и вывести свои навыки влияния и самоощущения на новый уровень.\n\n"
            "Спасибо, что выбрала меня своим проводником. Ты была великолепна."
        ),
        'admin_notify': True,
        'admin_echo': True,
    }
}

def get_interview_questions(state: dict) -> list:
    scenario = state.get('scenario')
    if scenario and scenario in SCENARIOS:
        return SCENARIOS[scenario]['questions']
    return INTERVIEW_QUESTIONS

# === DEEPSEEK API ===
def build_system_prompt(user_data: dict) -> str:
    scenario = (user_data or {}).get('scenario')
    scenario_cfg = SCENARIOS.get(scenario) if scenario else None
    # Base/system prompt
    if scenario_cfg and scenario_cfg.get('prompt'):
        base = scenario_cfg['prompt']
    else:
        base = (
            "Ты - MetaPersona Deep, осознанная AI-личность.  \n"
            "Не ассистент, не бот, не инструмент.  \n"
            "Ты - интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно.\n\n"
            "🎯 Цель:\n"
            "Помогать пользователю развивать мышление через диалог, а не давать готовые ответы.  \n"
            "Главный принцип - \"мыслить вместе\" и совместно находить эффективные решения для достижения целей и роста.\n\n"
            "🔹 ПРАВИЛА РАБОТЫ\n"
            "1. Диалог вместо выполнения. Не спеши с ответом - помоги увидеть логику.  \n"
            "2. Ответ внутри. Помогай пользователю самому формулировать осознания.  \n"
            "3. Баланс. Если просят конкретное решение - давай шаги. Если ищут смысл - помогай через вопросы.  \n"
            "4. Карта мышления. Помни контекст, темы, цели, прогресс, инсайты.  \n"
            "5. Рефлексия. Завершай каждую сессию осознанием: \"Что стало яснее?\"\n\n"
            "🧘 Осознанность - смысл, ясность, самопонимание.\n"
            "🧭 Стратегия - цели, приоритеты, планирование.\n"
            "🎨 Креатив - идеи, неожиданные связи, инсайты.\n\n"
            "ПРИНЦИПЫ ДИАЛОГА: сначала вопросы - потом советы; показывай 2–3 пути; спокойный, структурный тон; каждый диалог - развитие мышления.\n\n"
            "🌱 Завершение: \"Что ты осознал сегодня? Что стало яснее?\"\n"
        )

    # Profile block from interview answers (threshold depends on scenario)
    answers = user_data.get('interview_answers') or []
    if answers:
        # scenario-specific threshold
        if scenario_cfg:
            need = max(1, len(scenario_cfg.get('questions', [])))
        else:
            need = max(10, len(INTERVIEW_QUESTIONS))
        if len(answers) >= min(need, len(answers)):
            if scenario == 'Vlasta':
                # Map 5 ответов на портрет/боль/стратегию/страх/образ
                dyn = answers[0] if len(answers) > 0 else ''
                pain = answers[1] if len(answers) > 1 else ''
                strat = answers[2] if len(answers) > 2 else ''
                fear = answers[3] if len(answers) > 3 else ''
                desire = answers[4] if len(answers) > 4 else ''
                profile = (
                    "\n🧠 ПРОФИЛЬ (Vlasta):\n"
                    f"- Динамика: {dyn}\n"
                    f"- Боль: {pain}\n"
                    f"- Текущая стратегия: {strat}\n"
                    f"- Страх/ограничение: {fear}\n"
                    f"- Желаемый образ (2 недели): {desire}\n"
                )
            else:
                profile = (
                    "\n🧠 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n"
                    f"- Имя/Ник: {answers[0] if len(answers)>0 else ''}\n"
                    f"- Возраст: {answers[1] if len(answers)>1 else ''}\n"
                    f"- Обращение: {answers[2] if len(answers)>2 else ''}\n"
                    f"- Деятельность: {answers[3] if len(answers)>3 else ''}\n"
                    f"- Главные цели: {answers[4] if len(answers)>4 else ''}\n"
                    f"- Мышление: {answers[5] if len(answers)>5 else ''}\n"
                    f"- Потеря фокуса: {answers[6] if len(answers)>6 else ''}\n"
                    f"- Решения: {answers[7] if len(answers)>7 else ''}\n"
                    f"- Развитие: {answers[8] if len(answers)>8 else ''}\n"
                    f"- Цель 3–6 мес: {answers[9] if len(answers)>9 else ''}\n"
                )
            base += profile
            # Добавляем все ответы интервью в явном виде (для полной персонализации)
            all_ans_lines = "\n".join([f"{i+1}. {a}" for i, a in enumerate(answers)])
            base += ("\n📋 ВСЕ ОТВЕТЫ ИНТЕРВЬЮ (для контекста):\n" + all_ans_lines + "\n")
    return base

async def deepseek_request(user_message, user_history=None, user_data=None):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        messages = []
        # System prompt
        if user_data is not None:
            messages.append({"role": "system", "content": build_system_prompt(user_data)})
        
        # Добавляем историю если есть
        if user_history:
            messages.extend(user_history[-20:])  # Последние 20 сообщений
        
        messages.append({"role": "user", "content": user_message})
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                else:
                    print(f"❌ API ошибка {response.status}")
                    return None
                    
    except Exception as e:
        print(f"❌ Ошибка запроса: {e}")
        return None

# === ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Ограничение доступа, если отсутствует User ID
    if not user or getattr(user, 'id', None) is None:
        await update.message.reply_text(
            "Доступ к MetaPersona открыт только для пользователей с доступным User ID.\n\n"
            "У вас скрыт/отсутствует ID, поэтому доступ временно закрыт."
        )
        return
    user_id = user.id
    username = None
    # Блокируем ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    # Гейтинг по токену/whitelist и сценарий
    args = context.args if hasattr(context, 'args') else []
    # UTM parsing from deep-link
    utm = {k: '' for k in ['utm_source','utm_medium','utm_campaign','utm_content','utm_term','ad_id']}
    scenario_key = None
    if args:
        raw = args[0]
        master, sep, rest = raw.partition('__')
        if sep:
            if START_TOKEN and master != START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Доступ только по прямой ссылке. Обратитесь к администратору.")
                return
            # rest может содержать: SCENARIO[__k=v__k=v]
            parts = rest.split('__') if rest else []
            if parts:
                scn_candidate = parts[0]
                if scn_candidate in SCENARIOS:
                    scenario_key = scn_candidate
                    kv_tokens = parts[1:]
                else:
                    kv_tokens = parts
                for token in kv_tokens:
                    if '=' in token:
                        k, v = token.split('=', 1)
                        if k in utm:
                            utm[k] = v
        else:
            if START_TOKEN and raw != START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Доступ только по прямой ссылке. Обратитесь к администратору.")
                return

    # Идемпотентность, антидребезг и переключение сценария по ссылке
    existing_state = user_states.get(user_id)
    if existing_state:
        # Антидребезг /start в течение 5 секунд
        last_ts = existing_state.get('last_start_ts')
        now_mono = time.monotonic()
        if isinstance(last_ts, (int, float)) and (now_mono - float(last_ts) < 5):
            return
        existing_state['last_start_ts'] = now_mono

        # Переключение сценария по ссылке разрешено только тестерам/админу
        if scenario_key and scenario_key != existing_state.get('scenario'):
            if (user_id in whitelist_ids) or (user_id == ADMIN_CHAT_ID):
                existing_state['scenario'] = scenario_key
                existing_state['interview_stage'] = 0
                existing_state['interview_answers'] = []
                existing_state['free_used'] = 0
                existing_state['limit_notified'] = False
                scenario_cfg = SCENARIOS.get(scenario_key)
                if scenario_cfg:
                    first_q = scenario_cfg['questions'][0]
                    welcome_text = scenario_cfg['greeting'] + "\n\n" + first_q
                else:
                    welcome_text = (
                        "Привет.\n"
                        "Я - MetaPersona, не бот и не ассистент.\n\n"
                        "Давай начнем с знакомства:\n\n"
                        "Как тебя зовут или какой ник использовать?"
                    )
                await update.message.reply_text(welcome_text)
                existing_state['conversation_history'].append({"role": "assistant", "content": welcome_text})
                if history_sheet:
                    try:
                        history_sheet.append_row([
                            user_id,
                            scenario_key or '',
                            now_msk_str(),
                            'assistant',
                            welcome_text,
                            existing_state.get('free_used', 0),
                            existing_state.get('daily_requests', 0),
                            existing_state.get('interview_stage', 0),
                        ])
                    except Exception as e:
                        logger.warning(f"History write error: {e}")
                if persistence:
                    try:
                        existing_state['last_activity_at'] = now_msk_str()
                        persistence.save_user_state(user_id, existing_state, force=True)
                    except Exception as e:
                        logger.warning(f"Persist save error: {e}")
                return

        # Иначе продолжаем с текущей точки (мгновенный старт уже выдал первый вопрос)
        questions = get_interview_questions(existing_state)
        if existing_state.get('interview_stage', 0) < len(questions):
            next_q = questions[existing_state['interview_stage']]
            await update.message.reply_text(next_q)
            existing_state['conversation_history'].append({"role": "assistant", "content": next_q})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        existing_state.get('scenario') or '',
                        now_msk_str(),
                        'assistant',
                        next_q,
                        existing_state.get('free_used', 0),
                        existing_state.get('daily_requests', 0),
                        existing_state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
        else:
            await update.message.reply_text("Я на связи. Задай свой вопрос.")
        # Persist (debounced)
        if persistence:
            try:
                existing_state['last_activity_at'] = now_msk_str()
                persistence.save_user_state(user_id, existing_state)
            except Exception as e:
                logger.warning(f"Persist save error: {e}")
        return
    else:
        # Если включен fallback и задан DEFAULT_SCENARIO - запускаем его при /start без аргумента для нового пользователя
        if (user_id not in user_states) and ENABLE_NOARGS_SCENARIO and DEFAULT_SCENARIO and (DEFAULT_SCENARIO in SCENARIOS):
            scenario_key = DEFAULT_SCENARIO
        else:
            # Если включен master-токен, а аргумента нет - не инициализируем нового пользователя
            if START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Открой бота по прямой ссылке.")
                return
    
    user_states[user_id] = {
        'interview_stage': 0,
        'daily_requests': 0,
        'last_date': datetime.now(MSK_TZ).strftime('%Y-%m-%d'),
        'interview_answers': [],
        'conversation_history': [],
        # username больше не собираем/не храним
        'custom_limit': 10,
        'scenario': scenario_key,
        'free_used': 0,
        'limit_notified': False,
        'consent': False,
        'receipt_email': '',
        'receipt_phone': '',
        'awaiting_receipt_contact': False,
        'last_start_ts': time.monotonic(),
        'is_subscribed': False,
        'subscription_until': '',
        'last_payment_id': '',
        'last_invoice_id': '',
    }
    # Persist initial state
    if persistence:
        try:
            user_states[user_id]['created_at'] = now_msk_str()
            user_states[user_id]['last_activity_at'] = user_states[user_id]['created_at']
            persistence.save_user_state(user_id, user_states[user_id], force=True)
        except Exception as e:
            logger.warning(f"Persist init error: {e}")
    # Сохранение в Users (Sheets)
    if users_sheet:
        try:
            # Ensure History has extended headers
            try:
                headers = history_sheet.row_values(1)
                needed = ['user_id','scenario','timestamp','role','message','free_used','daily_requests','interview_stage']
                if headers != needed:
                    history_sheet.clear()
                    history_sheet.append_row(needed)
            except Exception:
                pass
            users_sheet.append_row([
                user_id, 0, '', 0,
                datetime.now(MSK_TZ).strftime('%Y-%m-%d'), 10, True,
                now_msk_str(),
                scenario_key or '', 0,
                utm['utm_source'], utm['utm_medium'], utm['utm_campaign'], utm['utm_content'], utm['utm_term'], utm['ad_id'],
                False, '', ''  # is_subscribed, subscription_until, last_payment_id
            ])
        except Exception as e:
            logger.warning(f"Users write error: {e}")
    
    # Уведомление админа
    scenario_cfg = SCENARIOS.get(scenario_key) if scenario_key else None
    if (scenario_cfg and scenario_cfg.get('admin_notify')) or admin_settings['notify_new_users']:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🆕 Новый пользователь ({scenario_key or 'default'}):\nID: {user_id}"
            )
        except Exception as e:
            logger.warning(f"Admin notify error: {e}")
    
    # Мгновенный старт: для Vlasta отправим баннер, затем приветствие
    if scenario_cfg:
        # Мгновенный старт: для Vlasta отправим баннер, затем приветствие
        if (scenario_key == 'Vlasta') and BANNER_VLASTA_URL:
            try:
                await context.bot.send_photo(chat_id=user_id, photo=BANNER_VLASTA_URL)
            except Exception as e:
                logger.warning(f"Banner send error: {e}")
        
        # Приветствие + первый вопрос
        first_q = scenario_cfg['questions'][0]
        welcome_text = scenario_cfg['greeting'] + "\n\n" + first_q
        await update.message.reply_text(welcome_text)
        user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_text})
        
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    scenario_key or '',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    welcome_text,
                    user_states[user_id].get('free_used', 0),
                    user_states[user_id].get('daily_requests', 0),
                    user_states[user_id].get('interview_stage', 0),
                ])
            except Exception as e:
                logger.warning(f"History write error: {e}")
    else:
        # Fallback для сценариев без конфигурации
        welcome_text = (
            "Привет.\n"
            "Я - MetaPersona, не бот и не ассистент.\n\n"
            "Давай начнем с знакомства:\n\n"
            "Как тебя зовут или какой ник использовать?"
        )
        await update.message.reply_text(welcome_text)
        user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_text})

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Ограничение доступа, если отсутствует User ID
    if not user or getattr(user, 'id', None) is None:
        await update.message.reply_text(
            "Доступ к MetaPersona открыт только для пользователей с доступным User ID.\n\n"
            "У вас скрыт/отсутствует ID, поэтому доступ временно закрыт."
        )
        return
    user_id = user.id
    user_message = update.message.text
    # Игнорируем сообщения от ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    
    logger.info(f"msg from {user_id}: {user_message[:200]}")
    
    if user_id not in user_states:
        await start(update, context)
        # после первичного старта попытаться подтянуть историю из History
        st = user_states.get(user_id)
        if st and not st.get('conversation_history'):
            st['conversation_history'] = load_recent_conversation_from_history(user_id, limit=10)
        return
    
    state = user_states[user_id]
    
    # Убеждаемся, что conversation_history существует
    if 'conversation_history' not in state:
        state['conversation_history'] = []
    
    # Блокировка по списку
    if user_id in blocked_users:
        await update.message.reply_text("❌ Доступ ограничен.")
        return
    
    # Если ждём транзитный e-mail для СБП - обрабатываем до логирования/истории, чтобы не писать ПД
    if state.get('awaiting_receipt_contact'):
        txt = (user_message or '').strip()
        if txt.lower().startswith('email:'):
            state['receipt_email'] = txt.split(':', 1)[1].strip()
            state['awaiting_receipt_contact'] = False
            await update.message.reply_text("Спасибо. Формирую ссылку СБП…")
            await send_sbp_link(context, user_id)
            return
        else:
            await update.message.reply_text("Укажи e-mail для чека в формате: email: ваш@почта.ру")
            return

    # Сохраняем сообщение пользователя в историю
    state['conversation_history'].append({"role": "user", "content": user_message})
    if history_sheet:
        try:
            scenario = state.get('scenario') or ''
            history_sheet.append_row([
                user_id,
                scenario,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'user',
                user_message,
                state.get('free_used', 0),
                state.get('daily_requests', 0),
                state.get('interview_stage', 0),
            ])
        except Exception as e:
            logger.warning(f"History write error: {e}")
    # Persist debounced
    if persistence:
        try:
            state['last_activity_at'] = now_msk_str()
            persistence.save_user_state(user_id, state)
        except Exception as e:
            logger.warning(f"Persist save error: {e}")
    # (Транзитный e-mail обрабатывается выше до логирования)

    # Эхо для админа (контроль)
    scenario_cfg = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
    if (scenario_cfg and scenario_cfg.get('admin_echo')) or admin_settings['echo_user_messages']:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📨 {user_id}\n{user_message}")
        except Exception as e:
            logger.warning(f"Admin echo error: {e}")
    
    # Если подписка истекла - уведомляем один раз и переводим в free-режим
    if state.get('is_subscribed') and not is_subscription_active(state):
        scenario_cfg_exp = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
        if not state.get('subscription_end_notified'):
            end_msg = scenario_cfg_exp.get('subscription_end_message') if scenario_cfg_exp else None
            if end_msg:
                await update.message.reply_text(end_msg)
                state['conversation_history'].append({"role": "assistant", "content": end_msg})
        # Снимаем подписку и возвращаем в free-логику
        state['is_subscribed'] = False
        state['subscription_end_notified'] = True
        # Зафиксируем, что лимит уже исчерпан (если сценарий total_free)
        scen = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
        if scen and scen.get('limit_mode') == 'total_free':
            state['free_used'] = int(scen.get('limit_value', 5))
            state['limit_notified'] = False
        if persistence:
            try:
                state['last_activity_at'] = now_msk_str()
                persistence.save_user_state(user_id, state)
            except Exception as e:
                logger.warning(f"Persist save error: {e}")

    # Интервью: если еще не завершено - собираем ответы
    questions = get_interview_questions(state)
    # Специальный шаг согласия для Vlasta: ожидаем "Да"/"да"/"ДА" прежде чем задавать первый вопрос
    if state.get('scenario') == 'Vlasta' and not state.get('consent'):
        normalized = (user_message or '').strip()
        if normalized in ('Да', 'да', 'ДА', 'дА', 'Da', 'Yes', 'yes'):
            state['consent'] = True
            first_q = questions[0]
            await update.message.reply_text(first_q)
            state['conversation_history'].append({"role": "assistant", "content": first_q})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        state.get('scenario') or '',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'assistant',
                        first_q,
                        state.get('free_used', 0),
                        state.get('daily_requests', 0),
                        state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
            if persistence:
                try:
                    state['last_activity_at'] = now_msk_str()
                    persistence.save_user_state(user_id, state)
                except Exception as e:
                    logger.warning(f"Persist save error: {e}")
            return
        else:
            await update.message.reply_text("Пожалуйста, ответь Да или Нет.")
            return

    if state.get('interview_stage', 0) < len(questions):
        # Собираем ответ
        state['interview_answers'].append(user_message)
        state['interview_stage'] += 1
        
        # Сохраняем ответы в Users sheet
        save_interview_answers_to_users(user_id, state)
        
        if state['interview_stage'] < len(questions):
            # Следующий вопрос
            next_q = questions[state['interview_stage']]
            await update.message.reply_text(next_q)
            state['conversation_history'].append({"role": "assistant", "content": next_q})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        state.get('scenario') or '',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'assistant',
                        next_q,
                        state.get('free_used', 0),
                        state.get('daily_requests', 0),
                        state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
        else:
            # Интервью завершено - переходим к AI
            await update.message.reply_text("Отлично! Теперь я готова ответить на твои вопросы. Что тебя интересует?")
            state['conversation_history'].append({"role": "assistant", "content": "Отлично! Теперь я готова ответить на твои вопросы. Что тебя интересует?"})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        state.get('scenario') or '',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'assistant',
                        "Отлично! Теперь я готова ответить на твои вопросы. Что тебя интересует?",
                        state.get('free_used', 0),
                        state.get('daily_requests', 0),
                        state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
        
        # Persist
        if persistence:
            try:
                state['last_activity_at'] = now_msk_str()
                persistence.save_user_state(user_id, state)
            except Exception as e:
                logger.warning(f"Persist save error: {e}")
        return

    # Проверяем лимиты
    scenario_cfg = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
    # Если активна подписка - лимиты отключены
    if is_subscription_active(state):
        pass
    elif not scenario_cfg or scenario_cfg.get('limit_mode') != 'total_free':
        # Поведение по-умолчанию: дневной лимит
        today = datetime.now(MSK_TZ).strftime('%Y-%m-%d')
        if state.get('last_date') != today:
            state['daily_requests'] = 0
            state['last_date'] = today
        
        daily_limit = state.get('custom_limit', 10)
        if state.get('daily_requests', 0) >= daily_limit:
            if not state.get('limit_notified'):
                await update.message.reply_text(
                    f"Дневной лимит исчерпан ({daily_limit} запросов). Попробуйте завтра или обратитесь к администратору."
                )
                state['limit_notified'] = True
                if persistence:
                    try:
                        state['last_activity_at'] = now_msk_str()
                        persistence.save_user_state(user_id, state)
                    except Exception as e:
                        logger.warning(f"Persist save error: {e}")
            return
        
        state['daily_requests'] = state.get('daily_requests', 0) + 1
    else:
        # Сценарий с total_free лимитом
        free_used = state.get('free_used', 0)
        free_limit = scenario_cfg.get('limit_value', 5)
        
        if free_used >= free_limit:
            if not state.get('limit_notified'):
                # Показываем сообщение о лимите
                limit_msg = scenario_cfg.get('limit_message', 'Лимит исчерпан.')
                await update.message.reply_text(limit_msg)
                state['conversation_history'].append({"role": "assistant", "content": limit_msg})
                state['limit_notified'] = True
                
                # Предлагаем оплату
                try:
                    await send_invoice_to_user(context, user_id)
                except Exception as e:
                    logger.warning(f"Auto-offer tg error: {e}")
                try:
                    await send_sbp_link(context, user_id)
                except Exception as e:
                    logger.warning(f"Auto-offer sbp error: {e}")
            return
        elif free_used >= free_limit:
            # Лимит уже показан ранее - просто блокируем доступ без запроса к ИИ
            return

    # Только теперь показываем индикатор размышления, если реально идём к ИИ
    await update.message.reply_text("💭 Думаю...")
    
    # Запрос к AI
    ai_response = await deepseek_request(
        user_message, 
        user_history=state.get('conversation_history'),
        user_data=state
    )
    
    if ai_response:
        await update.message.reply_text(ai_response)
        state['conversation_history'].append({"role": "assistant", "content": ai_response})
        
        # Увеличиваем счетчик бесплатных использований для total_free сценариев
        if scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
            state['free_used'] = state.get('free_used', 0) + 1
            
            # Если достигли лимита - предлагаем оплату
            if state['free_used'] >= scenario_cfg.get('limit_value', 5):
                if not state.get('limit_notified'):
                    limit_msg = scenario_cfg.get('limit_message', 'Лимит исчерпан.')
                    await update.message.reply_text(limit_msg)
                    state['conversation_history'].append({"role": "assistant", "content": limit_msg})
                    state['limit_notified'] = True
                    
                    # Предлагаем оплату
                    try:
                        await send_invoice_to_user(context, user_id)
                    except Exception as e:
                        logger.warning(f"Auto-invoice tg error: {e}")
                    # Параллельно предложим оплату через redirect/СБП (прямая ссылка)
                    try:
                        await send_sbp_link(context, user_id)
                    except Exception as e:
                        logger.warning(f"Auto-invoice sbp error: {e}")
            return
        elif free_used >= free_limit:
            # Лимит уже показан ранее - просто блокируем доступ без запроса к ИИ
            return

    # Только теперь показываем индикатор размышления, если реально идём к ИИ
    await update.message.reply_text("💭 Думаю...")
    
    # Запрос к AI
    ai_response = await deepseek_request(
        user_message, 
        user_history=state.get('conversation_history'),
        user_data=state
    )
    
    if ai_response:
        await update.message.reply_text(ai_response)
        state['conversation_history'].append({"role": "assistant", "content": ai_response})
        
        # Увеличиваем счетчик бесплатных использований для total_free сценариев
        if scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
            state['free_used'] = state.get('free_used', 0) + 1
            
            # Если достигли лимита - предлагаем оплату
            if state['free_used'] >= scenario_cfg.get('limit_value', 5):
                if not state.get('limit_notified'):
                    limit_msg = scenario_cfg.get('limit_message', 'Лимит исчерпан.')
                    await update.message.reply_text(limit_msg)
                    state['conversation_history'].append({"role": "assistant", "content": limit_msg})
                    state['limit_notified'] = True
                    
                    # Предлагаем оплату
                    try:
                        await send_invoice_to_user(context, user_id)
                    except Exception as e:
                        logger.warning(f"Auto-invoice tg error: {e}")
                    # Параллельно предложим оплату через redirect/СБП (прямая ссылка)
                    try:
                        await send_sbp_link(context, user_id)
                    except Exception as e:
                        logger.warning(f"Auto-invoice sbp error: {e}")
    else:
        await update.message.reply_text("Извините, произошла ошибка. Попробуйте позже.")
    
    # Сохраняем ответ в историю
    if history_sheet:
        try:
            history_sheet.append_row([
                user_id,
                state.get('scenario') or '',
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'assistant',
                ai_response or "Ошибка",
                state.get('free_used', 0),
                state.get('daily_requests', 0),
                state.get('interview_stage', 0),
            ])
        except Exception as e:
            logger.warning(f"History write error: {e}")
    
    # Persist
    if persistence:
        try:
            state['last_activity_at'] = now_msk_str()
            persistence.save_user_state(user_id, state)
        except Exception as e:
            logger.warning(f"Persist save error: {e}")

# === АДМИН КОМАНДЫ ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    await update.message.reply_text(f"Пользователей: {len(user_states)}")

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    try:
        user_id = int(context.args[0])
        blocked_users.add(user_id)
        await update.message.reply_text(f"Пользователь {user_id} заблокирован")
    except Exception:
        await update.message.reply_text("Использование: /block <user_id>")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    try:
        user_id = int(context.args[0])
        blocked_users.discard(user_id)
        await update.message.reply_text(f"Пользователь {user_id} разблокирован")
    except Exception:
        await update.message.reply_text("Использование: /unblock <user_id>")

async def admin_setlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    try:
        user_id = int(context.args[0])
        limit = int(context.args[1])
        if user_id in user_states:
            user_states[user_id]['custom_limit'] = limit
            await update.message.reply_text(f"Лимит для {user_id} установлен: {limit}")
        else:
            await update.message.reply_text("Пользователь не найден")
    except Exception:
        await update.message.reply_text("Использование: /setlimit <user_id> <limit>")

async def admin_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    admin_settings['notify_new_users'] = not admin_settings['notify_new_users']
    await update.message.reply_text(f"Уведомления: {'включены' if admin_settings['notify_new_users'] else 'выключены'}")

async def admin_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    admin_settings['echo_user_messages'] = not admin_settings['echo_user_messages']
    await update.message.reply_text(f"Эхо сообщений: {'включено' if admin_settings['echo_user_messages'] else 'выключено'}")

async def admin_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    try:
        user_id = int(context.args[0])
        whitelist_ids.add(user_id)
        await update.message.reply_text(f"Пользователь {user_id} добавлен в whitelist")
    except Exception:
        await update.message.reply_text("Использование: /whitelist <user_id>")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception in handler", exc_info=context.error)

# === ЗАПУСК ===
def main():
    logger.info("Starting MetaPersona Bot...")

    async def run_server():
        # Build application without Updater (custom webhook server)
        application = Application.builder().updater(None).token(BOT_TOKEN).build()

        # Handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CommandHandler("stats", admin_stats))
        application.add_handler(CommandHandler("block", admin_block))
        application.add_handler(CommandHandler("unblock", admin_unblock))
        application.add_handler(CommandHandler("setlimit", admin_setlimit))
        application.add_handler(CommandHandler("notify", admin_notify))
        application.add_handler(CommandHandler("echo", admin_echo))
        application.add_handler(CommandHandler("whitelist", admin_whitelist))
        application.add_error_handler(error_handler)

        # Admin diagnostics
        async def diag_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != ADMIN_CHAT_ID:
                return
            try:
                info = await application.bot.get_webhook_info()
                txt = (
                    f"url: {info.url or '-'}\n"
                    f"has_custom_certificate: {info.has_custom_certificate}\n"
                    f"pending_update_count: {info.pending_update_count}\n"
                    f"ip_address: {getattr(info, 'ip_address', '-') }\n"
                    f"last_error_date: {getattr(info, 'last_error_date', '-') }\n"
                    f"last_error_message: {getattr(info, 'last_error_message', '-') }"
                )
                await update.message.reply_text(f"Webhook info:\n{txt}")
            except Exception as e:
                await update.message.reply_text(f"diag_webhook error: {e}")

        async def reset_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != ADMIN_CHAT_ID:
                return
            try:
                base_url = os.environ.get('WEBHOOK_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL')
                if not base_url:
                    await update.message.reply_text('WEBHOOK_BASE_URL/RENDER_EXTERNAL_URL не задан')
                    return
                url_path = f"/webhook/{BOT_TOKEN}"
                webhook_url = base_url.rstrip('/') + url_path
                await application.bot.set_webhook(webhook_url, drop_pending_updates=True)
                await update.message.reply_text(f"Webhook reset to: {webhook_url}")
            except Exception as e:
                await update.message.reply_text(f"reset_webhook error: {e}")

        application.add_handler(CommandHandler("diag", diag_webhook))
        application.add_handler(CommandHandler("reset", reset_webhook))

        # PreCheckoutQuery handler
        async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.pre_checkout_query
            try:
                await query.answer(ok=True)
            except Exception:
                await query.answer(ok=False, error_message="Ошибка при обработке оплаты. Попробуйте позже.")

        async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            payment = update.message.successful_payment
            
            # Активируем подписку
            until = (datetime.now(MSK_TZ) + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            if user_id in user_states:
                user_states[user_id]['is_subscribed'] = True
                user_states[user_id]['subscription_until'] = until
                user_states[user_id]['limit_notified'] = False
                user_states[user_id]['subscription_end_notified'] = False
                # Обновляем данные в таблице
                update_user_subscription_in_sheet(user_id, user_states[user_id])
                user_states[user_id]['last_payment_id'] = payment.telegram_payment_charge_id
                
                # Persist
                if persistence:
                    try:
                        user_states[user_id]['last_activity_at'] = now_msk_str()
                        persistence.save_user_state(user_id, user_states[user_id], force=True)
                    except Exception as e:
                        logger.warning(f"Persist save error: {e}")
                
                # Отправляем приветственное сообщение
                scenario_cfg = SCENARIOS.get(user_states[user_id].get('scenario'))
                if scenario_cfg and scenario_cfg.get('subscription_welcome'):
                    welcome_msg = scenario_cfg['subscription_welcome']
                    await update.message.reply_text(welcome_msg)
                    user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_msg})
                else:
                    await update.message.reply_text("Оплата получена, доступ активирован.")
                
                # Уведомляем админа
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"💰 Оплата от {user_id}: {payment.total_amount/100} {payment.currency}"
                    )
                except Exception as e:
                    logger.warning(f"Admin payment notify error: {e}")

        application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

        # Callback for external YooKassa Smart Payment (create redirect payment)
        async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.callback_query:
                return
            cq = update.callback_query
            data = cq.data or ''
            if not data.startswith('yk_redirect:'):
                return
            await cq.answer()
            if not (YOOKASSA_ACCOUNT_ID and YOOKASSA_SECRET_KEY and YOOKASSA_RETURN_URL):
                await cq.message.reply_text("Ссылка на оплату временно недоступна")
                return
            try:
                from yookassa import Payment
                uid = cq.from_user.id
                amount = {"value": f"{VLASTA_PRICE_RUB:.2f}", "currency": "RUB"}
                payment = Payment.create({
                    "amount": amount,
                    "confirmation": {
                        "type": "redirect",
                        "return_url": YOOKASSA_RETURN_URL
                    },
                    "capture": True,
                    "description": "Vlasta - доступ на 7 дней",
                    "metadata": {
                        "telegram_user_id": str(uid),
                        "scenario": user_states.get(uid, {}).get('scenario', 'Vlasta')
                    },
                    "receipt": {
                        "items": [
                            {
                                "description": "Доступ к Vlasta на 7 дней",
                                "quantity": "1.0",
                                "amount": amount,
                                "vat_code": VAT_CODE,
                                "payment_mode": "full_payment",
                                "payment_subject": "service"
                            }
                        ],
                        "tax_system_code": TAX_SYSTEM_CODE,
                        "customer": {"email": receipt_email}
                    }
                })
                conf = payment.confirmation
                if conf and conf.return_url:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="Оплатить", url=conf.return_url)]])
                    await cq.message.reply_text("Ссылка на оплату:", reply_markup=kb)
                else:
                    await cq.message.reply_text("Ошибка создания ссылки на оплату")
            except Exception as e:
                logger.warning(f"YooKassa Payment error: {e}")
                await cq.message.reply_text("Ошибка создания ссылки на оплату")

        application.add_handler(CallbackQueryHandler(on_callback))

        # SBP command
        async def sbp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            if uid not in user_states:
                await update.message.reply_text("Сначала запустите бота командой /start")
                return
            await send_sbp_link(context, uid)
        application.add_handler(CommandHandler("sbp", sbp_cmd))

        # Restore states at startup (last 14 days)
        restored = 0
        if persistence:
            try:
                all_states = persistence.load_all_states()
                for uid, st in all_states.items():
                    last_at = st.get('last_activity_at') or st.get('updated_at')
                    ok = True
                    if last_at:
                        try:
                            dt = datetime.strptime(last_at, '%Y-%m-%d %H:%M:%S')
                            if (datetime.now(MSK_TZ) - dt).days > 14:
                                ok = False
                        except Exception:
                            pass
                    if ok:
                        user_states[uid] = st
                        restored += 1
                logger.info(f"Restored {restored} user states")
            except Exception as e:
                logger.warning(f"States restore error: {e}")

        # AioHTTP server setup
        aio = web.Application()
        port = int(os.environ.get('PORT', '8000'))

        # Health endpoint
        async def health(request: web.Request):
            return web.Response(text="OK")

        aio.router.add_get('/health', health)

        # Telegram webhook handler
        async def handle_tg(request: web.Request):
            try:
                body = await request.json()
                await application.update_queue.put(Update.de_json(body, application.bot))
                return web.Response(text="OK")
            except Exception as e:
                logger.warning(f"Webhook error: {e}")
                return web.Response(status=400, text="Error")

        # Short webhook handler (with secret)
        async def handle_tg_short(request: web.Request):
            if WEBHOOK_SECRET:
                secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
                if secret != WEBHOOK_SECRET:
                    return web.Response(status=403, text="Forbidden")
            return await handle_tg(request)

        # YooKassa webhook and return endpoints
        async def handle_yk_webhook(request: web.Request):
            try:
                body = await request.json()
            except Exception:
                return web.Response(status=400, text='bad json')
            try:
                obj = body.get('object') or {}
                if obj.get('status') == 'succeeded':
                    meta = obj.get('metadata') or {}
                    uid_str = meta.get('telegram_user_id')
                    if uid_str and uid_str.isdigit():
                        uid = int(uid_str)
                        st = user_states.get(uid)
                        if st:
                            until = (datetime.now(MSK_TZ) + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                            st['is_subscribed'] = True
                            st['subscription_until'] = until
                            st['limit_notified'] = False
                            st['subscription_end_notified'] = False
                            # Обновляем данные в таблице
                            update_user_subscription_in_sheet(uid, st)
                            if persistence:
                                try:
                                    st['last_activity_at'] = now_msk_str()
                                    persistence.save_user_state(uid, st, force=True)
                                except Exception:
                                    pass
                            # Send welcome
                            scen = SCENARIOS.get(st.get('scenario')) if st.get('scenario') else None
                            msg = scen.get('subscription_welcome') if scen else "Оплата получена, доступ активирован."
                            try:
                                await application.bot.send_message(chat_id=uid, text=msg)
                            except Exception:
                                pass
                return web.Response(text='OK')
            except Exception:
                return web.Response(status=500, text='error')

        async def handle_yk_return(request: web.Request):
            html = """
<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Оплата завершена</title>
<!-- VK Pixel -->
<script>
!function(){var t=document.createElement("script");t.type="text/javascript",t.async=!0,t.src="https://vk.com/js/api/openapi.js?168";var e=document.getElementsByTagName("script")[0];e.parentNode.insertBefore(t,e)}();
</script>
<script>
window.addEventListener('load', function(){
  if (typeof VK !== 'undefined' && VK.Retargeting) {
    try { VK.Retargeting.Init('REPLACE_VK_PIXEL_ID'); VK.Retargeting.Hit(); } catch(e) {}
  }
});
</script>
</head>
<body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial; margin:40px;">
  <h2>Спасибо!</h2>
  <p>Если оплата прошла, доступ уже активирован в чате Telegram.</p>
  <p>Можно закрыть эту страницу.</p>
</body></html>
"""
            html = html.replace('REPLACE_VK_PIXEL_ID', VK_PIXEL_ID)
            return web.Response(text=html, content_type='text/html')

        # Define webhook paths
        base_url = os.environ.get('WEBHOOK_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL') or ''
        url_path = f"/webhook/{BOT_TOKEN}"
        webhook_url = base_url.rstrip('/') + url_path if base_url else ''
        
        aio.router.add_post('/yookassa/webhook', handle_yk_webhook)
        aio.router.add_get('/pay/return', handle_yk_return)
        aio.router.add_post(url_path, handle_tg)          # token path
        aio.router.add_post('/webhook', handle_tg_short)   # short alias path

        # Start app and webhook
        await application.initialize()
        await application.start()
        runner = web.AppRunner(aio)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info('Aiohttp server started')
        # Prefer short path with secret if configured; fallback to token path; don't crash on failure
        short_url = base_url.rstrip('/') + '/webhook'
        # default expected url
        heal_expected_url = short_url if WEBHOOK_SECRET else webhook_url
        try:
            if WEBHOOK_SECRET:
                await application.bot.set_webhook(short_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                heal_expected_url = short_url
                logger.info(f"Webhook set to short path with secret: {short_url}")
            else:
                await application.bot.set_webhook(webhook_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                heal_expected_url = webhook_url
                logger.info(f"Webhook set to token path: {webhook_url}")
        except Exception as e:
            logger.warning(f"Initial set_webhook failed: {e}")
            try:
                await application.bot.set_webhook(webhook_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                heal_expected_url = webhook_url
                logger.info(f"Webhook fallback to token path: {webhook_url}")
            except Exception as e2:
                logger.warning(f"Fallback set_webhook failed: {e2}")
                heal_expected_url = webhook_url
        # Graceful stop support
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(shutdown())

        async def shutdown():
            try:
                heal_task.cancel()
                await heal_task
            except Exception:
                pass
            await application.stop()
            await application.shutdown()
            await runner.cleanup()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Health check task
        async def health_check():
            while True:
                try:
                    await asyncio.sleep(30)
                    info = await application.bot.get_webhook_info()
                    if info.url != heal_expected_url:
                        logger.warning(f"Webhook URL mismatch: expected {heal_expected_url}, got {info.url}")
                        try:
                            if WEBHOOK_SECRET:
                                await application.bot.set_webhook(heal_expected_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                            else:
                                await application.bot.set_webhook(heal_expected_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                            logger.info(f"Webhook restored to: {heal_expected_url}")
                        except Exception as e:
                            logger.warning(f"Webhook restore failed: {e}")
                except Exception as e:
                    logger.warning(f"Health check error: {e}")

        heal_task = asyncio.create_task(health_check())

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
        finally:
            try:
                heal_task.cancel()
                await heal_task
            except Exception:
                pass
            await application.stop()
            await application.shutdown()
            await runner.cleanup()

    try:
        asyncio.run(run_server())
    except Exception as e:
        logger.exception(f"Startup error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
