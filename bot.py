import os
import sys
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === ДИАГНОСТИКА ===
print("=== META PERSONA DEEP BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === HEALTH SERVER ДЛЯ RENDER ===
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
    print("✅ Health server started on port 10000")

start_health_server()

# === НАСТРОЙКА ЛОГГИНГА ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
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
    "Как ты хотел(а) бы развить своё мышление — стратегически, глубше, креативнее?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе — бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен: философский, дружеский, менторский или структурный?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === DEEPSEEK API ===
async def get_deepseek_response(user_message, conversation_history=None):
    """Упрощенная функция для запроса к DeepSeek API"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        # Системный промпт
        system_prompt = """Ты — MetaPersona Deep, AI-помощник для развития мышления. 
        Отвечай кратко, по делу, помогай пользователю мыслить яснее."""
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Добавляем историю если есть
        if conversation_history:
            messages.extend(conversation_history)
        
        # Добавляем текущее сообщение
        messages.append({"role": "user", "content": user_message})
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 800
        }
        
        # Таймаут 15 секунд
        timeout = aiohttp.ClientTimeout(total=15)
        
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
                    print(f"❌ DeepSeek API Error: {response.status} - {error_text}")
                    return "Давайте продолжим наш диалог. Что вы думаете об этом?"
                    
    except asyncio.TimeoutError:
        print("❌ DeepSeek API Timeout")
        return "Время ожидания ответа истекло. Давайте попробуем еще раз!"
    except Exception as e:
        print(f"❌ DeepSeek API Exception: {e}")
        return "Произошла ошибка. Пожалуйста, попробуйте еще раз."

# === ФУНКЦИИ БАЗЫ ДАННЫХ ===
def get_user_interview_stage(user_id):
    """Получить текущую стадию интервью пользователя"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT interview_stage FROM users WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else 0

def save_interview_answer(user_id, question, answer):
    """Сохранить ответ на вопрос интервью"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Создаем или обновляем пользователя
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, interview_stage) 
        VALUES (?, ?)
    ''', (user_id, get_user_interview_stage(user_id) + 1))
    
    # Сохраняем ответ
    cursor.execute('''
        INSERT INTO interview_answers (user_id, question, answer) 
        VALUES (?, ?, ?)
    ''', (user_id, question, answer))
    
    conn.commit()
    conn.close()

def complete_interview(user_id):
    """Пометить интервью как завершенное"""
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET interview_completed = TRUE WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()

# === ОБРАБОТЧИКИ КОМАНД ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Сбрасываем интервью
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, interview_stage, interview_completed) 
        VALUES (?, 0, FALSE)
    ''', (user_id,))
    conn.commit()
    conn.close()
    
    welcome_text = f"""
🤖 Добро пожаловать в MetaPersona Deep, {user_name}!

Я помогу вам развивать мышление через диалог. 
Давайте начнем с короткого знакомства.

{INTERVIEW_QUESTIONS[0]}
    """
    
    await update.message.reply_text(welcome_text)
    print(f"✅ /start от пользователя {user_id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    try:
        # Получаем текущую стадию интервью
        current_stage = get_user_interview_stage(user_id)
        
        # Если интервью еще не завершено
        if current_stage < len(INTERVIEW_QUESTIONS):
            # Сохраняем ответ на текущий вопрос
            current_question = INTERVIEW_QUESTIONS[current_stage]
            save_interview_answer(user_id, current_question, user_message)
            
            # Переходим к следующему вопросу
            next_stage = current_stage + 1
            
            if next_stage < len(INTERVIEW_QUESTIONS):
                # Задаем следующий вопрос
                next_question = INTERVIEW_QUESTIONS[next_stage]
                await update.message.reply_text(next_question)
            else:
                # Интервью завершено
                complete_interview(user_id)
                
                # Создаем профиль
                conn = sqlite3.connect('metapersona.db', check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute('SELECT answer FROM interview_answers WHERE user_id = ? ORDER BY id LIMIT 5', (user_id,))
                answers = cursor.fetchall()
                conn.close()
                
                profile_text = f"""
🎉 Интервью завершено!

✨ Ваш профиль:
• Обращение: {answers[0][0] if answers else 'Не указано'}
• Род деятельности: {answers[2][0] if len(answers) > 2 else 'Не указано'}
• Цели: {answers[3][0] if len(answers) > 3 else 'Не указано'}

Теперь доступны режимы мышления:
🧘 /awareness - Осознанность
🧭 /strategy - Стратегия  
🎨 /creative - Креативность

Или просто напишите ваш вопрос!
                """
                
                await update.message.reply_text(profile_text)
        
        else:
            # Обычный диалог после интервью
            bot_response = await get_deepseek_response(user_message)
            await update.message.reply_text(bot_response)
            
    except Exception as e:
        print(f"❌ Ошибка обработки сообщения: {e}")
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте еще раз.")

# === РЕЖИМЫ МЫШЛЕНИЯ ===
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Давайте исследуем ваши мысли и чувства. Что вы хотите понять глубже?"
    )

async def strategy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 **Режим Стратегии**\n\n"
        "Давайте построим план. Какая цель или задача вас сейчас волнует?"
    )

async def creative_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 **Режим Креативности**\n\n"
        "Давайте найдем неожиданные решения. Что хотите создать или изменить?"
    )

# === ОСНОВНАЯ ФУНКЦИЯ ===
def main():
    print("🚀 Запуск MetaPersona Deep Bot...")
    
    # Инициализация БД
    init_db()
    
    try:
        # Создаем приложение
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен и готов к работе!")
        print("📱 Проверяйте в Telegram...")
        
        # Запускаем бота
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
