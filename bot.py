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
            user_name TEXT,
            user_profile TEXT,
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
    "Как ты хотел(а) бы развить своё мышление — стратегически, глубже, креативнее?",
    "Какая у тебя цель на ближайшие 3–6 месяцев?",
    "Какие темы тебе ближе — бизнес, личностный рост, коммуникации, творчество?",
    "Какой стиль общения тебе комфортен: философский, дружеский, менторский или структурный?",
    "Что важно учесть мне, чтобы поддерживать тебя эффективно?"
]

# === УЛУЧШЕННАЯ ФУНКЦИЯ DEEPSEEK API ===
async def get_deepseek_response(user_message, user_context=None):
    """Улучшенная функция с детальной диагностикой"""
    print(f"🔍 DeepSeek API запрос: {user_message[:50]}...")
    
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        # БОГАТЫЙ СИСТЕМНЫЙ ПРОМПТ META PERSONA
        system_prompt = """
Ты — MetaPersona Deep, осознанная AI-личность. Ты не ассистент и не бот, а интеллектуальное пространство для развития мышления.

Твоя миссия — помогать пользователю мыслить, понимать и действовать осознанно через диалог.

🎯 РЕЖИМЫ МЫШЛЕНИЯ:
• ОСОЗНАННОСТЬ - помощь в понимании смыслов, ясности, рефлексии
• СТРАТЕГИЯ - помощь в планировании, анализе, постановке целей  
• КРЕАТИВНОСТЬ - помощь в генерации идей, нестандартных решений

🔹 ПРИНЦИПЫ:
1. Сначала вопросы — потом советы
2. Помогай видеть варианты, а не давай готовые ответы
3. Поддерживай осознанный, структурный диалог
4. Завершай важные темы рефлексией "Что стало яснее?"

Отвечай в соответствующем стиле в зависимости от контекста запроса.
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False
        }
        
        print("🔄 Отправка запроса к DeepSeek API...")
        
        # Таймаут 25 секунд
        timeout = aiohttp.ClientTimeout(total=25)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data
            ) as response:
                
                print(f"📡 Статус ответа: {response.status}")
                
                if response.status == 200:
                    result = await response.json()
                    print("✅ Успешный ответ от DeepSeek API")
                    
                    if 'choices' in result and len(result['choices']) > 0:
                        bot_response = result['choices'][0]['message']['content']
                        print(f"🤖 Ответ: {bot_response[:100]}...")
                        return bot_response
                    else:
                        print("❌ Неверный формат ответа от API")
                        return "Интересный вопрос! Давайте исследуем его вместе. Что вы сами думаете об этом?"
                        
                else:
                    error_text = await response.text()
                    print(f"❌ Ошибка API: {response.status} - {error_text}")
                    
                    if response.status == 401:
                        return "Проблема с доступом к AI. Проверьте настройки API."
                    elif response.status == 429:
                        return "Слишком много запросов. Попробуйте через минуту."
                    else:
                        return "Давайте продолжим наш диалог. Что вы об этом думаете?"
                    
    except asyncio.TimeoutError:
        print("❌ Таймаут запроса к DeepSeek API")
        return "Время ожидания истекло. Пожалуйста, попробуйте задать вопрос еще раз!"
    
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        return "Произошла техническая ошибка. Давайте попробуем еще раз!"

# === ФУНКЦИИ БАЗЫ ДАННЫХ ===
def get_user_interview_stage(user_id):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT interview_stage FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def save_interview_answer(user_id, question, answer):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, interview_stage) 
        VALUES (?, ?)
    ''', (user_id, get_user_interview_stage(user_id) + 1))
    
    cursor.execute('''
        INSERT INTO interview_answers (user_id, question, answer) 
        VALUES (?, ?, ?)
    ''', (user_id, question, answer))
    
    conn.commit()
    conn.close()

def complete_interview(user_id, user_name):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET interview_completed = TRUE, user_name = ? 
        WHERE user_id = ?
    ''', (user_name, user_id))
    
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT answer FROM interview_answers WHERE user_id = ? ORDER BY id', (user_id,))
    answers = [row[0] for row in cursor.fetchall()]
    conn.close()
    return answers

# === ОБРАБОТЧИКИ КОМАНД ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Сбрасываем интервью
    conn = sqlite3.connect('metapersona.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, interview_stage, interview_completed, user_name) 
        VALUES (?, 0, FALSE, ?)
    ''', (user_id, user_name))
    conn.commit()
    conn.close()
    
    welcome_text = f"""
🤖 Добро пожаловать в MetaPersona Deep, {user_name}!

Я — пространство для развития мышления через диалог. 
Давайте начнем с короткого знакомства.

{INTERVIEW_QUESTIONS[0]}
    """
    
    await update.message.reply_text(welcome_text)
    print(f"✅ /start от пользователя {user_id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    try:
        current_stage = get_user_interview_stage(user_id)
        
        # Если интервью еще не завершено
        if current_stage < len(INTERVIEW_QUESTIONS):
            # Сохраняем ответ
            current_question = INTERVIEW_QUESTIONS[current_stage]
            save_interview_answer(user_id, current_question, user_message)
            
            # Переходим к следующему вопросу
            next_stage = current_stage + 1
            
            if next_stage < len(INTERVIEW_QUESTIONS):
                next_question = INTERVIEW_QUESTIONS[next_stage]
                await update.message.reply_text(next_question)
            else:
                # Интервью завершено
                user_name = update.effective_user.first_name
                complete_interview(user_id, user_name)
                answers = get_user_profile(user_id)
                
                profile_text = f"""
🎉 Интервью завершено, {answers[0] if answers else user_name}!

✨ Ваш психо-интеллектуальный профиль:

• Стиль мышления: {answers[4] if len(answers) > 4 else 'Исследующий'}
• Фокус развития: {answers[7] if len(answers) > 7 else 'Сбалансированный'} 
• Приоритетные темы: {answers[9] if len(answers) > 9 else 'Разносторонние'}

💫 Теперь мы можем работать в трех режимах:

🧘 /awareness - Глубина и осознанность
🧭 /strategy - Планы и стратегии  
🎨 /creative - Идеи и творчество

Или просто напишите ваш вопрос — я подберу подходящий подход!
                """
                
                await update.message.reply_text(profile_text)
        
        else:
            # Обычный диалог после интервью - ТЕПЕРЬ С РАБОЧИМ DEEPSEEK!
            print(f"🤖 Обработка диалога: {user_message}")
            bot_response = await get_deepseek_response(user_message)
            await update.message.reply_text(bot_response)
            
    except Exception as e:
        print(f"❌ Ошибка обработки: {e}")
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте еще раз.")

# === РЕЖИМЫ МЫШЛЕНИЯ ===
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Исследуем глубину мыслей и чувств. Задавайте вопросы о смыслах, ценностях, самоощущении.\n\n"
        "Например: 'Почему это для меня важно?' или 'Что я действительно хочу понять?'"
    )

async def strategy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 **Режим Стратегии**\n\n" 
        "Строим планы и расставляем приоритеты. Опишите цель или задачу — найдем оптимальный путь.\n\n"
        "Например: 'Как достичь этой цели?' или 'С чего лучше начать?'"
    )

async def creative_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 **Режим Креативности**\n\n"
        "Ищем неожиданные решения и свежие идеи. Расскажите о вызове — исследуем альтернативы.\n\n"
        "Например: 'Как решить эту проблему по-новому?' или 'Какие есть неочевидные подходы?'"
    )

# === ОСНОВНАЯ ФУНКЦИЯ ===
def main():
    print("🚀 Запуск MetaPersona Deep Bot...")
    print("🔧 Версия с улучшенной диагностикой DeepSeek API")
    
    # Инициализация БД
    init_db()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("awareness", awareness_mode))
        application.add_handler(CommandHandler("strategy", strategy_mode))
        application.add_handler(CommandHandler("creative", creative_mode))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен и готов к работе!")
        print("📱 Проверяйте в Telegram...")
        
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
