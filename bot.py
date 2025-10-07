import sys
sys.modules['imghdr'] = type(sys)('imghdr')  # Фикс для отсутствующей библиотеки

import os
import logging
import aiohttp
import json
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import sys
sys.modules['imghdr'] = type(sys)('imghdr')  # Фикс для отсутствующей библиотеки
# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

print("=" * 50)
print("🤖 META PERSONA DEEP BOT ЗАПУСКАЕТСЯ")
print("=" * 50)

# Токены
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

print(f"BOT_TOKEN: {'✅ Установлен' if BOT_TOKEN else '❌ ОТСУТСТВУЕТ'}")
print(f"DEEPSEEK_API_KEY: {'✅ Установлен' if DEEPSEEK_API_KEY else '❌ ОТСУТСТВУЕТ'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    exit(1)

# Системный промпт MetaPersona Deep (ПОЛНЫЙ)
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

# Вопросы для интервью (ПОЛНЫЙ СПИСОК)
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

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

# DeepSeek API интеграция
async def get_deepseek_response(user_id, user_message, is_interview=False):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        # Получаем историю диалога
        history = get_conversation_history(user_id)
        
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
        
        # Добавляем историю диалога
        for role, content in history:
            messages.append({"role": role, "content": content})
        
        # Добавляем текущее сообщение пользователя
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
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                else:
                    return "🤔 Интересный вопрос! Давайте подумаем над этим вместе."
                    
    except Exception as e:
        return "💭 Давайте продолжим наш диалог. Что вы об этом думаете?"

# Команды
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
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
    
    update.message.reply_text(welcome_text)
    print(f"✅ /start от пользователя {user_id}")

def handle_message(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
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
                update.message.reply_text(next_question)
                return
            else:
                # Интервью завершено
                profile_summary = f"""
✨ **ПСИХО-ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОФИЛЬ**

• **Обращение**: {interview_data['answers'][0]}
• **Род обращения**: {interview_data['answers'][1]}
• **Род деятельности**: {interview_data['answers'][2]}
• **Ключевые цели**: {interview_data['answers'][3]}
• **Стиль мышления**: {interview_data['answers'][4]}

💫 **Фокус развития**: Осознанность + Стратегия
🎯 **Приоритетные темы**: {interview_data['answers'][9]}
🌊 **Эмоциональный ритм**: Уравновешенный

*Профиль будет уточняться в процессе работы*
                """
                
                # Сохраняем профиль в БД
                conn = sqlite3.connect('metapersona.db')
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET interview_completed = TRUE, user_profile = ?
                    WHERE user_id = ?
                ''', (profile_summary, user_id))
                conn.commit()
                conn.close()
                
                completion_text = f"""
🎉 Интервью завершено! 

{profile_summary}

Теперь мы можем работать в одном из режимов мышления:

🧘 /awareness - Осознанность
🧭 /strategy - Стратегия  
🎨 /creative - Креативность

Или просто напишите вашу задачу — я предложу подходящий режим.
                """
                update.message.reply_text(completion_text)
                del user_interviews[user_id]
                return
    
    # Обычная обработка сообщений
    import asyncio
    bot_response = asyncio.run(get_deepseek_response(user_id, user_message))
    
    # Сохраняем ответ бота
    save_message(user_id, 'assistant', bot_response)
    
    update.message.reply_text(bot_response)

# Команды режимов мышления
def awareness_mode(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Давайте исследуем ваши мысли и чувства. Что вы хотите понять глубже?\n\n"
        "Задавайте вопросы о смыслах, ценностях, самоощущении - я помогу найти ясность."
    )

def strategy_mode(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🧭 **Режим Стратегии**\n\n"
        "Давайте построим план. Какая цель или задача вас сейчас волнует?\n\n"
        "Опишите ситуацию - вместе найдем оптимальный путь и расставим приоритеты."
    )

def creative_mode(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🎨 **Режим Креативности**\n\n"
        "Давайте найдем неожиданные решения. Что хотите создать или изменить?\n\n"
        "Расскажите о вызове - исследуем альтернативные подходы и свежие идеи."
    )

def main():
    print("🔄 Инициализация MetaPersona Deep...")
    
    # Инициализируем базу данных
    init_db()
    
    # Создаем updater
    updater = Updater(BOT_TOKEN, use_context=True)
    
    # Добавляем обработчики
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("awareness", awareness_mode))
    dp.add_handler(CommandHandler("strategy", strategy_mode))
    dp.add_handler(CommandHandler("creative", creative_mode))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    print("✅ META PERSONA DEEP ЗАПУЩЕН!")
    print("🚀 Бот готов к работе. Проверяйте в Telegram...")
    print("📋 Функционал: Интервью + 3 режима мышления + История диалогов")
    
    # Запускаем бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()


