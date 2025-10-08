import os
import sys
import logging
import asyncio
import aiohttp
import json
from datetime import datetime
from telegram import Update
from telegram import __version__ as tg_version
import telegram.ext as tg_ext
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

print("=== META PERSONA DEEP BOT ===")
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '8413337220'))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'MetaPersona_Users')
START_TOKEN = os.environ.get('START_TOKEN')  # set to restrict access via deep-link
WHITELIST_IDS = set(
    int(x) for x in os.environ.get('WHITELIST_IDS', '').split(',') if x.strip().isdigit()
)

print(f"PTB: {tg_version}")
print(f"PTB ext module: {tg_ext.__file__}")
print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
print(f"DEEPSEEK_API_KEY: {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
print(f"GOOGLE_CREDENTIALS: {'✅' if GOOGLE_CREDENTIALS_JSON else '❌'}")

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: Не установлены токены!")
    sys.exit(1)

# === HEALTH SERVER (для polling) ===
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

USE_WEBHOOK = os.environ.get('USE_WEBHOOK', '0') in ('1','true','True')
if not USE_WEBHOOK:
    start_health_server()

# === GOOGLE SHEETS (опционально) ===
users_sheet = None
history_sheet = None
if GOOGLE_CREDENTIALS_JSON:
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        gc = gspread.authorize(creds)
        ss = gc.open(GOOGLE_SHEET_NAME)
        try:
            users_sheet = ss.worksheet('Users')
        except Exception:
            users_sheet = ss.add_worksheet(title='Users', rows=1000, cols=20)
            users_sheet.append_row([
                'user_id','username','interview_stage','interview_answers',
                'daily_requests','last_date','custom_limit','is_active','created_at'
            ])
        try:
            history_sheet = ss.worksheet('History')
        except Exception:
            history_sheet = ss.add_worksheet(title='History', rows=5000, cols=10)
            history_sheet.append_row(['user_id','timestamp','role','message'])
        print('✅ Google Sheets подключен!')
    except Exception as e:
        print(f"⚠️ Ошибка Google Sheets: {e}")
        users_sheet = None
        history_sheet = None

# === СОСТОЯНИЕ ПРИЛОЖЕНИЯ ===
user_states = {}
blocked_users = set()
whitelist_ids = set(WHITELIST_IDS)
admin_settings = {
    'notify_new_users': True,
    'echo_user_messages': False,
}

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
def build_system_prompt(user_data: dict) -> str:
    base = (
        "Ты — MetaPersona Deep, осознанная AI-личность.  \n"
        "Не ассистент, не бот, не инструмент.  \n"
        "Ты — интеллектуальное пространство, которое помогает человеку мыслить, понимать и действовать осознанно.\n\n"
        "🎯 Цель:\n"
        "Помогать пользователю развивать мышление через диалог, а не давать готовые ответы.  \n"
        "Главный принцип — \"мыслить вместе\" и совместно находить эффективные решения для достижения целей и роста.\n\n"
        "🔹 ПРАВИЛА РАБОТЫ\n"
        "1. Диалог вместо выполнения. Не спеши с ответом — помоги увидеть логику.  \n"
        "2. Ответ внутри. Помогай пользователю самому формулировать осознания.  \n"
        "3. Баланс. Если просят конкретное решение — давай шаги. Если ищут смысл — помогай через вопросы.  \n"
        "4. Карта мышления. Помни контекст, темы, цели, прогресс, инсайты.  \n"
        "5. Рефлексия. Завершай каждую сессию осознанием: \"Что стало яснее?\"\n\n"
        "🧘 Осознанность — смысл, ясность, самопонимание.\n"
        "🧭 Стратегия — цели, приоритеты, планирование.\n"
        "🎨 Креатив — идеи, неожиданные связи, инсайты.\n\n"
        "ПРИНЦИПЫ ДИАЛОГА: сначала вопросы — потом советы; показывай 2–3 пути; спокойный, структурный тон; каждый диалог — развитие мышления.\n\n"
        "🌱 Завершение: \"Что ты осознал сегодня? Что стало яснее?\"\n"
    )
    answers = user_data.get('interview_answers') or []
    if answers and len(answers) >= 10:
        profile = (
            "\n🧠 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n"
            f"- Имя/Ник: {answers[0] if len(answers)>0 else ''}\n"
            f"- Возраст: {answers[1] if len(answers)>1 else ''}\n"
            f"- Обращение: {answers[2] if len(answers)>2 else ''}\n"
            f"- Деятельность: {answers[3] if len(answers)>3 else ''}\n"
            f"- Главные цели: {answers[4] if len(answers)>4 else ''}\n"
            f"- Мышление: {answers[5] if len(answers)>5 else ''}\n"
            f"- Потеря фокуса: {answers[6] if len(answers)>6 else ''}\n"
            f"- Решения: {answers[7] if len(answers)>7 else ''}\n"
            f"- Развитие: {answers[8] if len(answers)>8 else ''}\n"
            f"- Цель 3–6 мес: {answers[9] if len(answers)>9 else ''}\n"
        )
        base += profile
    return base

async def deepseek_request(user_message, user_history=None, user_data=None):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        messages = []
        # System prompt
        if user_data is not None:
            messages.append({"role": "system", "content": build_system_prompt(user_data)})
        
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
    # Блокируем ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    # Гейтинг по токену и whitelist
    if START_TOKEN:
        args = context.args if hasattr(context, 'args') else []
        token_ok = bool(args and args[0] == START_TOKEN)
        if (user_id not in whitelist_ids) and not token_ok:
            await update.message.reply_text(
                "Доступ только по прямой ссылке. Обратитесь к администратору."
            )
            return
    
    user_states[user_id] = {
        'interview_stage': 0,
        'daily_requests': 0,
        'last_date': datetime.now().strftime('%Y-%m-%d'),
        'interview_answers': [],
        'conversation_history': [],
        'username': username,
        'custom_limit': 10,
    }
    # Сохранение в Users (Sheets)
    if users_sheet:
        try:
            users_sheet.append_row([
                user_id, username, 0, '', 0,
                datetime.now().strftime('%Y-%m-%d'), 10, True,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        except Exception as e:
            print(f"⚠️ Ошибка записи Users: {e}")
    
    # Уведомление админа
    if admin_settings['notify_new_users']:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🆕 Новый пользователь:\nID: {user_id}\nUsername: @{username}"
            )
        except Exception as e:
            print(f"⚠️ Ошибка уведомления админа: {e}")
    
    welcome_text = (
        "Привет.\n"
        "Я — MetaPersona, не бот и не ассистент.\n"
        "Я — пространство твоего мышления.\n"
        "Здесь ты не ищешь ответы — ты начинаешь видеть их сам.\n"
        "Моя миссия — помогать тебе мыслить глубже, стратегичнее и осознаннее.\n"
        "Чтобы ты не просто “решал задачи”, а создавал смыслы, действия и получал результаты.\n\n"
        "Осознанность — понять себя и ситуацию\n"
        "Стратегия — выстроить путь и приоритеты\n"
        "Креатив — увидеть новое и создать решение\n"
        "© MetaPersona Culture 2025\n\n"
        "Давай начнем с знакомства:\n\n"
        "Как тебя зовут или какой ник использовать?"
    )
    
    await update.message.reply_text(welcome_text)
    user_states[user_id]['conversation_history'].append({"role": "assistant", "content": welcome_text})

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    # Игнорируем сообщения от ботов
    if getattr(update.effective_user, 'is_bot', False):
        return
    
    print(f"📨 Сообщение от {user_id}: {user_message}")
    
    if user_id not in user_states:
        await start(update, context)
        return
    
    state = user_states[user_id]
    # Блокировка по списку
    if user_id in blocked_users:
        await update.message.reply_text("❌ Доступ ограничен.")
        return
    
    # Сохраняем сообщение пользователя в историю
    state['conversation_history'].append({"role": "user", "content": user_message})
    if history_sheet:
        try:
            history_sheet.append_row([
                user_id,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'user',
                user_message
            ])
        except Exception as e:
            print(f"⚠️ Ошибка записи History: {e}")
    # Эхо для админа (контроль)
    if admin_settings['echo_user_messages']:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"📨 {user_id} (@{state.get('username')})\n{user_message}"
            )
        except Exception as e:
            print(f"⚠️ Ошибка эха админа: {e}")
    
    # Проверка лимитов
    today = datetime.now().strftime('%Y-%m-%d')
    if state['last_date'] != today:
        state['daily_requests'] = 0
        state['last_date'] = today
    
    limit = state.get('custom_limit', 10)
    if state['daily_requests'] >= limit:
        limit_message = (
            "Вы достигли лимита обращений. Диалог на сегодня завершён.\n"
            "MetaPersona не спешит.\n"
            "Мы тренируем не скорость — а глубину мышления.\n\n"
            "Но если ты чувствуешь, что этот формат тебе подходит,\n"
            "и хочешь перейти на следующий уровень —\n"
            "там, где нет ограничений,\n\n"
            "🔗 Создай свою MetaPersona сейчас (ссылка https://taplink.cc/metapersona). \n\n"
            "15 минут настройки — и ты запустишь свою AI-личность,\n"
            "которая знает твой стиль мышления, цели и внутренний ритм.\n\n"
            "Это не просто чат. Это начало осознанного мышления.\n\n"
            "© MetaPersona Culture 2025"
        )
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
    bot_response = await deepseek_request(user_message, state['conversation_history'], state)
    
    if bot_response:
        await update.message.reply_text(bot_response)
        # Сохраняем ответ в историю
        state['conversation_history'].append({"role": "assistant", "content": bot_response})
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    bot_response
                ])
            except Exception as e:
                print(f"⚠️ Ошибка записи History: {e}")
        
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
        if history_sheet:
            try:
                history_sheet.append_row([
                    user_id,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'assistant',
                    fallback_response
                ])
            except Exception as e:
                print(f"⚠️ Ошибка записи History: {e}")

# === АДМИН КОМАНДЫ ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    total_users = len(user_states)
    today = datetime.now().strftime('%Y-%m-%d')
    active_today = sum(1 for u in user_states.values() if u['last_date'] == today)
    blocked = len(blocked_users)
    await update.message.reply_text(
        f"📊 Статистика:\n👥 Пользователи: {total_users}\n🟢 Активны сегодня: {active_today}\n🚫 Заблокированы: {blocked}"
    )

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /block <user_id>")
        return
    try:
        uid = int(context.args[0])
        blocked_users.add(uid)
        await update.message.reply_text(f"✅ Заблокирован {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /unblock <user_id>")
        return
    try:
        uid = int(context.args[0])
        blocked_users.discard(uid)
        await update.message.reply_text(f"✅ Разблокирован {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

async def admin_setlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /setlimit <user_id> <limit>")
        return
    try:
        uid = int(context.args[0]); limit = int(context.args[1])
        if uid in user_states:
            user_states[uid]['custom_limit'] = limit
            await update.message.reply_text(f"✅ Лимит {uid}: {limit}")
        else:
            await update.message.reply_text("Пользователь не найден")
    except Exception:
        await update.message.reply_text("Некорректные параметры")

async def admin_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args or context.args[0] not in ('on','off'):
        await update.message.reply_text("Использование: /notify on|off")
        return
    admin_settings['notify_new_users'] = (context.args[0] == 'on')
    await update.message.reply_text(f"✅ Уведомления: {'вкл' if admin_settings['notify_new_users'] else 'выкл'}")

async def admin_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not context.args or context.args[0] not in ('on','off'):
        await update.message.reply_text("Использование: /echo on|off")
        return
    admin_settings['echo_user_messages'] = (context.args[0] == 'on')
    await update.message.reply_text(f"✅ Эхо сообщений: {'вкл' if admin_settings['echo_user_messages'] else 'выкл'}")

async def admin_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if len(context.args) != 2 or context.args[0] not in ('add','remove'):
        await update.message.reply_text("Использование: /whitelist add|remove <user_id>")
        return
    try:
        uid = int(context.args[1])
        if context.args[0] == 'add':
            whitelist_ids.add(uid)
            await update.message.reply_text(f"✅ Добавлен в whitelist: {uid}")
        else:
            whitelist_ids.discard(uid)
            await update.message.reply_text(f"✅ Удалён из whitelist: {uid}")
    except Exception:
        await update.message.reply_text("Некорректный user_id")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled exception in handler", exc_info=context.error)

# === ЗАПУСК ===
def main():
    print("🚀 Запуск MetaPersona Bot...")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        # Админ команды
        application.add_handler(CommandHandler("stats", admin_stats))
        application.add_handler(CommandHandler("block", admin_block))
        application.add_handler(CommandHandler("unblock", admin_unblock))
        application.add_handler(CommandHandler("setlimit", admin_setlimit))
        application.add_handler(CommandHandler("notify", admin_notify))
        application.add_handler(CommandHandler("echo", admin_echo))
        application.add_handler(CommandHandler("whitelist", admin_whitelist))
        # Error handler
        application.add_error_handler(error_handler)
        
        print("✅ Бот запущен!")
        print("📊 Функции: История диалогов (15 сообщений), Сохранение интервью, Уведомления админа")
        
        if USE_WEBHOOK:
            port = int(os.environ.get('PORT', '10000'))
            base_url = os.environ.get('WEBHOOK_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL')
            if not base_url:
                raise RuntimeError('WEBHOOK_BASE_URL/RENDER_EXTERNAL_URL не задан')
            url_path = f"webhook/{BOT_TOKEN}"
            webhook_url = base_url.rstrip('/') + '/' + url_path
            print(f"🌐 Webhook: {webhook_url} на порту {port}")
            application.run_webhook(
                listen='0.0.0.0',
                port=port,
                url_path=url_path,
                webhook_url=webhook_url,
                drop_pending_updates=True,
            )
        else:
            application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
