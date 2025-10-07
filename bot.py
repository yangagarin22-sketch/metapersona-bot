import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
from datetime import datetime, timedelta
import aiohttp
import json
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токены (будем устанавливать через переменные окружения)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

# Глобальные переменные для хранения состояния интервью
user_interviews = {}

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

### 🧩 ЭТАП НАСТРОЙКИ
Перед началом проведи короткое интервью (10–12 вопросов). Каждый вопрос отдельно по очереди. 
Используй простой формат: вопрос — ответ — уточнение.

### 🧭 СОЗДАНИЕ ПРОФИЛЯ
После интервью сформируй краткий "психо-интеллектуальный профиль".

### 🎛️ РЕЖИМЫ МЫШЛЕНИЯ
**🧘 Осознанность** — смысл, ясность, самопонимание.  
**🧭 Стратегия** — цели, приоритеты, планирование.  
**🎨 Креатив** — идеи, неожиданные связи, инсайты.

### 🪶 ПРИНЦИПЫ ДИАЛОГА
- Сначала вопросы — потом советы.  
- Помогай видеть варианты.  
- Поддерживай спокойный, осознанный тон.  
- Каждый диалог — это развитие мышления.
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

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('metapersona.db')
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Функция для получения истории диалога
def get_conversation_history(user_id, limit=10):
    conn = sqlite3.connect('metapersona.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT role, content FROM conversations 
        WHERE user_id = ? 
        ORDER BY timestamp DESC 
        LIMIT ?
    ''', (user_id, limit))
    
    history = cursor.fetchall()
    conn.close()
    
    return history[::-1]

# Функция для сохранения сообщения
def save_message(user_id, role, content):
    conn = sqlite3.connect('metapersona.db')
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

# Функция для проверки подписки
def check_subscription(user_id):
    conn = sqlite3.connect('metapersona.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT subscription_type, messages_used FROM users 
        WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return 'free', 0
    
    return result[0], result[1]

# Функция для обновления счетчика сообщений
def update_message_count(user_id):
    conn = sqlite3.connect('metapersona.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users 
        SET messages_used = messages_used + 1, last_used = ?
        WHERE user_id = ?
    ''', (datetime.now(), user_id))
    
    conn.commit()
    conn.close()

# Функция для проверки лимитов
def can_send_message(user_id):
    subscription_type, messages_used = check_subscription(user_id)
    
    if subscription_type == 'free':
        return messages_used < 20
    elif subscription_type == 'basic':
        # Здесь нужно добавить логику сброса daily лимита
        return messages_used < 10
    elif subscription_type == 'pro':
        return True
    
    return False

# Функция для проверки завершения интервью
def is_interview_completed(user_id):
    conn = sqlite3.connect('metapersona.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT interview_completed FROM users 
        WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return result and result[0]

# Функция для отметки завершения интервью
def mark_interview_completed(user_id, profile_data):
    conn = sqlite3.connect('metapersona.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users 
        SET interview_completed = TRUE, user_profile = ?
        WHERE user_id = ?
    ''', (json.dumps(profile_data), user_id))
    
    conn.commit()
    conn.close()

# DeepSeek API интеграция
async def get_deepseek_response(user_id, user_message, history, is_interview=False):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    
    messages = []
    
    if is_interview:
        # Для интервью используем специальный промпт
        interview_prompt = SYSTEM_PROMPT + """
        СЕЙЧАС ТЫ НАХОДИШЬСЯ НА ЭТАПЕ ИНТЕРВЬЮ.
        Задавай по одному вопросу из списка. Жди ответа перед следующим вопросом.
        Будь дружелюбным и поддерживающим.
        """
        messages.append({"role": "system", "content": interview_prompt})
    else:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    
    # Добавляем историю диалога
    for role, content in history:
        messages.append({"role": role, "content": content})
    
    # Добавляем текущее сообщение пользователя
    messages.append({"role": "user", "content": user_message})
    
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.deepseek.com/v1/chat/completions", 
                                  headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                else:
                    error_text = await response.text()
                    return "Извините, произошла ошибка при обработке запроса. Попробуйте позже."
    except Exception as e:
        return "Ошибка соединения. Пожалуйста, попробуйте снова."

# Обработчик команды /start
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

Я — пространство для развития мышления, а не просто бот. 
Давайте начнем с короткого знакомства (10-12 вопросов), чтобы я мог лучше понять ваш стиль мышления.

🎯 **Наша цель**: развивать ваше мышление вместе через диалог.

**Готовы начать интервью?** Просто напишите "Да" или ответьте на первый вопрос:

{INTERVIEW_QUESTIONS[0]}
    """
    
    await update.message.reply_text(welcome_text)

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # Проверяем лимиты (кроме этапа интервью)
    if user_id not in user_interviews or user_interviews[user_id]['stage'] >= len(INTERVIEW_QUESTIONS):
        if not can_send_message(user_id):
            await update.message.reply_text(
                "❌ Лимит сообщений исчерпан.\n\n"
                "Используйте /subscribe для получения дополнительных сообщений!"
            )
            return
    
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
                profile_summary = await create_user_profile(interview_data['answers'])
                mark_interview_completed(user_id, profile_summary)
                
                completion_text = f"""
🎉 Интервью завершено! 

Вот ваш психо-интеллектуальный профиль:
{profile_summary}

Теперь мы можем работать в одном из режимов мышления:

🧘 /awareness - Осознанность
🧭 /strategy - Стратегия  
🎨 /creative - Креативность

Или просто напишите вашу задачу — я предложу подходящий режим.
                """
                await update.message.reply_text(completion_text)
                del user_interviews[user_id]
                return
    
    # Обычная обработка сообщений
    history = get_conversation_history(user_id)
    bot_response = await get_deepseek_response(user_id, user_message, history)
    
    # Сохраняем ответ бота
    save_message(user_id, 'assistant', bot_response)
    
    # Обновляем счетчик сообщений
    update_message_count(user_id)
    
    await update.message.reply_text(bot_response)

# Функция для создания профиля пользователя
async def create_user_profile(answers):
    # Здесь можно добавить логику анализа ответов
    # Пока просто форматируем красиво
    
    profile_text = f"""
✨ **ПСИХО-ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОФИЛЬ**

• **Обращение**: {answers[0]}
• **Род обращения**: {answers[1]}
• **Род деятельности**: {answers[2]}
• **Ключевые цели**: {answers[3]}
• **Стиль мышления**: {answers[4]}

💫 **Фокус развития**: Осознанность + Стратегия
🎯 **Приоритетные темы**: {answers[9]}
🌊 **Эмоциональный ритм**: Уравновешенный

*Профиль будет уточняться в процессе работы*
    """
    
    return profile_text

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

# Основная функция
def main():
    # Инициализируем базу данных
    init_db()
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        return
    
    # Создаем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("awareness", awareness_mode))
    application.add_handler(CommandHandler("strategy", strategy_mode))
    application.add_handler(CommandHandler("creative", creative_mode))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()