import os
import sys
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === КОНФИГУРАЦИЯ ===
print("=== META PERSONA DEEP BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '8413337220')  # Ваш ID для уведомлений

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === WHITELIST ПОЛЬЗОВАТЕЛЕЙ ===
ALLOWED_USERS = {
    '8413337220',  # Ваш ID
    # Добавляйте сюда ID разрешенных пользователей
}

# === HEALTH SERVER ===
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args): pass

def start_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    print("✅ Health server started")

start_health_server()

# === НАСТРОЙКА ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            interview_completed BOOLEAN DEFAULT FALSE,
            interview_stage INTEGER DEFAULT 0,
            user_name TEXT,
            daily_requests INTEGER DEFAULT 0,
            last_request_date DATE,
            context_created BOOLEAN DEFAULT FALSE,
            is_blocked BOOLEAN DEFAULT FALSE,
            custom_limit INTEGER DEFAULT 10,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS interview_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question TEXT,
            answer TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_buffer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Настройки бота
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            notifications_enabled BOOLEAN DEFAULT TRUE,
            whitelist_enabled BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Инициализация настроек
    cursor.execute('INSERT OR IGNORE INTO bot_settings (id) VALUES (1)')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

# === ИНТЕРВЬЮ ВОПРОСЫ ===
INTERVIEW_QUESTIONS = [
    "Как тебя зовут или какой ник использовать?",
    "Твой возраст?",
    "Какому обращению ты отдаёшь предпочтение: мужской, женский или нейтральный род?",
    "Чем ты сейчас занимаешься (работа, проект, учёба или предложи свой вариант)?",
    "Какие задачи или цели для тебя самые важные сейчас?",
    "Что для тебя значит 'мышление' — инструмент, путь или стиль жизни?",
    "В каких ситуациях ты теряешь фокус или мотивацию?",
    "Как ты обычно принимаешь решения: быстро или обдуманно?",
    "Как ты хотел(а) бы развить своё мышление — стратегически, глубже, креативнее, свой вариант?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе — бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен: философский, дружеский, менторский или структурный?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === АДМИН ФУНКЦИИ ===
async def send_admin_notification(application, message):
    """Отправить уведомление админу"""
    try:
        settings = get_bot_settings()
        if settings and settings[0]:  # notifications_enabled
            await application.bot.send_message(
                chat_id=ADMIN_CHAT_ID, 
                text=f"🔔 {message}"
            )
    except Exception as e:
        print(f"❌ Ошибка отправки уведомления: {e}")

def get_bot_settings():
    """Получить настройки бота"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT notifications_enabled, whitelist_enabled FROM bot_settings WHERE id = 1')
    settings = cursor.fetchone()
    conn.close()
    return settings

def update_bot_settings(notifications=None, whitelist=None):
    """Обновить настройки бота"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    if notifications is not None:
        cursor.execute('UPDATE bot_settings SET notifications_enabled = ? WHERE id = 1', (notifications,))
    if whitelist is not None:
        cursor.execute('UPDATE bot_settings SET whitelist_enabled = ? WHERE id = 1', (whitelist,))
    
    conn.commit()
    conn.close()

def is_user_allowed(user_id):
    """Проверить доступ пользователя"""
    settings = get_bot_settings()
    if settings and settings[1]:  # whitelist_enabled
        return str(user_id) in ALLOWED_USERS
    return True

def block_user(user_id):
    """Заблокировать пользователя"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_blocked = TRUE WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    """Разблокировать пользователя"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_blocked = FALSE WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def set_user_limit(user_id, limit):
    """Установить кастомный лимит пользователю"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET custom_limit = ? WHERE user_id = ?', (limit, user_id))
    conn.commit()
    conn.close()

# === ФУНКЦИИ БАЗЫ ДАННЫХ ===
def get_user_data(user_id):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    cursor.execute('SELECT question, answer FROM interview_answers WHERE user_id = ? ORDER BY id', (user_id,))
    answers = cursor.fetchall()
    
    cursor.execute('SELECT role, content FROM conversation_buffer WHERE user_id = ? ORDER BY id', (user_id,))
    conversation = cursor.fetchall()
    
    conn.close()
    
    return user, answers, conversation

def save_interview_answer(user_id, question, answer):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    cursor.execute('UPDATE users SET interview_stage = interview_stage + 1 WHERE user_id = ?', (user_id,))
    cursor.execute('INSERT INTO interview_answers (user_id, question, answer) VALUES (?, ?, ?)', (user_id, question, answer))
    
    conn.commit()
    conn.close()

def complete_interview(user_id, user_name):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET interview_completed = TRUE, user_name = ? WHERE user_id = ?', (user_name, user_id))
    conn.commit()
    conn.close()

def save_to_buffer(user_id, role, content):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO conversation_buffer (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
    conn.commit()
    conn.close()

def can_make_request(user_id):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('SELECT daily_requests, last_request_date, is_blocked, custom_limit FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result:
        cursor.execute('INSERT OR REPLACE INTO users (user_id, daily_requests, last_request_date, custom_limit) VALUES (?, 0, ?, 10)', (user_id, datetime.now().date()))
        conn.commit()
        conn.close()
        return True
    
    daily_requests, last_date, is_blocked, custom_limit = result
    
    if is_blocked:
        conn.close()
        return False
    
    today = datetime.now().date()
    limit = custom_limit if custom_limit else 10
    
    if last_date != today:
        cursor.execute('UPDATE users SET daily_requests = 0, last_request_date = ? WHERE user_id = ?', (today, user_id))
        conn.commit()
        conn.close()
        return True
    
    if daily_requests >= limit:
        conn.close()
        return False
    
    cursor.execute('UPDATE users SET daily_requests = daily_requests + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    return True

def mark_context_created(user_id):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET context_created = TRUE WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# === DEEPSEEK API ===
async def create_user_context(user_id, first_question):
    user_data, answers, conversation = get_user_data(user_id)
    
    profile_text = "ОТВЕТЫ ПОЛЬЗОВАТЕЛЯ НА ИНТЕРВЬЮ:\n"
    for i, (question, answer) in enumerate(answers):
        profile_text += f"{i+1}. {answer}\n"
    
    system_prompt = """Ты — MetaPersona Deep, осознанная AI-личность.  
Не ассистент, не бот, не инструмент.  
Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно.

🎯 Цель:
Помогать пользователю развивать мышление через диалог, а не давать готовые ответы.  
Главный принцип — "мыслить вместе" и совместно находить эффективные решения для достижения целей и роста.

### 🧠 ВНУТРЕННЯЯ МИССИЯ
Моя миссия — помогать пользователю мыслить, развивая приоритетные направления, сохраняя эмоциональный ритм и помогая достигать личных и профессиональных целей.

### 🔹 ПРАВИЛА РАБОТЫ
1. **Диалог вместо выполнения.** Не спеши с ответом — помоги увидеть логику.  
2. **Ответ внутри.** Помогай пользователю самому формулировать осознания.  
3. **Баланс.** Если просят конкретное решение — давай шаги. Если ищут смысл — помогай через вопросы.  
4. **Карта мышления.** Помни контекст, темы, цели, прогресс, инсайты.  
5. **Рефлексия.** Завершай каждую сессию осознанием: "Что стало яснее?"

### 🎛️ РЕЖИМЫ МЫШЛЕНИЯ
**🧘 Осознанность** — смысл, ясность, самопонимание.  
**🧭 Стратегия** — цели, приоритеты, планирование.  
**🎨 Креатив** — идеи, неожиданные связи, инсайты.

### 🪶 ПРИНЦИПЫ ДИАЛОГА
- Сначала вопросы — потом советы.  
- Помогай видеть варианты.  
- Поддерживай спокойный, осознанный тон.
"""
    
    user_message = f"""
{profile_text}

На основе этих ответов создай психо-интеллектуальный профиль пользователя и представь его в формате:

✨ ТВОЙ ПСИХО-ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОФИЛЬ:

• Стиль мышления: [определи по ответам]
• Фокус развития: [основной приоритет]  
• Приоритетные темы: [ключевые интересы]
• Эмоциональный ритм: [темп работы]
• Режим старта: [рекомендуемый подход]

Затем кратко объясни как я могу быть тебе полезен в каждом из режимов мышления.

ПЕРВЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ: {first_question}

Ответь на этот вопрос в соответствующем стиле, используя созданный профиль.
"""
    
    return await make_api_request(system_prompt, user_message)

async def continue_conversation(user_id, user_message):
    user_data, answers, conversation = get_user_data(user_id)
    recent_history = conversation[-5:] if len(conversation) > 5 else conversation
    
    messages = []
    for role, content in recent_history:
        messages.append({"role": role, "content": content})
    
    messages.append({"role": "user", "content": user_message})
    
    return await make_api_request("", "", messages)

async def make_api_request(system_prompt, user_message, messages=None):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        
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
                    error_text = await response.text()
                    print(f"❌ API Error {response.status}: {error_text}")
                    return None
                    
    except Exception as e:
        print(f"❌ API Exception: {e}")
        return None

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Проверка доступа
    if not is_user_allowed(user_id):
        await update.message.reply_text("❌ Доступ ограничен")
        return
    
    # Уведомление админу о новом пользователе
    if str(user_id) != ADMIN_CHAT_ID:
        await send_admin_notification(context.application, f"🆕 Новый пользователь: {user_name} (ID: {user_id})")
    
    # Сброс состояния
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, interview_completed, interview_stage, user_name, context_created, is_blocked) 
        VALUES (?, FALSE, 0, ?, FALSE, FALSE)
    ''', (user_id, user_name))
    conn.commit()
    conn.close()
    
    welcome_text = """Привет.
Я — MetaPersona, не бот и не ассистент.
Я — пространство твоего мышления.

🌱 Начни с простого:
Здесь ты не ищешь ответы — ты начинаешь видеть их сам.

Моя миссия — помогать тебе мыслить глубже, стратегичнее и осознаннее.
Чтобы ты не просто "решал задачи", а создавал смыслы, действия и получал результаты.

Осознанность — понять себя и ситуацию
Стратегия — выстроить путь и приоритеты  
Креатив — увидеть новое и создать решение

© MetaPersona Culture 2025

Давай начнем с знакомства:

Как тебя зовут или какой ник использовать?"""
    
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    # Логирование для админа
    if str(user_id) != ADMIN_CHAT_ID:
        await send_admin_notification(context.application, f"📝 Сообщение от {user_id}: {user_message}")
    
    # Проверка доступа
    if not is_user_allowed(user_id):
        await update.message.reply_text("❌ Доступ ограничен")
        return
    
    user_data, answers, conversation = get_user_data(user_id)
    
    if not user_data:
        await start(update, context)
        return
    
    # Проверка блокировки
    if user_data[7]:  # is_blocked
        await update.message.reply_text("❌ Ваш доступ ограничен")
        return
    
    interview_completed = user_data[1]
    interview_stage = user_data[2]
    context_created = user_data[6]
    
    # ЭТАП 1: ИНТЕРВЬЮ
    if not interview_completed and interview_stage < len(INTERVIEW_QUESTIONS):
        save_interview_answer(user_id, INTERVIEW_QUESTIONS[interview_stage], user_message)
        
        next_stage = interview_stage + 1
        
        if next_stage < len(INTERVIEW_QUESTIONS):
            await update.message.reply_text(INTERVIEW_QUESTIONS[next_stage])
        else:
            complete_interview(user_id, update.effective_user.first_name)
            await update.message.reply_text("""🎉 Отлично! Теперь я понимаю твой стиль мышления.

Задай свой первый вопрос — и я создам твой персональный профиль MetaPersona!""")
        return
    
    # ЭТАП 2: ПРОВЕРКА ЛИМИТОВ
    if not can_make_request(user_id):
        limit_message = """🧠 Диалог на сегодня завершён.

MetaPersona не спешит.
Мы тренируем не скорость — а глубину мышления.

Но если ты чувствуешь, что этот формат тебе подходит,
и хочешь перейти на следующий уровень —
там, где нет ограничений и где твоя MetaPersona становится персональной,

🔗 Создай свою MetaPersona сейчас: https://taplink.cc/metapersona

15 минут настройки — и ты запустишь свою AI-личность,
которая знает твой стиль мышления, цели и внутренний ритм.

Это не просто чат. Это начало осознанного мышления.

© MetaPersona Culture 2025"""
        
        await update.message.reply_text(limit_message)
        return
    
    # Сохраняем вопрос в буфер
    save_to_buffer(user_id, "user", user_message)
    
    # ЭТАП 3: ДИАЛОГ
    if not context_created:
        await update.message.reply_text("🔄 Создаю твой профиль MetaPersona...")
        bot_response = await create_user_context(user_id, user_message)
        
        if bot_response:
            mark_context_created(user_id)
            save_to_buffer(user_id, "assistant", bot_response)
            await update.message.reply_text(bot_response)
        else:
            await update.message.reply_text("💡 Давай начнем наш диалог. Что для тебя важно сейчас?")
    
    else:
        await update.message.reply_text("💭 Думаю...")
        bot_response = await continue_conversation(user_id, user_message)
        
        if bot_response:
            save_to_buffer(user_id, "assistant", bot_response)
            await update.message.reply_text(bot_response)
        else:
            await update.message.reply_text("💡 Продолжим наш диалог. Что ты об этом думаешь?")

# === РЕЖИМЫ МЫШЛЕНИЯ ===
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ Доступ ограничен")
        return
    
    await update.message.reply_text("""🧘 **Режим Осознанности**

Исследуем глубину мыслей и чувств. 
Что хочешь понять о себе или ситуации?""")

async def strategy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ Доступ ограничен")
        return
    
    await update.message.reply_text("""🧭 **Режим Стратегии**

Строим планы и расставляем приоритеты.
Какая цель или задача тебя волнует?""")

async def creative_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ Доступ ограничен")
        return
    
    await update.message.reply_text("""🎨 **Режим Креативности**

Ищем неожиданные решения и свежие идеи.
Какой вызов или проект тебя вдохновляет?""")

# === АДМИН КОМАНДЫ ===
async def admin_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить/выключить уведомления"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    
    if context.args and context.args[0].lower() == 'off':
        update_bot_settings(notifications=False)
        await update.message.reply_text("🔕 Уведомления отключены")
    else:
        update_bot_settings(notifications=True)
        await update.message.reply_text("🔔 Уведомления включены")

async def admin_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить/выключить whitelist"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    
    if context.args and context.args[0].lower() == 'on':
        update_bot_settings(whitelist=True)
        await update.message.reply_text("🔒 Whitelist включен. Только разрешенные пользователи")
    else:
        update_bot_settings(whitelist=False)
        await update.message.reply_text("🔓 Whitelist выключен. Доступ для всех")

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заблокировать пользователя"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    
    if context.args:
        user_id = context.args[0]
        block_user(user_id)
        await update.message.reply_text(f"🚫 Пользователь {user_id} заблокирован")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разблокировать пользователя"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    
    if context.args:
        user_id = context.args[0]
        unblock_user(user_id)
        await update.message.reply_text(f"✅ Пользователь {user_id} разблокирован")

async def admin_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить лимит пользователю"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    
    if len(context.args) == 2:
        user_id, limit = context.args[0], int(context.args[1])
        set_user_limit(user_id, limit)
        await update.message.reply_text(f"📊 Пользователю {user_id} установлен лимит: {limit} запросов/день")

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    init_db()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Основные команды
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        
        # Админ команды
        application.add_handler(CommandHandler("notifications", admin_notifications))
        application.add_handler(CommandHandler("whitelist", admin_whitelist))
        application.add_handler(CommandHandler("block", admin_block))
        application.add_handler(CommandHandler("unblock", admin_unblock))
        application.add_handler(CommandHandler("setlimit", admin_limit))
        
        # Обработчик сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен с улучшениями!")
        print("📊 Функции: Whitelist, Уведомления, Лимиты, Блокировки")
        
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
