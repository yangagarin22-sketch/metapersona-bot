import os
import sys
import logging
import asyncio
import aiohttp
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import gspread
from google.oauth2.service_account import Credentials

print("=== META PERSONA DEEP BOT ===")

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY') 
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '8413337220')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS')

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
print(f"GOOGLE_CREDENTIALS: {'✅' if GOOGLE_CREDENTIALS_JSON else '❌'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === GOOGLE SHEETS ===
users_sheet = None
history_sheet = None

if GOOGLE_CREDENTIALS_JSON:
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        gc = gspread.authorize(creds)
        
        spreadsheet = gc.open("MetaPersona_Users")
        users_sheet = spreadsheet.worksheet("Users")
        history_sheet = spreadsheet.worksheet("History")
        print("✅ Google Sheets подключен!")
        
    except Exception as e:
        print(f"⚠️ Ошибка Google Sheets: {e}")

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

# === УПРАВЛЕНИЕ ДАННЫМИ ===
class UserManager:
    def __init__(self):
        self.users = {}
        self.blocked_users = set()
        self.admins = {int(ADMIN_CHAT_ID)}
        
    def init_user(self, user_id, username):
        if user_id not in self.users:
            user_data = {
                'user_id': user_id,
                'username': username,
                'interview_stage': 0,
                'interview_answers': [],
                'daily_requests': 0,
                'last_date': datetime.now().strftime('%Y-%m-%d'),
                'custom_limit': 10,
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'conversation_history': []
            }
            self.users[user_id] = user_data
            
            if users_sheet:
                try:
                    users_sheet.append_row([
                        user_id, username, 0, '', 0, 
                        user_data['last_date'], 10, True,
                        user_data['created_at']
                    ])
                except Exception as e:
                    print(f"⚠️ Ошибка сохранения пользователя: {e}")
                    
        return self.users[user_id]
    
    def save_interview_answer(self, user_id, answer):
        if user_id in self.users:
            self.users[user_id]['interview_answers'].append(answer)
    
    def add_to_history(self, user_id, role, message):
        if user_id in self.users:
            self.users[user_id]['conversation_history'].append({
                'role': role,
                'content': message,
                'timestamp': datetime.now().isoformat()
            })
            # Сохраняем последние 15 сообщений
            if len(self.users[user_id]['conversation_history']) > 15:
                self.users[user_id]['conversation_history'] = self.users[user_id]['conversation_history'][-15:]
            
            if history_sheet:
                try:
                    history_sheet.append_row([
                        user_id,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        role,
                        message
                    ])
                except Exception as e:
                    print(f"⚠️ Ошибка сохранения истории: {e}")
    
    def get_conversation_history(self, user_id):
        if user_id in self.users:
            return self.users[user_id]['conversation_history']
        return []

user_manager = UserManager()

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
async def deepseek_request_async(user_message, user_data):
    """Асинхронный запрос к API"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        system_prompt = """Ты — MetaPersona Deep, осознанная AI-личность.  
Не ассистент, не бот, не инструмент.  
Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно."""

        # Добавляем профиль пользователя если интервью завершено
        if len(user_data.get('interview_answers', [])) >= len(INTERVIEW_QUESTIONS):
            answers = user_data['interview_answers']
            user_profile = f"""
🧠 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
- Имя/Ник: {answers[0]}
- Возраст: {answers[1]}
- Обращение: {answers[2]}
- Деятельность: {answers[3]}
- Главные цели: {answers[4]}
- Мышление: {answers[5]}
- Потеря фокуса: {answers[6]}
- Решения: {answers[7]}
- Развитие: {answers[8]}
- Цель 3-6 мес: {answers[9]}
- Темы: {answers[10]}
- Стиль общения: {answers[11]}
- Особенности: {answers[12] if len(answers) > 12 else ''}"""
            system_prompt += user_profile

        messages = [{"role": "system", "content": system_prompt}]
        
        # Добавляем историю диалога
        history = user_manager.get_conversation_history(user_data['user_id'])
        for msg in history[-10:]:
            messages.append({"role": msg['role'], "content": msg['content']})
        
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
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    # Уведомление админа
    admin_message = f"🆕 Новый пользователь:\nID: {user_id}\nUsername: @{username}"
    context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)
    
    # Инициализация пользователя
    user_data = user_manager.init_user(user_id, username)
    
    welcome_text = """Привет.
Я — MetaPersona, не бот и не ассистент.
Я — пространство твоего мышления.
Здесь ты не ищешь ответы — ты начинаешь видеть их сам.
Моя миссия — помогать тебе мыслить глубже, стратегичнее и осознаннее.
Чтобы ты не просто "решал задачи", а создавал смыслы, действия и получал результаты.

Осознанность — понять себя и ситуацию
Стратегия — выстроить путь и приоритеты  
Креатив — увидеть новое и создать решение

© MetaPersona Culture 2025

Давай начнем с знакомства:

Как тебя зовут или какой ник использовать?"""
    
    update.message.reply_text(welcome_text)
    user_manager.add_to_history(user_id, 'assistant', welcome_text)

def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    # Сохраняем сообщение пользователя
    user_manager.add_to_history(user_id, 'user', user_message)
    
    # Инициализация пользователя если нужно
    if user_id not in user_manager.users:
        user_data = user_manager.init_user(user_id, username)
    else:
        user_data = user_manager.users[user_id]
    
    # Проверка блокировки
    if user_id in user_manager.blocked_users:
        update.message.reply_text("❌ Доступ ограничен.")
        return
    
    # Проверка лимитов
    today = datetime.now().strftime('%Y-%m-%d')
    if user_data['last_date'] != today:
        user_data['daily_requests'] = 0
        user_data['last_date'] = today
    
    current_limit = user_data.get('custom_limit', 10)
    
    if user_data['daily_requests'] >= current_limit:
        limit_message = """🧠 Вы достигли лимита обращений. Диалог на сегодня завершён.

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
        update.message.reply_text(limit_message)
        user_manager.add_to_history(user_id, 'assistant', limit_message)
        return
    
    # ЭТАП 1: ИНТЕРВЬЮ (БЕЗ ЗАПРОСОВ К ИИ)
    if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
        if user_data['interview_stage'] > 0:
            user_manager.save_interview_answer(user_id, user_message)
        
        user_data['interview_stage'] += 1
        
        if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
            next_question = INTERVIEW_QUESTIONS[user_data['interview_stage']]
            update.message.reply_text(next_question)
            user_manager.add_to_history(user_id, 'assistant', next_question)
        else:
            user_manager.save_interview_answer(user_id, user_message)
            completion_text = """🎉 Отлично! Теперь я понимаю твой стиль мышления.

Теперь я буду помогать тебе:
• Видеть глубинную структуру мыслей
• Находить неочевидные решения  
• Двигаться к целям осознанно
• Развивать твой уникальный стиль мышления

Задай свой первый вопрос — и начнем!"""
            update.message.reply_text(completion_text)
            user_manager.add_to_history(user_id, 'assistant', completion_text)
        return
    
    # ЭТАП 2: ДИАЛОГ С AI
    user_data['daily_requests'] += 1
    
    # Отправляем сообщение "Думаю..."
    thinking_msg = update.message.reply_text("💭 Думаю...")
    
    # Асинхронный запрос к ИИ
    async def get_ai_response():
        bot_response = await deepseek_request_async(user_message, user_data)
        
        # Удаляем сообщение "Думаю..."
        context.bot.delete_message(chat_id=update.effective_chat.id, message_id=thinking_msg.message_id)
        
        if bot_response:
            update.message.reply_text(bot_response)
            user_manager.add_to_history(user_id, 'assistant', bot_response)
        else:
            import random
            fallbacks = [
                "Интересный вопрос! Давай подумаем над ним вместе.",
                "Это важная тема. Что ты сам об этом думаешь?",
                "Давай исследуем это глубже. Что привело тебя к этому вопросу?"
            ]
            fallback_response = random.choice(fallbacks)
            update.message.reply_text(fallback_response)
            user_manager.add_to_history(user_id, 'assistant', fallback_response)
    
    # Запускаем асинхронную задачу
    asyncio.create_task(get_ai_response())

# === АДМИН КОМАНДЫ ===
def admin_stats(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    total_users = len(user_manager.users)
    active_today = sum(1 for u in user_manager.users.values() 
                      if u['last_date'] == datetime.now().strftime('%Y-%m-%d'))
    
    stats_text = f"""📊 Статистика бота:
👥 Всего пользователей: {total_users}
🟢 Активных сегодня: {active_today}
🚫 Заблокировано: {len(user_manager.blocked_users)}"""
    
    update.message.reply_text(stats_text)

def admin_block(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if context.args:
        target_id = int(context.args[0])
        user_manager.blocked_users.add(target_id)
        update.message.reply_text(f"✅ Пользователь {target_id} заблокирован")
        context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Пользователь {target_id} заблокирован")

def admin_unblock(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if context.args:
        target_id = int(context.args[0])
        user_manager.blocked_users.discard(target_id)
        update.message.reply_text(f"✅ Пользователь {target_id} разблокирован")

def admin_set_limit(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if len(context.args) == 2:
        target_id = int(context.args[0])
        new_limit = int(context.args[1])
        
        if target_id in user_manager.users:
            user_manager.users[target_id]['custom_limit'] = new_limit
            update.message.reply_text(f"✅ Лимит для {target_id} установлен: {new_limit}")

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    
    try:
        # Используем Updater из версии 13.15 - РАБОТАЕТ СТАБИЛЬНО
        updater = Updater(token=BOT_TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # Обработчики
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
        
        # Админ команды
        dispatcher.add_handler(CommandHandler("stats", admin_stats))
        dispatcher.add_handler(CommandHandler("block", admin_block))
        dispatcher.add_handler(CommandHandler("unblock", admin_unblock))
        dispatcher.add_handler(CommandHandler("setlimit", admin_set_limit))
        
        print("✅ Бот запущен с полным функционалом!")
        print("📊 Сохранение истории: ✅ (15 сообщений)")
        print("💬 Интервью без ИИ: ✅") 
        print("🔧 Админ функции: ✅")
        print("🚀 Стабильная версия: python-telegram-bot==13.15")
        
        # Запускаем бота
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
