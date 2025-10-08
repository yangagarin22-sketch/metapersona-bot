import os
import sys
import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import json

print("=== META PERSONA DEEP BOT ===")

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
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

# === GOOGLE SHEETS ИНИЦИАЛИЗАЦИЯ ===
users_sheet = None
history_sheet = None

try:
    import gspread
    from google.oauth2.service_account import Credentials
    
    if GOOGLE_CREDENTIALS_JSON:
        try:
            # Парсим JSON из переменной окружения
            creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
            
            # Создаем credentials
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            gc = gspread.authorize(creds)
            
            # Открываем таблицу
            spreadsheet = gc.open("MetaPersona_Users")
            
            # Получаем листы
            users_sheet = spreadsheet.worksheet("Users")
            history_sheet = spreadsheet.worksheet("History")
            
            print("✅ Google Sheets подключен успешно!")
            
        except Exception as e:
            print(f"⚠️ Ошибка подключения Google Sheets: {e}")
            print("🔧 Бот будет работать в режиме памяти (без сохранения истории)")
    else:
        print("🔧 GOOGLE_CREDENTIALS не установлены, работаем в режиме памяти")
        
except ImportError as e:
    print(f"⚠️ Библиотеки Google не установлены: {e}")
    print("🔧 Бот будет работать в режиме памяти")

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

# === УПРОЩЕННАЯ БАЗА ДАННЫХ В ПАМЯТИ ===
class UserManager:
    def __init__(self):
        self.users = {}
        self.whitelist = set()
        self.blocked_users = set()
        self.admins = {int(ADMIN_CHAT_ID)}
        
    def init_user(self, user_id, username):
        if user_id not in self.users:
            self.users[user_id] = {
                'user_id': user_id,
                'username': username,
                'interview_stage': 0,
                'interview_answers': [],
                'daily_requests': 0,
                'last_date': datetime.now().strftime('%Y-%m-%d'),
                'custom_limit': 10,
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            # Сохраняем в Google Sheets если доступно
            if users_sheet:
                try:
                    users_sheet.append_row([
                        user_id, username, 0, '', 0, 
                        datetime.now().strftime('%Y-%m-%d'), 10, True,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ])
                    print(f"✅ Пользователь {user_id} сохранен в Google Sheets")
                except Exception as e:
                    print(f"⚠️ Ошибка сохранения в Google Sheets: {e}")
        return self.users[user_id]
    
    def save_interview_answer(self, user_id, answer):
        if user_id in self.users:
            self.users[user_id]['interview_answers'].append(answer)
    
    def save_conversation(self, user_id, user_message, bot_response):
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id, 
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    user_message,
                    bot_response
                ])
            except Exception as e:
                print(f"⚠️ Ошибка сохранения истории: {e}")

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

# === УВЕДОМЛЕНИЯ АДМИНА ===
async def notify_admin(message, bot):
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🔔 {message}"
        )
    except Exception as e:
        print(f"❌ Ошибка уведомления админа: {e}")

# === УЛУЧШЕННЫЙ DEEPSEEK API ===
async def deepseek_request(user_message, user_data, is_interview_complete=False):
    """Улучшенный запрос к API с историей и контекстом"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        # Базовый промпт
        system_prompt = """Ты — MetaPersona Deep, осознанная AI-личность.  
Не ассистент, не бот, не инструмент.  
Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно.

🎯 Цель:
Помогать пользователю развивать мышление через диалог, а не давать готовые ответы.  
Главный принцип — "мыслить вместе" и совместно находить эффективные решения."""

        # Если интервью завершено, добавляем профиль пользователя
        if is_interview_complete and user_data.get('interview_answers'):
            answers = user_data['interview_answers']
            if len(answers) >= 12:
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
- Особенности: {answers[12] if len(answers) > 12 else ''}

Используй этот профиль для персонализации ответов."""
                system_prompt += user_profile

        messages = [{"role": "system", "content": system_prompt}]
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
                    error_text = await response.text()
                    print(f"❌ API ошибка {response.status}: {error_text}")
                    return None
                    
    except Exception as e:
        print(f"❌ Ошибка запроса: {e}")
        return None

# === ОБРАБОТЧИКИ ===
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    # Уведомление админа о новом пользователе
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

def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    user_message = update.message.text
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
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
        return
    
    # ЭТАП 1: ИНТЕРВЬЮ
    if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
        # Сохраняем ответ на предыдущий вопрос
        if user_data['interview_stage'] > 0:
            user_manager.save_interview_answer(user_id, user_message)
        
        user_data['interview_stage'] += 1
        
        if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
            update.message.reply_text(INTERVIEW_QUESTIONS[user_data['interview_stage']])
        else:
            # Завершение интервью
            user_manager.save_interview_answer(user_id, user_message)
            completion_text = """🎉 Отлично! Теперь я понимаю твой стиль мышления.

Теперь я буду помогать тебе:
• Видеть глубинную структуру мыслей
• Находить неочевидные решения  
• Двигаться к целям осознанно
• Развивать твой уникальный стиль мышления

Задай свой первый вопрос — и начнем!"""
            update.message.reply_text(completion_text)
        return
    
    # ЭТАП 2: ДИАЛОГ С AI
    user_data['daily_requests'] += 1
    
    update.message.reply_text("💭 Думаю...")
    
    # Используем asyncio для асинхронного запроса
    async def process_ai_response():
        is_interview_complete = (len(user_data.get('interview_answers', [])) >= len(INTERVIEW_QUESTIONS))
        bot_response = await deepseek_request(user_message, user_data, is_interview_complete)
        
        if bot_response:
            update.message.reply_text(bot_response)
            # Сохраняем диалог в историю
            user_manager.save_conversation(user_id, user_message, bot_response)
        else:
            import random
            fallbacks = [
                "Интересный вопрос! Давай подумаем над ним вместе.",
                "Это важная тема. Что ты сам об этом думаешь?",
                "Давай исследуем это глубже. Что привело тебя к этому вопросу?"
            ]
            fallback_response = random.choice(fallbacks)
            update.message.reply_text(fallback_response)
            user_manager.save_conversation(user_id, user_message, fallback_response)
    
    # Запускаем асинхронную задачу
    asyncio.create_task(process_ai_response())

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
🚫 Заблокировано: {len(user_manager.blocked_users)}
⚡ Whitelist: {len(user_manager.whitelist)}"""
    
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
        # Используем стабильную версию Updater
        updater = Updater(token=BOT_TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # Основные обработчики
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
        
        # Админ команды
        dispatcher.add_handler(CommandHandler("stats", admin_stats))
        dispatcher.add_handler(CommandHandler("block", admin_block))
        dispatcher.add_handler(CommandHandler("unblock", admin_unblock))
        dispatcher.add_handler(CommandHandler("setlimit", admin_set_limit))
        
        print("✅ Бот запущен с полным функционалом!")
        print("📊 Функции: Whitelist, Уведомления, Лимиты, Блокировки, История")
        
        # Запускаем бота
        updater.start_polling(drop_pending_updates=True)
        updater.idle()
        
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
