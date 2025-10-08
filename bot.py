import os
import sys
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === КОНФИГУРАЦИЯ ===
print("=== META PERSONA BOT ===")
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

# === ПРОСТАЯ БАЗА ДАННЫХ ===
def init_simple_db():
    try:
        conn = sqlite3.connect('metapersona.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Только самая необходимая структура
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                interview_stage INTEGER DEFAULT 0,
                daily_requests INTEGER DEFAULT 0,
                last_date TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ База данных инициализирована")
        return True
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")
        return True  # Продолжаем даже при ошибке БД

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

# === ПРОСТОЙ DEEPSEEK API ===
async def simple_deepseek_request(user_message):
    """Упрощенный запрос к API"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        system_prompt = "Ты — MetaPersona, помощник для развития мышления. Отвечай кратко и по делу."
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7,
            "max_tokens": 800
        }
        
        timeout = aiohttp.ClientTimeout(total=20)
        
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
                    return "Давай подумаем над этим вместе. Что ты сам об этом думаешь?"
                    
    except Exception:
        return "Интересный вопрос! Давай обсудим его."

# === ПРОСТЫЕ ОБРАБОТЧИКИ ===
user_states = {}  # Простое хранение состояния в памяти

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Сбрасываем состояние
    user_states[user_id] = {
        'interview_stage': 0,
        'interview_completed': False,
        'daily_requests': 0,
        'last_date': datetime.now().strftime('%Y-%m-%d')
    }
    
    welcome_text = """Привет.
Я — MetaPersona, пространство твоего мышления.

Здесь ты не ищешь ответы — ты начинаешь видеть их сам.

Моя миссия — помогать тебе мыслить глубже, стратегичнее и осознаннее.

Давай начнем с знакомства:

Как тебя зовут или какой ник использовать?"""
    
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    # Инициализируем состояние если нужно
    if user_id not in user_states:
        user_states[user_id] = {
            'interview_stage': 0,
            'interview_completed': False, 
            'daily_requests': 0,
            'last_date': datetime.now().strftime('%Y-%m-%d')
        }
    
    state = user_states[user_id]
    
    # Проверка лимитов
    today = datetime.now().strftime('%Y-%m-%d')
    if state['last_date'] != today:
        state['daily_requests'] = 0
        state['last_date'] = today
    
    if state['daily_requests'] >= 10:
        await update.message.reply_text(
            "🧠 На сегодня диалог завершён. Лимит: 10 вопросов в день.\n\n"
            "Хочешь безлимитный доступ? 🔗 https://taplink.cc/metapersona"
        )
        return
    
    # ЭТАП 1: ИНТЕРВЬЮ
    if not state['interview_completed'] and state['interview_stage'] < len(INTERVIEW_QUESTIONS):
        # Просто переходим к следующему вопросу
        state['interview_stage'] += 1
        
        if state['interview_stage'] < len(INTERVIEW_QUESTIONS):
            await update.message.reply_text(INTERVIEW_QUESTIONS[state['interview_stage']])
        else:
            state['interview_completed'] = True
            await update.message.reply_text(
                "🎉 Отлично! Теперь я понимаю твой стиль мышления.\n\n"
                "Задай свой вопрос — и я помогу тебе мыслить эффективнее!"
            )
        return
    
    # ЭТАП 2: ДИАЛОГ С AI
    state['daily_requests'] += 1
    
    await update.message.reply_text("💭 Думаю...")
    
    bot_response = await simple_deepseek_request(user_message)
    await update.message.reply_text(bot_response)

# === РЕЖИМЫ МЫШЛЕНИЯ ===
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Исследуем глубину мыслей и чувств. Что хочешь понять?"
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
    
    # Простая инициализация БД (не критично если сломается)
    init_simple_db()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Только основные обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен!")
        
        # Запускаем с обработкой ошибок
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        # Пытаемся перезапуститься
        import time
        time.sleep(5)
        main()

if __name__ == '__main__':
    main()
