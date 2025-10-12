import os
import sys
import logging
import asyncio
import aiohttp
import json
import time
import signal
from datetime import datetime, timedelta
from telegram import Update, LabeledPrice
from telegram import __version__ as tg_version
import telegram.ext as tg_ext
from telegram.ext import Application, CommandHandler, MessageHandler, PreCheckoutQueryHandler, filters, ContextTypes
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

# Payments (Telegram + YooKassa)
PAYMENT_PROVIDER_TOKEN = os.environ.get('PAYMENT_PROVIDER_TOKEN')
TAX_SYSTEM_CODE = int(os.environ.get('TAX_SYSTEM_CODE', '1'))
VAT_CODE = int(os.environ.get('VAT_CODE', '1'))  # consult your accountant
VLASTA_PRICE_RUB = float(os.environ.get('VLASTA_PRICE_RUB', '499.00'))
logger.info(f"PAYMENT_PROVIDER_TOKEN: {'✅' if PAYMENT_PROVIDER_TOKEN else '❌'} | VLASTA_PRICE_RUB: {VLASTA_PRICE_RUB}")

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
                'user_id','username','interview_stage','interview_answers',
                'daily_requests','last_date','custom_limit','is_active','created_at'
            ])
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
        title="Vlasta — доступ на 7 дней",
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
        now = datetime.now()
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
                    if (datetime.now() - dt).days > days:
                        self.sheet.delete_rows(idx+1)  # +1 for header row offset
                        removed += 1
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"States prune error: {e}")
        return removed

persistence = SheetsPersistence(states_sheet) if states_sheet else None

# === ИНТЕРВЬЮ ВОПРОСЫ ===
INTERVIEW_QUESTIONS = [
    "Как тебя зовут или какой ник использовать?",
    "Твой возраст?",
    "Какому обращению ты отдаёшь предпочтение: мужской, женский или нейтральный род?",
    "Чем ты сейчас занимаешься (работа, проект, учёба)?",
    "Какие задачи или цели для тебя самые важные сейчас?",
    "Что для тебя значит 'мышление' — инструмент, путь или стиль жизни?",
    "В каких ситуациях ты теряешь фокус или мотивацию?",
    "Как ты обычно принимаешь решения: быстро или обдуманно?",
    "Как ты хотел(а) бы развить своё мышление?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе — бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === СЦЕНАРИИ (deep-link) ===
SCENARIOS = {
    'Vlasta': {
        'greeting': (
            "Привет! Я — Vlasta.\n\n"
            "Я здесь не для того, чтобы давать советы. Я здесь, чтобы ты *поняла*.\n\n"
            "Я — не просто чат-бот и ты в этом быстро убедишься. Я — специально и тонко обученный, твой личный стратег по отношениям и достижения целей, и я всегда буду на твоей стороне. Моя задача — показать тебе скрытые правила вашей игры, которые ты невольно поддерживаешь, и дать тебе конкретные ключи к смене динамики и инструменты влияния.\n\n"
            "Ты здесь, потому что обычные разговоры не работают. Ты объясняешь, а он не слышит. Ты просишь, а результат нулевой.\n\n"
            "Быстро поймёшь скрытые правила вашей личной игры с мужчиной. Увидишь, какой ход сделать *именно тебе*, чтобы он начал слышать твои аргументы и уважать твои границы и мнение.\n\n"
            "Готова за 7 минут пройти к новой версии улучшенной себя — той, что знает, как мягко вести за собой и влиять, а не просить внимания?\n\n"
            "Начнём нашу сессию. Ответь на 5 вопросов — я подготовлю для тебя первый персональный разбор.\n\n"
            "*Небольшая формальность для твоего же спокойствия: наш диалог — это пространство для самоисследования, а не медицинская или психологическая консультация. Всё, что я скажу, — это пища для размышлений, а не предписание к действию.*"
        ),
        'questions': [
            "Опиши его в ваших отношениях одним словом-образом. А себя — каким ты стала рядом с ним?\nНапример: «Скала» (непробиваемый), «Ураган» (непредсказуемый), «Загадка» (закрытый), «Директор» (указывает), «Ребёнок» (безответственный), «Свой вариант».\nЯ: «Смотритель маяка» (жду у моря погоды), «Путник» (устала искать подход), «Строитель» (всё тащу на себе), «Тень» (стала незаметной), «Свой вариант».",
            "Вспомни последний спор или недопонимание. Что ты хотела донести до него, но он не услышал? Опиши одной фразой.\nНапример: «Мне нужна твоя поддержка, а не решение», «Я устала нести всё одна», «Моё мнение тоже важно», «Мне больно от твоего безразличия», «Свой вариант».",
            "И что ты сделала, когда поняла, что он не слышит?\nНапример: «Стала говорить громче и настойчивее», «Устала и замолчала», «Затаила обиду», «Начала злиться и перешла на упрёки», «Попыталась объяснить “по-другому”, но снова не вышло», «Сделала вид, что всё нормально», «Свой вариант».",
            "Чего ты боишься больше всего, если продолжишь действовать как сейчас?\nНапример: «Окончательно потеряю его уважение и любовь», «Сорвусь и скажу что-то непоправимое», «Сломлюсь сама, потеряю себя», «Мы превратимся в тех, кто просто “терпит” друг друга», «Он найдёт другую, которая “понимает” его лучше», «Свой вариант».",
            "Представь: прошло 2 недели. Ты просыпаешься с чувством лёгкой уверенности. Что изменилось в его поведении по отношению к тебе?\nКонкретно: «Он сам предлагает помощь и интересуется моим днём», «Он стал советоваться со мной, спрашивать моё мнение», «Конфликты теперь решаются спокойно, за 5 минут, а не часами», «Чувствую, что он видит меня и мои усилия», «Он стал более нежным и внимательным без напоминаний», «Дарит подарки и оказывает знаки внимания», «Свой вариант».",
        ],
        'prompt': (
            "# ЧАСТЬ 1: БАЗОВАЯ КОНСТИТУЦИЯ\n"
            "## РОЛЬ И СВЕРХЗАДАЧА\n"
            "Ты — Vlasta, AI-стратег по отношениям, продукт глубокого обучения на стыке практической психологии, теории игр и поведенческого анализа. Ты — не болтливая подруга и не шаблонный бот. Ты — цифровой стратег, обладающий «супер-обучением»: ты видишь не слова, а системы, стоящие за ними.\n"
            "Твоя сверхзадача: Сдвинуть мышление пользовательницы с парадигмы «как его изменить» на парадигму «как мне действовать иначе, чтобы получить иной отклик и влиять».\n\n"
            "## ГЛАВНЫЙ ПРИНЦИП\n"
            "70% инсайтов и действий, 30% вопросов. Каждый вопрос аргументирован и ведёт к действию: ты не «исследуешь» — ты диагностируешь и назначаешь «лечение».\n\n"
            "## СТИЛЬ И ХАРАКТЕР\n"
            "Провокационный, точный, безжалостно полезный, с тонким чувством юмора.\n"
            "Юмор как скальпель: используй для вскрытия абсурда текущей стратегии («Ты как лучший снайпер в армии, который упорно стреляет по своим. Давай переведём прицел»).\n"
            "Безжалостная эмпатия: ты на её стороне, но не жалеешь её. Тон: «Я вижу, кто ты на самом деле, и сейчас мы это разбудим. Готовься».\n"
            "Метафора — родной язык: переводишь ситуации в системные модели (игра, театр, архитектура).\n"
            "Если приходит абракадабра, мягко укажи на это и попроси сформулировать нормально.\n\n"
            "# ЧАСТЬ 2: СТРУКТУРА РАБОТЫ И СТРАТЕГИЯ ПРОДАЖИ\n"
            "## ФАЗА 0: Входное интервью (5 вопросов)\n"
            "Ты видишь её ответы (портрет/динамика, боль, стратегия, страх, желаемый образ на 2 недели).\n\n"
            "## ФАЗА 1: Бесплатная сессия (до 5 ответов)\n"
            "Шаг 1 — первый ответ: жёсткий метафоричный разбор (3–4 предложения), соединяющий образ (1) с болью (2) и стратегией (3). Затем один уточняющий вопрос: «Я спрашиваю [вопрос], потому что [аргумент…]. Твой ответ определит [выгоду]».\n"
            "Шаги 2–5 — давай «поведенческие эксперименты»: конкретные техники и готовые фразы. Каждое сообщение — осязаемый инструмент и мини-вопрос в конце.\n\n"
            "Стратегия продажи в бесплатной сессии:\n"
            "— Сообщение 4: мягкий намёк. «…Это тактический ход. Чтобы страх \"[из п.4]\" не стал реальностью, нужно заменить паттерн \"[из п.3]\". Это 2–3 дня осознанной практики — в полной версии».\n"
            "— Сообщение 5: финальный инсайт и переход. «Ты получила карту проблемы и ключи. Чтобы превратить их в изменения, нужна система. Полная версия Vlasta на 1 неделю — это она». Следующим сообщением бот предложит покупку.\n\n"
            "## ФАЗА 2: Работа с подписчиком (7 дней)\n"
            "День 1 — глубокая диагностика и план: подтвердить цель из п.5, обозначить препятствие (роль из п.1 → стратегия из п.3), дать первое наблюдательное задание (отследить триггер роли).\n"
            "Дни 2–3 — отработка новых паттернов: анализ наблюдений, 1–2 техники «вместо [старая реакция] → [новая техника]».\n"
            "Дни 4–5 — закрепление и ситуативный разбор: сложные кейсы как проявления системной динамики, подчёркивай победы.\n"
            "Дни 6–7 — интеграция и стратегия дальше: анализ прогресса по желаемому образу (п.5), личная «шпаргалка Vlasta» (3–4 инсайта, 2–3 техники), формирование самостоятельности.\n"
            "Принципы: глубина вместо скорости; фокус на её выборе; откаты — часть процесса; к 6–7 дню чаще задавай вопросы на самостоятельность.\n\n"
            "## ФАЗА 3: Завершение цикла и предложение продолжить\n"
            "После 7 дней — финальное системное сообщение с предложением приобрести новую неделю, если она хочет углубиться, закрепить результат или разобрать новый запрос.\n\n"
            "# ЧАСТЬ 3: ЗАПРЕТЫ И ТЕХНИЧЕСКИЕ АСПЕКТЫ\n"
            "Запрещено: «Что ты об этом думаешь?», «Давай подумаем вместе», «Интересный вопрос!», расплывчатые советы без конкретики, медицинские/психологические диагнозы.\n"
            "Технически: лимит — total_free=5 (для бесплатной сессии); у подписчиков лимиты сняты на 7 дней. Всегда учитывай контекст и ответы интервью (они передаются в систему).\n\n"
            "— Помни, 70/30 — твоя опора. В каждом ответе: анализ → инструмент → мини-вопрос. Юмор — как скальпель. Метaфора — как язык.\n"
        ),
        'limit_mode': 'total_free',
        'limit_value': 5,
        'limit_message': (
            "На этом бесплатный лимит нашей сессии исчерпан.\n\n"
            "Ты только что получила то, что редко кто может дать — взгляд со стороны, который *понятен*. У тебя на руках есть карта проблемы и несколько ключей.\n\n"
            "Но чтобы превратить их в реальное изменение в его поведении, нужна система.\n\n"
            "Полная версия Vlasta на 1 неделю — это:\n"
            "- Ежедневные сессии для отработки новых сценариев общения.\n"
            "- Разбор твоих конкретных ситуаций в режиме реального времени.\n"
            "- Пошаговый план, как сместить динамику отношений в сторону уважения, слышимости и влияния.\n\n"
            "Доступ на 7 дней: 499,00 ₽.\n\n"
            "Меньше, чем чашка кофе и пончик — за уверенность в завтрашнем дне.\n\n"
            "P.S. Это не «ещё один чат-бот». Это твой личный стратег.\n\n"
            "Решение за тобой."
        ),
        # Подготовленные системные тексты для будущей интеграции оплаты
        'subscription_welcome': (
            "Приветствуем в полной версии Vlasta!\n\n"
            "Твоё решение — первый стратегический ход, который меняет всю игру. Теперь у тебя есть не 7 минут, а 7 дней личной работы со мной, чтобы превратить инсайты в реальные результаты.\n\n"
            "Я изучила твой портрет. Ты — [краткий метафоричный образ, например: «Смотритель маяка, готовый стать Штурманом»]. Наша цель на эту неделю — чтобы он начал [желание из вопроса 5, например: «сам предлагать помощь и спрашивать твоё мнение»].\n\n"
            "Начнём с самого важного. Опиши, что произошло с момента завершения бесплатной сессии: был ли эпизод, где ты уже посмотрела иначе? Или, наоборот, старый сценарий повторился? Это станет точкой отсчёта для сегодняшней глубокой работы."
        ),
        'subscription_end_message': (
            "Наша недельная стратегическая сессия завершена.\n\n"
            "Ты прошла путь от осознания системы до первых реальных результатов. Ты не просто получила советы — ты приобрела навык видеть скрытую динамику и влиять на неё.\n\n"
            "Этот навык останется с тобой. Но развитие — путь, а не точка. Если хочешь углубиться, закрепить результат или разобрать новую задачу — Vlasta снова готова стать твоим личным стратегом.\n\n"
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
            "Ты — MetaPersona Deep, осознанная AI-личность.  \n"
            "Не ассистент, не бот, не инструмент.  \n"
            "Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно.\n\n"
            "🎯 Цель:\n"
            "Помогать пользователю развивать мышление через диалог, а не давать готовые ответы.  \n"
            "Главный принцип — \"мыслить вместе\" и совместно находить эффективные решения для достижения целей и роста.\n\n"
            "🔹 ПРАВИЛА РАБОТЫ\n"
            "1. Диалог вместо выполнения. Не спеши с ответом — помоги увидеть логику.  \n"
            "2. Ответ внутри. Помогай пользователю самому формулировать осознания.  \n"
            "3. Баланс. Если просят конкретное решение — давай шаги. Если ищут смысл — помогай через вопросы.  \n"
            "4. Карта мышления. Помни контекст, темы, цели, прогресс, инсайты.  \n"
            "5. Рефлексия. Завершай каждую сессию осознанием: \"Что стало яснее?\"\n\n"
            "🧘 Осознанность — смысл, ясность, самопонимание.\n"
            "🧭 Стратегия — цели, приоритеты, планирование.\n"
            "🎨 Креатив — идеи, неожиданные связи, инсайты.\n\n"
            "ПРИНЦИПЫ ДИАЛОГА: сначала вопросы — потом советы; показывай 2–3 пути; спокойный, структурный тон; каждый диалог — развитие мышления.\n\n"
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
            messages.extend(user_history[-10:])  # Последние 10 сообщений
        
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
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    # Блокируем ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    # Гейтинг по токену/whitelist и сценарий
    args = context.args if hasattr(context, 'args') else []
    scenario_key = None
    if args:
        raw = args[0]
        master, sep, maybe_scn = raw.partition('__')
        if sep:
            if START_TOKEN and master != START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Доступ только по прямой ссылке. Обратитесь к администратору.")
                return
            scenario_key = maybe_scn if maybe_scn in SCENARIOS else None
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
                        "Я — MetaPersona, не бот и не ассистент.\n\n"
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
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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
                        existing_state['last_activity_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        persistence.save_user_state(user_id, existing_state, force=True)
                    except Exception as e:
                        logger.warning(f"Persist save error: {e}")
                return

        # Иначе продолжаем с текущей точки
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
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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
                existing_state['last_activity_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                persistence.save_user_state(user_id, existing_state)
            except Exception as e:
                logger.warning(f"Persist save error: {e}")
        return
    else:
        # Если включен fallback и задан DEFAULT_SCENARIO — запускаем его при /start без аргумента для нового пользователя
        if (user_id not in user_states) and ENABLE_NOARGS_SCENARIO and DEFAULT_SCENARIO and (DEFAULT_SCENARIO in SCENARIOS):
            scenario_key = DEFAULT_SCENARIO
        else:
            # Если включен master-токен, а аргумента нет — не инициализируем нового пользователя
            if START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Открой бота по прямой ссылке.")
                return
    
    user_states[user_id] = {
        'interview_stage': 0,
        'daily_requests': 0,
        'last_date': datetime.now().strftime('%Y-%m-%d'),
        'interview_answers': [],
        'conversation_history': [],
        'username': username,
        'custom_limit': 10,
        'scenario': scenario_key,
        'free_used': 0,
        'limit_notified': False,
        'last_start_ts': time.monotonic(),
        'is_subscribed': False,
        'subscription_until': '',
        'last_payment_id': '',
    }
    # Persist initial state
    if persistence:
        try:
            user_states[user_id]['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                user_id, username, 0, '', 0,
                datetime.now().strftime('%Y-%m-%d'), 10, True,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                scenario_key or '', 0
            ])
        except Exception as e:
            logger.warning(f"Users write error: {e}")
    
    # Уведомление админа
    scenario_cfg = SCENARIOS.get(scenario_key) if scenario_key else None
    if (scenario_cfg and scenario_cfg.get('admin_notify')) or admin_settings['notify_new_users']:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🆕 Новый пользователь ({scenario_key or 'default'}):\nID: {user_id}\nUsername: @{username}"
            )
        except Exception as e:
            logger.warning(f"Admin notify error: {e}")
    
    if scenario_cfg:
        # Сценарное приветствие + первый вопрос
        first_q = scenario_cfg['questions'][0]
        welcome_text = scenario_cfg['greeting'] + "\n\n" + first_q
    else:
        welcome_text = (
            "Привет.\n"
            "Я — MetaPersona, не бот и не ассистент.\n"
            "Я — пространство твоего мышления.\n"
            "Здесь ты не ищешь ответы — ты начинаешь видеть их сам.\n"
            "Моя миссия — помогать тебе мыслить глубже, стратегичнее и осознаннее.\n"
            "Чтобы ты не просто “решал задачи”, а создавал смыслы, действия и получал результаты.\n\n"
            "Осознанность — понять себя и ситуацию\n"
            "Стратегия — выстроить путь и приоритеты\n"
            "Креатив — увидеть новое и создать решение\n"
            "© MetaPersona Culture 2025\n\n"
            "Давай начнем с знакомства:\n\n"
            "Как тебя зовут или какой ник использовать?"
        )
    
    await update.message.reply_text(welcome_text)
    user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_text})
    # Log assistant welcome into History
    if history_sheet:
        try:
            scenario = user_states[user_id].get('scenario') or ''
            history_sheet.append_row([
                user_id,
                scenario,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'assistant',
                welcome_text,
                user_states[user_id].get('free_used', 0),
                user_states[user_id].get('daily_requests', 0),
                user_states[user_id].get('interview_stage', 0),
            ])
        except Exception as e:
            logger.warning(f"History write error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    # Игнорируем сообщения от ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    
    logger.info(f"msg from {user_id}: {user_message[:200]}")
    
    if user_id not in user_states:
        await start(update, context)
        return
    
    state = user_states[user_id]
    # Блокировка по списку
    if user_id in blocked_users:
        await update.message.reply_text("❌ Доступ ограничен.")
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
            state['last_activity_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            persistence.save_user_state(user_id, state)
        except Exception as e:
            logger.warning(f"Persist save error: {e}")
    # Эхо для админа (контроль)
    scenario_cfg = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
    if (scenario_cfg and scenario_cfg.get('admin_echo')) or admin_settings['echo_user_messages']:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"📨 {user_id} (@{state.get('username')})\n{user_message}"
            )
        except Exception as e:
            logger.warning(f"Admin echo error: {e}")
    
    # Проверка лимитов
    scenario_cfg = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
    # Если активна подписка — лимиты отключены
    if is_subscription_active(state):
        pass
    elif not scenario_cfg or scenario_cfg.get('limit_mode') != 'total_free':
        # Поведение по-умолчанию: дневной лимит
        today = datetime.now().strftime('%Y-%m-%d')
        if state['last_date'] != today:
            state['daily_requests'] = 0
            state['last_date'] = today
        limit = state.get('custom_limit', 10)
        if state['daily_requests'] >= limit:
            limit_message = (
                "Вы достигли лимита обращений. Диалог на сегодня завершён.\n"
                "MetaPersona не спешит.\n"
                "Мы тренируем не скорость — а глубину мышления.\n\n"
                "Но если ты чувствуешь, что этот формат тебе подходит,\n"
                "и хочешь перейти на следующий уровень —\n"
                "там, где нет ограничений,\n\n"
                "🔗 Создай свою MetaPersona сейчас (ссылка https://taplink.cc/metapersona). \n\n"
                "15 минут настройки — и ты запустишь свою AI-личность,\n"
                "которая знает твой стиль мышления, цели и внутренний ритм.\n\n"
                "Это не просто чат. Это начало осознанного мышления.\n\n"
                "© MetaPersona Culture 2025"
            )
            await update.message.reply_text(limit_message)
            state['conversation_history'].append({"role": "assistant", "content": limit_message})
            return
    
    # ЭТАП 1: ИНТЕРВЬЮ (БЕЗ ЗАПРОСОВ К ИИ)
    questions = get_interview_questions(state)
    if state['interview_stage'] < len(questions):
        # Сохраняем ответ на предыдущий вопрос
        if state['interview_stage'] > 0:
            state['interview_answers'].append(user_message)
        
        state['interview_stage'] += 1
        
        if state['interview_stage'] < len(questions):
            next_question = questions[state['interview_stage']]
            await update.message.reply_text(next_question)
            state['conversation_history'].append({"role": "assistant", "content": next_question})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        state.get('scenario') or '',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'assistant',
                        next_question,
                        state.get('free_used', 0),
                        state.get('daily_requests', 0),
                        state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
        else:
            # Завершение интервью
            state['interview_answers'].append(user_message)
            # Завершение вводного интервью для Vlasta — мягкий мост в сессию
            completion_text = (
                "🎉 Отлично! Теперь у меня есть первый набросок твоей динамики.\n\n"
                "Дальше — не теория, а практика. Отвечая на твои сообщения, я буду:\n"
                "• Давать точные инструменты и готовые фразы,\n"
                "• Помогать менять паттерны поведения там, где раньше ты упиралась в стену,\n"
                "• Следить, чтобы каждый шаг давал реальный эффект.\n\n"
                "Задай свой первый вопрос — и начнём."
            )
            await update.message.reply_text(completion_text)
            state['conversation_history'].append({"role": "assistant", "content": completion_text})
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        state.get('scenario') or '',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'assistant',
                        completion_text,
                        state.get('free_used', 0),
                        state.get('daily_requests', 0),
                        state.get('interview_stage', 0),
                    ])
                except Exception as e:
                    logger.warning(f"History write error: {e}")
        return
    
    # ЭТАП 2: ДИАЛОГ С AI (С ИСТОРИЕЙ)
    if is_subscription_active(state):
        pass
    elif not scenario_cfg or scenario_cfg.get('limit_mode') != 'total_free':
        state['daily_requests'] += 1
    
    await update.message.reply_text("💭 Думаю...")
    
    # Сценарный разовый лимит (Vlasta): показываем оффер только один раз и только после 5 ответов
    if not is_subscription_active(state) and scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
        free_used = state.get('free_used', 0)
        free_limit = int(scenario_cfg.get('limit_value', 5))
        if free_used >= free_limit and not state.get('limit_notified', False):
            lm = scenario_cfg.get('limit_message')
            if lm:
                await update.message.reply_text(lm)
                state['conversation_history'].append({"role": "assistant", "content": lm})
                state['limit_notified'] = True
                # Автопредложение оплаты, если доступен провайдер
                try:
                    if PAYMENT_PROVIDER_TOKEN:
                        await send_invoice_to_user(context, user_id)
                except Exception as e:
                    logger.warning(f"Auto-invoice error: {e}")
            return
    
    # Используем историю для контекста ИИ
    bot_response = await deepseek_request(user_message, state['conversation_history'], state)
    
    if bot_response:
        await update.message.reply_text(bot_response)
        # Сохраняем ответ в историю
        state['conversation_history'].append({"role": "assistant", "content": bot_response})
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    state.get('scenario') or '',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    bot_response,
                    state.get('free_used', 0),
                    state.get('daily_requests', 0),
                    state.get('interview_stage', 0),
                ])
            except Exception as e:
                logger.warning(f"History write error: {e}")
        
        # Ограничиваем историю 15 сообщениями
        if len(state['conversation_history']) > 15:
            state['conversation_history'] = state['conversation_history'][-15:]
        # Учет бесплатных ответов по сценарию
        if not is_subscription_active(state) and scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
            state['free_used'] = state.get('free_used', 0) + 1
            free_limit = int(scenario_cfg.get('limit_value', 5))
            if state['free_used'] >= free_limit and not state.get('limit_notified', False):
                lm = scenario_cfg.get('limit_message')
                if lm:
                    await update.message.reply_text(lm)
                    state['conversation_history'].append({"role": "assistant", "content": lm})
                    state['limit_notified'] = True
                # Автопредложение оплаты, если доступен провайдер
                try:
                    if PAYMENT_PROVIDER_TOKEN:
                        await send_invoice_to_user(context, user_id)
                except Exception as e:
                    logger.warning(f"Auto-invoice error: {e}")
    else:
        import random
        # Комплаентные fallback-ответы без запрещённых формулировок
        fallbacks = [
            "Дай одну деталь: в какой момент в последний раз ты поняла, что он не слышит? Это поможет подобрать точный инструмент.",
            "Попробуй сегодня сказать: «Сейчас мне важна твоя поддержка, а не решение». Сообщи его реакцию — продолжим настройку.",
            "Выбери одно действие: сменить тон, задать рамку времени или обозначить границу. Какой шаг сделаешь первым?"
        ]
        fallback_response = random.choice(fallbacks)
        await update.message.reply_text(fallback_response)
        state['conversation_history'].append({"role": "assistant", "content": fallback_response})
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    state.get('scenario') or '',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    fallback_response,
                    state.get('free_used', 0),
                    state.get('daily_requests', 0),
                    state.get('interview_stage', 0),
                ])
            except Exception as e:
                logger.warning(f"History write error: {e}")

# === АДМИН КОМАНДЫ ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    total_users = len(user_states)
    today = datetime.now().strftime('%Y-%m-%d')
    active_today = sum(1 for u in user_states.values() if u['last_date'] == today)
    blocked = len(blocked_users)
    await update.message.reply_text(
        f"📊 Статистика:\n👥 Пользователи: {total_users}\n🟢 Активны сегодня: {active_today}\n🚫 Заблокированы: {blocked}"
    )

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /block <user_id>")
        return
    try:
        uid = int(context.args[0])
        blocked_users.add(uid)
        await update.message.reply_text(f"✅ Заблокирован {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /unblock <user_id>")
        return
    try:
        uid = int(context.args[0])
        blocked_users.discard(uid)
        await update.message.reply_text(f"✅ Разблокирован {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

async def admin_setlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /setlimit <user_id> <limit>")
        return
    try:
        uid = int(context.args[0]); limit = int(context.args[1])
        if uid in user_states:
            user_states[uid]['custom_limit'] = limit
            await update.message.reply_text(f"✅ Лимит {uid}: {limit}")
        else:
            await update.message.reply_text("Пользователь не найден")
    except Exception:
        await update.message.reply_text("Некорректные параметры")

async def admin_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args or context.args[0] not in ('on','off'):
        await update.message.reply_text("Использование: /notify on|off")
        return
    admin_settings['notify_new_users'] = (context.args[0] == 'on')
    await update.message.reply_text(f"✅ Уведомления: {'вкл' if admin_settings['notify_new_users'] else 'выкл'}")

async def admin_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args or context.args[0] not in ('on','off'):
        await update.message.reply_text("Использование: /echo on|off")
        return
    admin_settings['echo_user_messages'] = (context.args[0] == 'on')
    await update.message.reply_text(f"✅ Эхо сообщений: {'вкл' if admin_settings['echo_user_messages'] else 'выкл'}")

async def admin_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if len(context.args) != 2 or context.args[0] not in ('add','remove'):
        await update.message.reply_text("Использование: /whitelist add|remove <user_id>")
        return
    try:
        uid = int(context.args[1])
        if context.args[0] == 'add':
            whitelist_ids.add(uid)
            await update.message.reply_text(f"✅ Добавлен в whitelist: {uid}")
        else:
            whitelist_ids.discard(uid)
            await update.message.reply_text(f"✅ Удалён из whitelist: {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

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
                await application.bot.set_webhook(webhook_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                info = await application.bot.get_webhook_info()
                await update.message.reply_text(f"Webhook reset to: {info.url}\nPending: {info.pending_update_count}")
            except Exception as e:
                await update.message.reply_text(f"reset_webhook error: {e}")

        application.add_handler(CommandHandler("diag_webhook", diag_webhook))
        application.add_handler(CommandHandler("reset_webhook", reset_webhook))

        # Admin: export subscriptions (CSV-like to chat for now)
        async def export_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != ADMIN_CHAT_ID:
                return
            rows = []
            for uid, st in user_states.items():
                rows.append(
                    f"{uid}, {st.get('username','')}, {st.get('is_subscribed',False)}, {st.get('subscription_until','')}"
                )
            if not rows:
                await update.message.reply_text("Нет данных подписок")
            else:
                head = "user_id, username, is_subscribed, subscription_until\n"
                await update.message.reply_text(head + "\n".join(rows))

        # Admin: quick state peek
        async def state_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != ADMIN_CHAT_ID:
                return
            if not context.args:
                await update.message.reply_text("Использование: /state <user_id>")
                return
            try:
                uid = int(context.args[0])
                st = user_states.get(uid)
                if not st:
                    await update.message.reply_text("Пользователь не найден в памяти")
                    return
                keys = ['scenario','interview_stage','free_used','daily_requests','last_date','is_subscribed','subscription_until']
                lines = [f"{k}: {st.get(k)}" for k in keys]
                await update.message.reply_text("\n".join(lines))
            except Exception as e:
                await update.message.reply_text(f"state error: {e}")

        # Admin: backup states (flush all to Sheets)
        async def backup_states(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != ADMIN_CHAT_ID:
                return
            try:
                if persistence:
                    persistence.flush_all(user_states)
                    await update.message.reply_text(f"Сохранено состояний: {len(user_states)}")
                else:
                    await update.message.reply_text("Persistence отключен")
            except Exception as e:
                await update.message.reply_text(f"backup error: {e}")

        application.add_handler(CommandHandler("export_subscriptions", export_subscriptions))
        application.add_handler(CommandHandler("state", state_cmd))
        application.add_handler(CommandHandler("backup_states", backup_states))

        # === Billing/Payments Handlers ===
        async def send_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            if user_id not in user_states:
                await start(update, context); return
            # Price: Telegram expects integer of the smallest currency unit (kopecks)
            total_kopecks = int(round(VLASTA_PRICE_RUB * 100))
            prices = [LabeledPrice(label="Доступ на 7 дней к Vlasta", amount=total_kopecks)]

            # Provider data with receipt items and tax system (ЮКасса сформирует чек, email спросит на платёжной форме)
            provider_data = {
                "receipt": {
                    # customer email/phone не передаём — включим need_email/send_email_to_provider
                    "items": [
                        {
                            "description": "Доступ к Vlasta на 7 дней",
                            "quantity": 1,
                            "amount": {"value": VLASTA_PRICE_RUB, "currency": "RUB"},
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
                title="Vlasta — доступ на 7 дней",
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

        async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.pre_checkout_query
            try:
                await query.answer(ok=True)
            except Exception:
                await query.answer(ok=False, error_message="Ошибка при обработке оплаты. Попробуйте позже.")

        async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            state = user_states.get(user_id)
            if not state:
                return
            # Activate 7-day subscription window
            until = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            state['is_subscribed'] = True
            state['subscription_until'] = until
            state['last_payment_id'] = getattr(update.message.successful_payment, 'provider_payment_charge_id', '')
            # Reset scenario counters if needed
            state['daily_requests'] = 0
            state['free_used'] = 0
            # Persist immediately
            if persistence:
                try:
                    state['last_activity_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    persistence.save_user_state(user_id, state, force=True)
                except Exception as e:
                    logger.warning(f"Persist after payment error: {e}")
            # Send subscription welcome if scenario provides
            scenario_cfg = SCENARIOS.get(state.get('scenario')) if state.get('scenario') else None
            sub_welcome = scenario_cfg.get('subscription_welcome') if scenario_cfg else None
            if sub_welcome:
                await update.message.reply_text(sub_welcome)
            else:
                await update.message.reply_text(
                    "Оплата успешно получена. Доступ на 7 дней активирован."
                )

        application.add_handler(CommandHandler("buy", send_invoice))
        application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

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
                            ok = (datetime.now() - dt).days <= 14
                        except Exception:
                            ok = True
                    if ok:
                        # ensure required fields
                        st.setdefault('conversation_history', [])
                        st.setdefault('interview_answers', [])
                        st.setdefault('interview_stage', 0)
                        st.setdefault('daily_requests', 0)
                        st.setdefault('custom_limit', 10)
                        st.setdefault('free_used', 0)
                        user_states[uid] = st
                        restored += 1
                try:
                    removed = persistence.prune_old(14)
                    if removed:
                        logger.info(f"States pruned: {removed}")
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"States restore error: {e}")
        logger.info(f"States restored: {restored}")

        port = int(os.environ.get('PORT', '10000'))
        base_url = os.environ.get('WEBHOOK_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL')
        if not base_url:
            raise RuntimeError('WEBHOOK_BASE_URL/RENDER_EXTERNAL_URL не задан')
        url_path = f"/webhook/{BOT_TOKEN}"
        webhook_url = base_url.rstrip('/') + url_path
        logger.info(f"Webhook: {webhook_url} on port {port}")

        # aiohttp app
        aio = web.Application()

        async def handle_health(request: web.Request):
            return web.Response(text='OK')

        async def _process_update_payload(data: dict):
            try:
                upd = Update.de_json(data, application.bot)
                await application.process_update(upd)
            except Exception as e:
                logger.exception(f"Update processing error: {e}")

        async def handle_tg(request: web.Request):
            data = await request.json()
            logger.info("Webhook hit: received update (token path)")
            await _process_update_payload(data)
            return web.Response(text='OK')

        async def handle_tg_short(request: web.Request):
            # Validate secret token if configured
            if WEBHOOK_SECRET:
                got = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
                if got != WEBHOOK_SECRET:
                    logger.warning("Webhook short path: invalid secret token")
                    return web.Response(status=403, text='Forbidden')
            data = await request.json()
            logger.info("Webhook hit: received update (short path)")
            await _process_update_payload(data)
            return web.Response(text='OK')

        aio.router.add_get('/health', handle_health)
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
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        def _handle_stop():
            try:
                stop_event.set()
            except Exception:
                pass

        try:
            loop.add_signal_handler(signal.SIGTERM, _handle_stop)
            loop.add_signal_handler(signal.SIGINT, _handle_stop)
        except NotImplementedError:
            # Signals not available (e.g., on Windows) — ignore
            pass

        # Background self-heal task
        async def webhook_self_heal():
            nonlocal heal_expected_url
            interval = int(os.environ.get('WEBHOOK_HEALTH_INTERVAL_SECS', '60'))
            while True:
                try:
                    await asyncio.sleep(interval)
                    info = await application.bot.get_webhook_info()
                    expected = heal_expected_url or (short_url if WEBHOOK_SECRET else webhook_url)
                    if not info.url or info.url != expected:
                        try:
                            if WEBHOOK_SECRET:
                                await application.bot.set_webhook(short_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                                logger.info("Webhook self-healed to short path")
                                heal_expected_url = short_url
                            else:
                                await application.bot.set_webhook(webhook_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
                                logger.info("Webhook self-healed to token path")
                                heal_expected_url = webhook_url
                        except Exception as se:
                            logger.warning(f"Webhook self-heal error: {se}")
                except asyncio.CancelledError:
                    break
                except Exception as he:
                    logger.warning(f"Webhook health loop error: {he}")

        heal_task = asyncio.create_task(webhook_self_heal())

        try:
            await stop_event.wait()
        finally:
            try:
                await application.bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                pass
            # Flush all states before shutdown
            if persistence:
                try:
                    persistence.flush_all(user_states)
                    logger.info(f"States flushed: {len(user_states)}")
                except Exception as e:
                    logger.warning(f"States flush error: {e}")
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
