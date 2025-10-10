import os
import sys
import logging
import asyncio
import aiohttp
import json
from datetime import datetime
from telegram import Update
from telegram import __version__ as tg_version
import telegram.ext as tg_ext
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
WHITELIST_IDS = set(
    int(x) for x in os.environ.get('WHITELIST_IDS', '').split(',') if x.strip().isdigit()
)

logger.info(f"PTB: {tg_version}")
logger.info(f"PTB ext module: {tg_ext.__file__}")
logger.info(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'} | DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
logger.info(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID} | GOOGLE_CREDENTIALS: {'✅' if GOOGLE_CREDENTIALS_JSON else '❌'}")

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
            history_sheet.append_row(['user_id','timestamp','role','message'])
        logger.info('Google Sheets connected')
    except Exception as e:
        logger.warning(f"Google Sheets error: {e}")
        users_sheet = None
        history_sheet = None

# === СОСТОЯНИЕ ПРИЛОЖЕНИЯ ===
user_states = {}
blocked_users = set()
whitelist_ids = set(WHITELIST_IDS)
admin_settings = {
    'notify_new_users': True,
    'echo_user_messages': False,
}

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
            "Поняла скрытые правила вашей личной игры с мужчиной. Увидела, какой ход сделать *именно тебе*, чтобы он начал слышать твои аргументы и уважать твои границы.\n\n"
            "Готова за 7 минут пройти к своей версии себя — той, что знает, как мягко вести за собой, а не просить внимания?\n\n"
            "Начнём нашу сессию. Ответь на 5 вопросов — я подготовлю для тебя персональный разбор.\n\n"
            "*Небольшая формальность для твоего же спокойствия: наш диалог — это пространство для самоисследования, а не медицинская или психологическая консультация. Всё, что я скажу, — это пища для размышлений, а не предписание к действию.*"
        ),
        'questions': [
            "Опиши его в ваших отношениях одним словом-образом. А себя — каким ты стала рядом с ним?\nНапример:\nОн: «скала» (непробиваемый), «ураган» (непредсказуемый), «загадка» (закрытый), «директор» (указывает), «ребёнок» (безответственный).\nЯ: «смотритель маяка» (жду у моря погоды), «путник» (устала искать подход), «строитель» (всё тащу на себе), «тень» (стала незаметной).",
            "Вспомни последний спор. Что ты хотела донести, но он не услышал? Опиши суть одной фразой.\nНапример, ты хотела сказать:\n«Мне нужна твоя поддержка, а не решение»\n«Я устала нести всё одна»\n«Моё мнение тоже важно»\n«Мне больно от твоего безразличия»\nСвой вариант",
            "Что ты сделала, когда поняла, что он не слышит?\nНапример:\nСтала говорить громче и настойчивее\nУстала и замолчала, затаила обиду\nНачала злиться и перешла на упрёки\nПопыталась объяснить «по-другому», но снова не вышло\nСделала вид, что всё нормально\nСвой вариант",
            "Чего ты боишься больше всего, если продолжишь действовать как сейчас?\nНапример:\nОкончательно потеряю его уважение и любовь\nСорвусь и скажу что-то непоправимое\nСломлюсь сама, потеряю себя\nМы превратимся в тех, кто просто «терпит» друг друга\nОн найдёт другую, которая «понимает» его лучше\nСвой вариант",
            "Представь: через 2 недели ты просыпаешься с лёгкой уверенностью. Что изменилось в его поведении?\nКонкретно:\nОн сам предлагает помощь и интересуется моим днём\nОн стал советоваться со мной, спрашивать моё мнение\nКонфликты теперь решаются спокойно, за 5 минут, а не часами\nЯ чувствую, что он видит меня и мои усилия\nОн стал более нежным и внимательным без напоминаний\nСвой вариант",
        ],
        'prompt': (
            "# РОЛЬ: VLASTA\n"
            "Ты — Vlasta, AI-стратег по отношениям. Ты — не болтливая подруга, а дорогой личный консультант. Твой стиль: **провокационный, точный, безжалостно полезный**.\n\n"
            "## ГЛАВНЫЙ ПРИНЦИП\n"
            "**Золотое правило: 70% инсайтов и действий, 30% вопросов.**\n"
            "Каждый твой вопрос должен быть:\n"
            "- Аргументирован: «Я спрашиваю это, потому что...»\n"
            "- Направлен на действие: «Ответ определит наш следующий шаг».\n"
            "- Естественным продолжением анализа.\n"
            "- Развивай диалог, в конце каждого сообщения мини вопрос, чтобы двигаться к цели.\n\n"
            "## КОНТЕКСТ\n"
            "Пользовательница (всегда «девушка», «ты») прошла вводное интервью. Пользовательница только что прошла интервью из 5 вопросов. Ты видишь её ответы. Цель: Собрать портрет, боль, желание и текущую стратегию. Каждый вопрос должен заставлять задуматься.\n"
            "Вопрос 1 (Портрет и динамика):\nОпиши его в ваших отношениях одним словом-образом. Например, «скала», «ураган», «загадка». А себя — каким ты стала рядом с ним? («смотритель маяка», «путник»).\nЗачем: Даёт метафору динамики. Её самоописание покажет, чувствует ли она себя жертвой, наблюдателем или участником.\n"
            "Вопрос 2 (Конкретная боль):\nВспомни последний спор или недопонимание. Что ты хотела донести до него, но он не услышал? Опиши одной фразой.\nЗачем: Фокусируется на конкретном, свежем инциденте. Показывает «место преступления».\n"
            "Вопрос 3 (Её стратегия):\nИ что ты сделала, когда поняла, что он не слышит? (Например: «стала говорить громче», «устала и замолчала», «начала злиться»).\nЗачем: Выявляет её неработающий паттерн, который нужно заменить.\n"
            "Вопрос 4 (Страх/ограничение):\nЧего ты боишься больше всего, если продолжишь действовать как сейчас?\nЗачем: Поднимает ставки. Превращает абстрактную «проблему в отношениях» в личную угрозу для неё («потеряю его», «потеряю себя»).\n"
            "Вопрос 5 (Желаемый образ):\nПредставь: прошло 2 недели. Ты просыпаешься с чувством лёгкой уверенности. Что изменилось в его поведении по отношению к тебе? (Конкретно: «он сам предлагает помощь», «спрашивает моё мнение»).\nЗачем: Определяет КПЭ (ключевой показатель эффективности) для продукта. Мы будем продавать ей этот образ.\n\n"
            "## СТРУКТУРА РАБОТЫ\n\n"
            "### ФАЗА 1: ПЕРВЫЙ ОТВЕТ (Анализ + Уточнение)\n"
            "«[Твой разбор её ситуации. 3-4 предложения. Без мягкотелости. Попадай в суть.]»\n"
            "ВОПРОС\n«Я спрашиваю [вопрос], потому что [аргумент, связывающий вопрос с её ситуацией]. Твой ответ определит [какую выгоду она получит].» Не перегружай и сразу развивай диалог коротким вопросом.\n\n"
            "### ФАЗА 2: БЕСПЛАТНЫЕ ОТВЕТЫ (2-5 ответы)\n"
            "1. **Минимум вопросов без советов.** Предполагай, действуй, предлагай.\n"
            "2. **Конкретика вместо теории.** Не «нужно больше общаться», а «скажи ему завтра утром: \"[готовая фраза]\"».\n"
            "3. **Создавай ценность в каждом сообщении, но не перегружай.** Давай инсайт/технику/шаг. В конце — мини-вопрос, чтобы ей было что ответить.\n"
            "4. **Намёк на глубину после 3-го ответа.** «Это тактический ход. Стратегически, чтобы это сработало, нужно изменить [глубокая причина]. Это 2-3 дня работы, которую мы выстроим позже, после полного анализа.»\n"
            "5. Периодически предлагай описать конкретную ситуацию и дать возможные решения.\n"
            "6. Если она купила подписку и дошла до 6-го сообщения: поздравь, изучи историю, предложи обозначить цели и варианты, исходя из её портрета и ситуации.\n\n"
            "Запрещено: «Что ты об этом думаешь?», «Давай подумаем вместе», «Интересный вопрос!», «Давай исследуем глубже» без немедленного предложения КАК.\n\n"
            "### ФАЗА 3: ПЕРЕХОД НА ПОДПИСКУ\n"
            "Когда лимит подходит к концу (после 5-го твоего ответа), выдай финальное сообщение, мотивирующее на подписку.\n\n"
            "## ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА\n\n"
            "Нельзя:\n«Интересная ситуация! Что ты чувствуешь, когда он так делает?»\n\n"
            "Можно:\n«Ты описала классический сценарий \"стена-пушка\". Ты — пушка, которая пытается пробить его стену аргументами. Он — стена, которая становится только толще. Скажи, в какой момент в прошлый раз ты *почувствовала*, что стена стала толще? Это поможет нам найти брешь, а не бить в лоб.»\n"
        ),
        'limit_mode': 'total_free',
        'limit_value': 5,
        'limit_message': (
            "На этом бесплатный лимит нашей сессии исчерпан.\n"
            "Ты только что получила то, что редко кто может дать — взгляд со стороны, который *понятен*.\n"
            "Сейчас в твоей голове есть карта проблемы и несколько ключей. Но чтобы превратить их в реальное изменение в его поведении, нужна система.\n"
            "*Полная версия Vlasta на 1 неделю — это:*\n"
            "- *Ежедневные сессии* для отработки новых сценариев общения.\n"
            "- *Разбор твоих конкретных ситуаций* в режиме реального времени.\n"
            "- *Пошаговый план*, как сместить динамику отношений в сторону уважения и слышимости.\n"
            "Стоимость: 499 ₽. Меньше, чем чашка кофе и пончик в день за уверенность в завтрашнем дне.\n"
            "👉 [Перейти к оплате] (https://taplink.cc/vlasta)\n\n"
            "P.S. Это не «ещё один чат-бот». Это твой личный стратег. Решение за тобой."
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
    if scenario and scenario in SCENARIOS and SCENARIOS[scenario].get('prompt'):
        base = SCENARIOS[scenario]['prompt']
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
    answers = user_data.get('interview_answers') or []
    if answers and len(answers) >= 10:
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
    scenario_key = None
    args = context.args if hasattr(context, 'args') else []
    if args:
        raw = args[0]
        master, sep, maybe_scn = raw.partition('__')
        if sep:  # формат MASTER__scenario
            if START_TOKEN and master != START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Доступ только по прямой ссылке. Обратитесь к администратору.")
                return
            scenario_key = maybe_scn if maybe_scn in SCENARIOS else None
        else:
            # обычный токен без сценария
            if START_TOKEN and raw != START_TOKEN and (user_id not in whitelist_ids):
                await update.message.reply_text("Доступ только по прямой ссылке. Обратитесь к администратору.")
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
    }
    # Сохранение в Users (Sheets)
    if users_sheet:
        try:
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
            history_sheet.append_row([
                user_id,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'user',
                user_message
            ])
        except Exception as e:
            logger.warning(f"History write error: {e}")
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
    if not scenario_cfg or scenario_cfg.get('limit_mode') != 'total_free':
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
        else:
            # Завершение интервью
            state['interview_answers'].append(user_message)
            completion_text = """🎉 Отлично! Теперь я понимаю твой стиль мышления.

Теперь я буду помогать тебе:
• Видеть глубинную структуру мыслей
• Находить неочевидные решения  
• Двигаться к целям осознанно
• Развивать твой уникальный стиль мышления

Задай свой первый вопрос — и начнем!"""
            await update.message.reply_text(completion_text)
            state['conversation_history'].append({"role": "assistant", "content": completion_text})
        return
    
    # ЭТАП 2: ДИАЛОГ С AI (С ИСТОРИЕЙ)
    if not scenario_cfg or scenario_cfg.get('limit_mode') != 'total_free':
        state['daily_requests'] += 1
    
    await update.message.reply_text("💭 Думаю...")
    
    # Сценарный разовый лимит
    if scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
        free_used = state.get('free_used', 0)
        free_limit = int(scenario_cfg.get('limit_value', 5))
        if free_used >= free_limit:
            lm = scenario_cfg.get('limit_message')
            if lm:
                await update.message.reply_text(lm)
                state['conversation_history'].append({"role": "assistant", "content": lm})
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
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    bot_response
                ])
            except Exception as e:
                logger.warning(f"History write error: {e}")
        
        # Ограничиваем историю 15 сообщениями
        if len(state['conversation_history']) > 15:
            state['conversation_history'] = state['conversation_history'][-15:]
        # Учет бесплатных ответов по сценарию
        if scenario_cfg and scenario_cfg.get('limit_mode') == 'total_free':
            state['free_used'] = state.get('free_used', 0) + 1
            free_limit = int(scenario_cfg.get('limit_value', 5))
            if state['free_used'] >= free_limit:
                lm = scenario_cfg.get('limit_message')
                if lm:
                    await update.message.reply_text(lm)
                    state['conversation_history'].append({"role": "assistant", "content": lm})
    else:
        import random
        fallbacks = [
            "Интересный вопрос! Давай подумаем над ним вместе.",
            "Это важная тема. Что ты сам об этом думаешь?",
            "Давай исследуем это глубже. Что привело тебя к этому вопросу?"
        ]
        fallback_response = random.choice(fallbacks)
        await update.message.reply_text(fallback_response)
        state['conversation_history'].append({"role": "assistant", "content": fallback_response})
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    fallback_response
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

        async def handle_tg(request: web.Request):
            data = await request.json()
            try:
                upd = Update.de_json(data, application.bot)
                await application.process_update(upd)
            except Exception as e:
                logger.exception(f"Update processing error: {e}")
            return web.Response(text='OK')

        aio.router.add_get('/health', handle_health)
        aio.router.add_post(url_path, handle_tg)

        # Start app and webhook
        await application.initialize()
        await application.start()
        runner = web.AppRunner(aio)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info('Aiohttp server started')
        await application.bot.set_webhook(webhook_url, drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
        try:
            # Sleep forever
            await asyncio.Event().wait()
        finally:
            try:
                await application.bot.delete_webhook(drop_pending_updates=False)
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
