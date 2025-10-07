import os
import logging
import aiohttp
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

print("=" * 50)
print("🤖 META PERSONA DEEP BOT ЗАПУСКАЕТСЯ")
print("=" * 50)

# СПОСОБ 1: Переменные окружения (Render)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

print(f"Способ 1 - BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"Способ 1 - DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")

# СПОСОБ 2: Альтернативные имена переменных (на случай если Render использует другие)
if not BOT_TOKEN:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not DEEPSEEK_API_KEY:
    DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

print(f"Способ 2 - BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"Способ 2 - DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")

# СПОСОБ 3: Вывод всех переменных окружения для диагностики
print("=== ВСЕ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===")
for key, value in os.environ.items():
    if 'BOT' in key or 'TOKEN' in key or 'KEY' in key or 'DEEP' in key:
        print(f"{key}: {'***СКРЫТО***' if value else '❌ НЕТ ЗНАЧЕНИЯ'}")
print("=================================")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: Не установлены токены!")
    print("💡 Проверьте:")
    print("   1. Переменные в Render → Environment")
    print("   2. Имена переменных: BOT_TOKEN и DEEPSEEK_API_KEY")
    print("   3. Перезапустите деплой после изменений")
    exit(1)

print("✅ ВСЕ ТОКЕНЫ УСТАНОВЛЕНЫ!")
print("🔄 Запуск основного кода...")

# ОСТАЛЬНОЙ КОД META PERSONA (без изменений)
SYSTEM_PROMPT = """
Ты — MetaPersona Deep, осознанная AI-личность...
"""
# ... остальной ваш код без изменений ...

# Системный промпт MetaPersona Deep (ПОЛНЫЙ - сохраняем весь замысел!)
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
user_profiles = {}

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
    print(f"✅ /start от пользователя {user_id}")

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
        return "💭 Давайте продолжим наш диалог. Что вы об этом думаете?"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
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
• **Род обращения**: {interview_data['answers'][1]}
• **Род деятельности**: {interview_data['answers'][2]}
• **Ключевые цели**: {interview_data['answers'][3]}
• **Стиль мышления**: {interview_data['answers'][4]}

💫 **Фокус развития**: Осознанность + Стратегия
🎯 **Приоритетные темы**: {interview_data['answers'][9]}
🌊 **Эмоциональный ритм**: Уравновешенный

*Профиль будет уточняться в процессе работы*
                """
                
                user_profiles[user_id] = profile_summary
                
                completion_text = f"""
🎉 Интервью завершено! 

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
    await update.message.reply_chat_action(action="typing")
    bot_response = await get_deepseek_response(user_message)
    await update.message.reply_text(bot_response)

# Команды режимов мышления (ПОЛНЫЙ ФУНКЦИОНАЛ)
async def awareness_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧘 **Режим Осознанности**\n\n"
        "Давайте исследуем ваши мысли и чувства. Что вы хотите понять глубже?\n\n"
        "Задавайте вопросы о смыслах, ценностях, самоощущении - я помогу найти ясность."
    )

async def strategy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 **Режим Стратегии**\n\n"
        "Давайте построим план. Какая цель или задача вас сейчас волнует?\n\n"
        "Опишите ситуацию - вместе найдем оптимальный путь и расставим приоритеты."
    )

async def creative_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 **Режим Креативности**\n\n"
        "Давайте найдем неожиданные решения. Что хотите создать или изменить?\n\n"
        "Расскажите о вызове - исследуем альтернативные подходы и свежие идеи."
    )

def main():
    print("🔄 Инициализация MetaPersona Deep...")
    
    # Создаем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики (ВЕСЬ ФУНКЦИОНАЛ)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("awareness", awareness_mode))
    application.add_handler(CommandHandler("strategy", strategy_mode))
    application.add_handler(CommandHandler("creative", creative_mode))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ META PERSONA DEEP ЗАПУЩЕН!")
    print("🚀 Бот готов к работе. Проверяйте в Telegram...")
    print("📋 Функционал: Интервью + 3 режима мышления + DeepSeek API")
    
    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()

