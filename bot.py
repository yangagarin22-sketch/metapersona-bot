import os
import sys
import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

print("=== META PERSONA BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '8413337220')

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")

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

# === БАЗА ДАННЫХ ===
user_states = {}

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

# === DEEPSEEK API ===
async def deepseek_request(user_message, user_history=None):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        messages = []
        
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
    
    user_states[user_id] = {
        'interview_stage': 0,
        'daily_requests': 0,
        'last_date': datetime.now().strftime('%Y-%m-%d'),
        'interview_answers': [],
        'conversation_history': [],
        'username': username
    }
    
    # Уведомление админа
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый пользователь:\nID: {user_id}\nUsername: @{username}"
        )
    except Exception as e:
        print(f"⚠️ Ошибка уведомления админа: {e}")
    
    welcome_text = """Привет.
Я — MetaPersona, пространство твоего мышления.

Здесь ты не ищешь ответы — ты начинаешь видеть их сам.

Давай начнем с знакомства:

Как тебя зовут или какой ник использовать?"""
    
    await update.message.reply_text(welcome_text)
    user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_text})

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    if user_id not in user_states:
        await start(update, context)
        return
    
    state = user_states[user_id]
    
    # Сохраняем сообщение пользователя в историю
    state['conversation_history'].append({"role": "user", "content": user_message})
    
    # Проверка лимитов
    today = datetime.now().strftime('%Y-%m-%d')
    if state['last_date'] != today:
        state['daily_requests'] = 0
        state['last_date'] = today
    
    if state['daily_requests'] >= 10:
        limit_message = """🧠 На сегодня диалог завершён. Лимит: 10 вопросов в день.

MetaPersona не спешит.
Мы тренируем не скорость — а глубину мышления.

Но если ты чувствуешь, что этот формат тебе подходит,
и хочешь перейти на следующий уровень —
там, где нет ограничений,

🔗 Создай свою MetaPersona сейчас: https://taplink.cc/metapersona

15 минут настройки — и ты запустишь свою AI-личность,
которая знает твой стиль мышления, цели и внутренний ритм.

Это не просто чат. Это начало осознанного мышления.

© MetaPersona Culture 2025"""
        await update.message.reply_text(limit_message)
        state['conversation_history'].append({"role": "assistant", "content": limit_message})
        return
    
    # ЭТАП 1: ИНТЕРВЬЮ (БЕЗ ЗАПРОСОВ К ИИ)
    if state['interview_stage'] < len(INTERVIEW_QUESTIONS):
        # Сохраняем ответ на предыдущий вопрос
        if state['interview_stage'] > 0:
            state['interview_answers'].append(user_message)
        
        state['interview_stage'] += 1
        
        if state['interview_stage'] < len(INTERVIEW_QUESTIONS):
            next_question = INTERVIEW_QUESTIONS[state['interview_stage']]
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
    state['daily_requests'] += 1
    
    await update.message.reply_text("💭 Думаю...")
    
    # Используем историю для контекста ИИ
    bot_response = await deepseek_request(user_message, state['conversation_history'])
    
    if bot_response:
        await update.message.reply_text(bot_response)
        # Сохраняем ответ в историю
        state['conversation_history'].append({"role": "assistant", "content": bot_response})
        
        # Ограничиваем историю 15 сообщениями
        if len(state['conversation_history']) > 15:
            state['conversation_history'] = state['conversation_history'][-15:]
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

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    
    try:
        # Используем Application builder (современный подход для версии 20.x)
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ Бот запущен!")
        print("📊 Функции: История диалогов (15 сообщений), Сохранение интервью, Уведомления админа")
        
        # Простой запуск без сложных параметров
        application.run_polling()
        
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
