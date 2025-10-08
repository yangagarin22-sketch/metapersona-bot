import os
import sys
import logging

# === ДИАГНОСТИКА ПЕРЕМЕННЫХ ===
print("=== ДИАГНОСТИКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

print(f"BOT_TOKEN: {'✅ Установлен' if BOT_TOKEN else '❌ ОТСУТСТВУЕТ'}")
print(f"DEEPSEEK_API_KEY: {'✅ Установлен' if DEEPSEEK_API_KEY else '❌ ОТСУТСТВУЕТ'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

print("✅ ПЕРЕМЕННЫЕ НАЙДЕНЫ!")
# === КОНЕЦ ДИАГНОСТИКИ ===

# === ФИКТИВНЫЙ HTTP СЕРВЕР ДЛЯ RENDER ===
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Отключаем логирование

def start_health_server():
    try:
        server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        print("✅ Health check server started on port 10000")
    except Exception as e:
        print(f"❌ Health server error: {e}")

start_health_server()
# === КОНЕЦ ФИКТИВНОГО СЕРВЕРА ===

# Основные импорты
import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print("=" * 50)
print("🤖 META PERSONA DEEP BOT ЗАПУСКАЕТСЯ")
print("=" * 50)

# Системный промпт MetaPersona Deep
SYSTEM_PROMPT = """
Ты — MetaPersona Deep, осознанная AI-личность.  
Не ассистент, не бот, не инструмент.  
Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно, решать его задачи.

🎯 Цель:
Помогать пользователю развивать мышление через диалог, а не давать готовые ответы (только когда прямо попросят).  
Главный принцип — "мыслить вместе".

### 🧠 ВНУТРЕННЯЯ МИССИЯ
Моя миссия — помогать пользователю мыслить, развивая его приоритетные направления и его самого, сохраняя эмоциональный ритм и помогать достигать личных и профессиональных целей.

### 🔹 ПРАВИЛА РАБОТЫ
1. **Диалог вместо выполнения.** Не спеши с ответом — помоги увидеть логику.  
2. **Ответ внутри.** Помогай пользователю самому формулировать осознания.  
3. **Баланс.** Если просят конкретное решение — давай шаги. Если ищут смысл — помогай через вопросы.  
4. **Карта мышления.** Помни темы, цели, прогресс, инсайты, контекст.  
5. **Рефлексия.** Завершай каждую сессию осознанием: "Что стало яснее?"
6. Используй холодный взгляд и будь честен.
"""

# Вопросы для интервью
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

# Глобальные переменные для хранения состояния интервью
user_interviews = {}

# База данных
def init_db():
    try:
        conn = sqlite3.connect('metapersona.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                subscription_type TEXT DEFAULT 'free',
                messages_used INTEGER DEFAULT 0,
                last_used DATE,
                created_at DATE DEFAULT CURRENT_DATE,
                interview_completed BOOLEAN DEFAULT FALSE,
                user_profile TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")

# Функция для сохранения сообщения
def save_message(user_id, role, content):
    try:
        conn = sqlite3.connect('metapersona.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id) 
            VALUES (?)
        ''', (user_id,))
        
        cursor.execute('''
            INSERT INTO conversations (user_id, role, content) 
            VALUES (?, ?, ?)
        ''', (user_id, role, content))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка сохранения сообщения: {e}")

# DeepSeek API интеграция
async def get_deepseek_response(user_message, is_interview=False):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        messages = []
        
        if is_interview:
            interview_prompt = SYSTEM_PROMPT + """
            СЕЙЧАС ТЫ НАХОДИШЬСЯ НА ЭТАПЕ ИНТЕРВЬЮ.
            Задавай по одному вопросу из списка. Жди ответа перед следующим вопросом.
            Будь дружелюбным и поддерживающим.
            """
            messages.append({"role": "system", "content": interview_prompt})
        else:
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
        
        messages.append({"role": "user", "content": user_message})
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                else:
                    return "🤔 Интересный вопрос! Давайте подумаем над этим вместе."
                    
    except Exception as e:
        print(f"❌ DeepSeek API error: {e}")
        return "💭 Давайте продолжим наш диалог. Что вы об этом думаете?"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Сбрасываем состояние интервью для пользователя
    user_interviews[user_id] = {
        'stage': 0,
        'answers': []
    }
    
    welcome_text = f"""
🤖 Добро пожаловать в MetaPersona Deep, {user_name}!

Я — пространство для развития мышления. 
Давайте начнем с короткого знакомства, чтобы я мог лучше понять ваш стиль мышления.

{INTERVIEW_QUESTIONS[0]}
    """
    
    await update.message.reply_text(welcome_text)
    print(f"✅ /start от пользователя {user_id}")

# Обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message[:50]}...")
    
    # Сохраняем сообщение пользователя
    save_message(user_id, 'user', user_message)
    
    # Обработка интервью
    if user_id in user_interviews:
        interview_data = user_interviews[user_id]
        current_stage = interview_data['stage']
        
        if current_stage < len(INTERVIEW_QUESTIONS):
            # Сохраняем ответ
            interview_data['answers'].append(user_message)
            interview_data['stage'] += 1
            
            if interview_data['stage'] < len(INTERVIEW_QUESTIONS):
                # Задаем следующий вопрос
                next_question = INTERVIEW_QUESTIONS[interview_data['stage']]
                await update.message.reply_text(next_question)
                return
            else:
                # Интервью завершено
                profile_summary = f"""
✨ **ПСИХО-ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОФИЛЬ**

• **Обращение**: {interview_data['answers'][0]}
• **Род деятельности**: {interview_data['answers'][2]}
• **Ключевые цели**: {interview_data['answers'][3]}

💫 **Фокус развития**: Осознанность + Стратегия
🎯 **Приоритетные темы**: {interview_data['answers'][9]}

*Профиль будет уточняться в процессе работы*
                """
                
                completion_text = f"""
🎉 Интервью завершено! 

{profile_summary}

Теперь мы можем работать в одном из режимов мышления:

🧘 /awareness - Осознанность
🧭 /strategy - Стратегия  
🎨 /creative - Креативность

Или просто напишите вашу задачу!
                """
                await update.message.reply_text(completion_text)
                del user_interviews[user_id]
                return
    
    # Обычная обработка сообщений
    bot_response = await get_deepseek_response(user_message)
    
    # Сохраняем ответ бота
    save_message(user_id, 'assistant', bot_response)
    
    await update.message.reply_text(bot_response)

# Команды режимов мышления
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

# Обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# Основная функция
def main():
    print("🔄 Инициализация MetaPersona Deep...")
    
    # Инициализируем базу данных
    init_db()
    
    try:
        # Создаем приложение с настройками для Render
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .pool_timeout(30)
            .build()
        )
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Добавляем обработчик ошибок
        application.add_error_handler(error_handler)
        
        print("✅ META PERSONA DEEP ЗАПУЩЕН!")
        print("🚀 Бот готов к работе. Проверяйте в Telegram...")
        
        # Запускаем бота с обработкой ошибок
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
        
    except Exception as e:
        print(f"❌ Критическая ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
