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

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

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
    
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            interview_completed BOOLEAN DEFAULT FALSE,
            interview_stage INTEGER DEFAULT 0,
            user_name TEXT,
            daily_requests INTEGER DEFAULT 0,
            last_request_date DATE,
            context_created BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Ответы интервью
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS interview_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question TEXT,
            answer TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # История диалога (буфер)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_buffer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

# === ИНТЕРВЬЮ ВОПРОСЫ ===
INTERVIEW_QUESTIONS = [
    "Как к тебе обращаться или какой ник использовать?",
    "Какому обращению ты отдаёшь предпочтение: мужской, женский или нейтральный род?",
    "Чем ты сейчас занимаешься (работа, проект, учёба)?",
    "Какие задачи или цели для тебя самые важные сейчас?",
    "Что для тебя значит 'мышление' — инструмент, путь или стиль жизни?",
    "В каких ситуациях ты теряешь фокус или мотивацию?",
    "Как ты обычно принимаешь решения: быстро или обдуманно?",
    "Как ты хотел(а) бы развить своё мышление — стратегически, глубже, креативнее?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе — бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен: философский, дружеский, менторский или структурный?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === ФУНКЦИИ БАЗЫ ДАННЫХ ===
def get_user_data(user_id):
    """Получить все данные пользователя"""
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
    """Сохранить ответ интервью"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id) VALUES (?)
    ''', (user_id,))
    
    cursor.execute('''
        UPDATE users SET interview_stage = interview_stage + 1 
        WHERE user_id = ?
    ''', (user_id,))
    
    cursor.execute('''
        INSERT INTO interview_answers (user_id, question, answer) 
        VALUES (?, ?, ?)
    ''', (user_id, question, answer))
    
    conn.commit()
    conn.close()

def complete_interview(user_id, user_name):
    """Завершить интервью"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET interview_completed = TRUE, user_name = ? 
        WHERE user_id = ?
    ''', (user_name, user_id))
    
    conn.commit()
    conn.close()

def save_to_buffer(user_id, role, content):
    """Сохранить сообщение в буфер"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO conversation_buffer (user_id, role, content) 
        VALUES (?, ?, ?)
    ''', (user_id, role, content))
    
    conn.commit()
    conn.close()

def can_make_request(user_id):
    """Проверить лимит запросов"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT daily_requests, last_request_date FROM users WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    
    if not result:
        # Новый пользователь
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, daily_requests, last_request_date) 
            VALUES (?, 0, ?)
        ''', (user_id, datetime.now().date()))
        conn.commit()
        conn.close()
        return True
    
    daily_requests, last_date = result
    today = datetime.now().date()
    
    # Сброс счетчика если новый день
    if last_date != today:
        cursor.execute('''
            UPDATE users SET daily_requests = 0, last_request_date = ? 
            WHERE user_id = ?
        ''', (today, user_id))
        conn.commit()
        conn.close()
        return True
    
    # Проверка лимита
    if daily_requests >= 10:  # 10 запросов в сутки
        conn.close()
        return False
    
    # Увеличиваем счетчик
    cursor.execute('''
        UPDATE users SET daily_requests = daily_requests + 1 
        WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()
    return True

def mark_context_created(user_id):
    """Пометить что контекст создан"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET context_created = TRUE WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()

# === DEEPSEEK API ===
async def create_user_context(user_id, first_question):
    """Создать контекст пользователя (1 мощный запрос)"""
    user_data, answers, conversation = get_user_data(user_id)
    
    # Формируем профиль из ответов
    profile_text = "ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n"
    for i, (question, answer) in enumerate(answers):
        profile_text += f"{i+1}. {question}\n   Ответ: {answer}\n\n"
    
    system_prompt = """
Ты — MetaPersona Deep, осознанная AI-личность для развития мышления.

МЕТОДОЛОГИЯ:
🧘 ОСОЗНАННОСТЬ - смыслы, ясность, рефлексия
🧭 СТРАТЕГИЯ - цели, планирование, приоритеты  
🎨 КРЕАТИВНОСТЬ - идеи, нестандартные решения

ПРИНЦИПЫ:
• Диалог вместо инструкций
• Вопросы перед ответами  
• Рефлексия в завершение
• Холодный взгляд и честность

На основе профиля пользователя ниже, создай персонализированный контекст для диалога.
"""
    
    user_message = f"""
{profile_text}
ПЕРВЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ: {first_question}

Создай контекст для нашего диалога и ответь на первый вопрос в методологии MetaPersona.
"""
    
    return await make_api_request(system_prompt, user_message)

async def continue_conversation(user_id, user_message):
    """Продолжить диалог с историей"""
    user_data, answers, conversation = get_user_data(user_id)
    
    # Собираем историю (последние 5 сообщений)
    recent_history = conversation[-5:] if len(conversation) > 5 else conversation
    
    messages = []
    
    # Добавляем историю
    for role, content in recent_history:
        messages.append({"role": role, "content": content})
    
    # Добавляем текущее сообщение
    messages.append({"role": "user", "content": user_message})
    
    return await make_api_request("", "", messages)

async def make_api_request(system_prompt, user_message, messages=None):
    """Базовый запрос к API"""
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

# === ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Сброс состояния
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, interview_completed, interview_stage, user_name, context_created) 
        VALUES (?, FALSE, 0, ?, FALSE)
    ''', (user_id, user_name))
    conn.commit()
    conn.close()
    
    welcome_text = f"""
🤖 Добро пожаловать в MetaPersona Deep, {user_name}!

Развиваем мышление через диалог. Начнем с знакомства:

{INTERVIEW_QUESTIONS[0]}
    """
    
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    user_data, answers, conversation = get_user_data(user_id)
    
    if not user_data:
        await start(update, context)
        return
    
    interview_completed = user_data[1] if user_data else False
    interview_stage = user_data[2] if user_data else 0
    context_created = user_data[6] if user_data else False
    
    # ЭТАП 1: ИНТЕРВЬЮ (0 API запросов)
    if not interview_completed and interview_stage < len(INTERVIEW_QUESTIONS):
        save_interview_answer(user_id, INTERVIEW_QUESTIONS[interview_stage], user_message)
        
        next_stage = interview_stage + 1
        
        if next_stage < len(INTERVIEW_QUESTIONS):
            await update.message.reply_text(INTERVIEW_QUESTIONS[next_stage])
        else:
            complete_interview(user_id, update.effective_user.first_name)
            profile_text = """
🎉 Интервью завершено!

✨ Теперь я понимаю ваш стиль мышления.

Доступны режимы:
🧘 /awareness - Осознанность
🧭 /strategy - Стратегия  
🎨 /creative - Креативность

Или просто задайте ваш вопрос!
            """
            await update.message.reply_text(profile_text)
        return
    
    # ЭТАП 2: ПРОВЕРКА ЛИМИТОВ
    if not can_make_request(user_id):
        await update.message.reply_text(
            "❌ Достигнут дневной лимит: 10 вопросов.\n\n"
            "Лимит обновится через 24 часа. Спасибо за тестирование MetaPersona!"
        )
        return
    
    # Сохраняем вопрос пользователя в буфер
    save_to_buffer(user_id, "user", user_message)
    
    # ЭТАП 3: ПЕРВЫЙ ДИАЛОГ (1 мощный запрос)
    if not context_created:
        await update.message.reply_text("🔄 Создаю ваш контекст...")
        
        bot_response = await create_user_context(user_id, user_message)
        
        if bot_response:
            mark_context_created(user_id)
            save_to_buffer(user_id, "assistant", bot_response)
            await update.message.reply_text(bot_response)
        else:
            await update.message.reply_text(
                "⚠️ Ошибка соединения. Попробуйте еще раз."
            )
    
    # ЭТАП 4: ПРОДОЛЖЕНИЕ ДИАЛОГА
    else:
        await update.message.reply_text("💭 Думаю над ответом...")
        
        bot_response = await continue_conversation(user_id, user_message)
        
        if bot_response:
            save_to_buffer(user_id, "assistant", bot_response)
            await update.message.reply_text(bot_response)
        else:
            await update.message.reply_text(
                "💡 Давайте продолжим наш диалог. Что вы об этом думаете?"
            )

# Режимы мышления
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Исследуем глубину мыслей и чувств. Что хотите понять?"
    )

async def strategy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 **Режим Стратегии**\n\n"
        "Строим планы и расставляем приоритеты. Какая задача?"
    )

async def creative_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 **Режим Креативности**\n\n"
        "Ищем неожиданные решения. Какой вызов?"
    )

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    init_db()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен!")
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
