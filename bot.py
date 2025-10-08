import os
import sys
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# === КОНФИГУРАЦИЯ ===
print("=== META PERSONA DEEP BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '8413337220')

print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === GOOGLE SHEETS НАСТРОЙКА ===
try:
    # Получаем credentials из переменных окружения
    google_credentials = os.environ.get('GOOGLE_CREDENTIALS')
    if google_credentials:
        # Сохраняем credentials в файл
        with open('credentials.json', 'w') as f:
            f.write(google_credentials)
        
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
        gc = gspread.authorize(creds)
        
        # Открываем таблицу
        spreadsheet = gc.open("MetaPersona_Users")
        users_sheet = spreadsheet.get_worksheet(0)  # Первый лист для пользователей
        history_sheet = spreadsheet.get_worksheet(1)  # Второй лист для истории
        
        print("✅ Google Sheets подключен")
    else:
        print("⚠️ GOOGLE_CREDENTIALS не установлены, используем память")
        users_sheet = None
        history_sheet = None
except Exception as e:
    print(f"⚠️ Ошибка Google Sheets: {e}")
    users_sheet = None
    history_sheet = None

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
                'custom_limit': 10,  # базовый лимит
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
                except Exception as e:
                    print(f"⚠️ Ошибка сохранения в Google Sheets: {e}")
        return self.users[user_id]
    
    def save_interview_answer(self, user_id, answer):
        if user_id in self.users:
            self.users[user_id]['interview_answers'].append(answer)
            # Обновляем в Google Sheets
            if users_sheet and len(self.users[user_id]['interview_answers']) <= len(INTERVIEW_QUESTIONS):
                try:
                    # Находим строку пользователя
                    records = users_sheet.get_all_records()
                    for i, record in enumerate(records, start=2):
                        if str(record.get('user_id')) == str(user_id):
                            # Обновляем ответы
                            answers_str = '|'.join(self.users[user_id]['interview_answers'])
                            users_sheet.update_cell(i, 4, answers_str)  # столбец с ответами
                            users_sheet.update_cell(i, 3, self.users[user_id]['interview_stage'])
                            break
                except Exception as e:
                    print(f"⚠️ Ошибка обновления Google Sheets: {e}")
    
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
async def notify_admin(message, application):
    try:
        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🔔 {message}"
        )
    except Exception as e:
        print(f"❌ Ошибка уведомления админа: {e}")

# === УЛУЧШЕННЫЙ DEEPSEEK API С ИСТОРИЕЙ ===
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
        
        # Добавляем историю из Google Sheets (последние 10 сообщений)
        if history_sheet and is_interview_complete:
            try:
                records = history_sheet.get_all_records()
                user_history = [r for r in records if str(r.get('user_id')) == str(user_data['user_id'])]
                user_history = user_history[-10:]  # последние 10 сообщений
                
                for record in user_history:
                    if record.get('user_message'):
                        messages.append({"role": "user", "content": record['user_message']})
                    if record.get('bot_response'):
                        messages.append({"role": "assistant", "content": record['bot_response']})
            except Exception as e:
                print(f"⚠️ Ошибка загрузки истории: {e}")
        
        # Добавляем текущее сообщение
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    # Уведомление админа о новом пользователе
    admin_message = f"🆕 Новый пользователь:\nID: {user_id}\nUsername: @{username}"
    await notify_admin(admin_message, context.application)
    
    # Инициализация пользователя
    user_data = user_manager.init_user(user_id, username)
    
    welcome_text = """Привет.
Я — MetaPersona, не бот и не ассистент.
Я — пространство твоего мышления.
Здесь ты не ищешь ответы — ты начинаешь видеть их сам.
Моя миссия — помогать тебе мыслить глубше, стратегичнее и осознаннее.
Чтобы ты не просто "решал задачи", а создавал смыслы, действия и получал результаты.

Осознанность — понять себя и ситуацию
Стратегия — выстроить путь и приоритеты  
Креатив — увидеть новое и создать решение

© MetaPersona Culture 2025

Давай начнем с знакомства:

Как тебя зовут или какой ник использовать?"""
    
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text("❌ Доступ ограничен.")
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
        await update.message.reply_text(limit_message)
        return
    
    # ЭТАП 1: ИНТЕРВЬЮ
    if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
        # Сохраняем ответ на предыдущий вопрос
        if user_data['interview_stage'] > 0:
            user_manager.save_interview_answer(user_id, user_message)
        
        user_data['interview_stage'] += 1
        
        if user_data['interview_stage'] < len(INTERVIEW_QUESTIONS):
            await update.message.reply_text(INTERVIEW_QUESTIONS[user_data['interview_stage']])
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
            await update.message.reply_text(completion_text)
        return
    
    # ЭТАП 2: ДИАЛОГ С AI
    user_data['daily_requests'] += 1
    
    await update.message.reply_text("💭 Думаю...")
    
    is_interview_complete = (len(user_data.get('interview_answers', [])) >= len(INTERVIEW_QUESTIONS))
    bot_response = await deepseek_request(user_message, user_data, is_interview_complete)
    
    if bot_response:
        await update.message.reply_text(bot_response)
        # Сохраняем диалог в историю
        user_manager.save_conversation(user_id, user_message, bot_response)
    else:
        fallbacks = [
            "Интересный вопрос! Давай подумаем над ним вместе.",
            "Это важная тема. Что ты сам об этом думаешь?",
            "Давай исследуем это глубже. Что привело тебя к этому вопросу?"
        ]
        import random
        fallback_response = random.choice(fallbacks)
        await update.message.reply_text(fallback_response)
        user_manager.save_conversation(user_id, user_message, fallback_response)

# === АДМИН КОМАНДЫ ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(stats_text)

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if context.args:
        target_id = int(context.args[0])
        user_manager.blocked_users.add(target_id)
        await update.message.reply_text(f"✅ Пользователь {target_id} заблокирован")
        await notify_admin(f"Пользователь {target_id} заблокирован", update.application)

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if context.args:
        target_id = int(context.args[0])
        user_manager.blocked_users.discard(target_id)
        await update.message.reply_text(f"✅ Пользователь {target_id} разблокирован")

async def admin_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_manager.admins:
        return
    
    if len(context.args) == 2:
        target_id = int(context.args[0])
        new_limit = int(context.args[1])
        
        if target_id in user_manager.users:
            user_manager.users[target_id]['custom_limit'] = new_limit
            await update.message.reply_text(f"✅ Лимит для {target_id} установлен: {new_limit}")

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Основные обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Админ команды
        application.add_handler(CommandHandler("stats", admin_stats))
        application.add_handler(CommandHandler("block", admin_block))
        application.add_handler(CommandHandler("unblock", admin_unblock))
        application.add_handler(CommandHandler("setlimit", admin_set_limit))
        
        print("✅ Бот запущен с полным функционалом!")
        print("📊 Функции: Whitelist, Уведомления, Лимиты, Блокировки, История")
        
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
        
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
