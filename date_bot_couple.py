#!/usr/bin/env python3
"""
💕 Date Planner Bot — романтический бот для пар с AI
Один планирует свидание, второй получает персональную подготовку от AI
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from groq import Groq
import httpx

# ─── Логгирование ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Конфиг ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8655608335:AAF1f_IL3rCpPEXsRxhRI5jGVbFR1Pe2GJw")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "55996b89d132c400e30ad21647f5e6cc")

# ─── Состояния ConversationHandler ──────────────────────────────────────────
(
    MAIN_MENU,
    WAITING_DATE,
    WAITING_TIME,
    WAITING_CITY,
    WAITING_ACTIVITY_PREF,
    WAITING_PARTNER_INTERESTS,
    WAITING_OUTFIT_OCCASION,
    WAITING_AI_CHAT,
    WAITING_SURPRISE_THEME,
) = range(9)

# ─── Хранилище данных ────────────────────────────────────────────────────────
# Структура: {chat_id: {planner_id, partner_id, date_info, ...}}
couples_data = {}

groq_client = Groq(api_key=GROQ_API_KEY)


# ════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════════════════════

def get_couple_data(user_id: int) -> dict:
    """Получить данные пары по ID любого из партнёров."""
    for chat_id, data in couples_data.items():
        if data.get("planner_id") == user_id or data.get("partner_id") == user_id:
            return data
    # Создаём новую пару если не нашли
    couples_data[user_id] = {
        "planner_id": user_id,
        "partner_id": None,
        "date_info": {},
        "planner_name": "",
        "partner_name": "",
    }
    return couples_data[user_id]


def is_planner(user_id: int) -> bool:
    """Проверить, является ли пользователь планировщиком."""
    data = get_couple_data(user_id)
    return data.get("planner_id") == user_id


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Клавиатура зависит от роли пользователя."""
    data = get_couple_data(user_id)
    
    if is_planner(user_id):
        # Меню для планировщика
        keyboard = [
            [KeyboardButton("🗓 Запланировать свидание")],
            [KeyboardButton("💌 Пригласить партнёра"), KeyboardButton("💕 Наше свидание")],
            [KeyboardButton("🤖 AI-ассистент")],
        ]
    else:
        # Меню для приглашённого партнёра
        keyboard = [
            [KeyboardButton("💕 Наше свидание"), KeyboardButton("☁️ Погода")],
            [KeyboardButton("👗 Что надеть?"), KeyboardButton("📍 Где и что делать?")],
            [KeyboardButton("🎁 Идея сюрприза"), KeyboardButton("💬 Романтичная фраза")],
            [KeyboardButton("🤖 AI-ассистент")],
        ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def ask_claude(prompt: str, system: str = "") -> str:
    """Вызов Groq API."""
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=800,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return "Упс, AI временно недоступен 😔"


async def get_weather(city: str, date_str: str) -> str:
    """Получить погоду через OpenWeatherMap API."""
    try:
        url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru&cnt=40"
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=10)
            data = r.json()

        if data.get("cod") != "200":
            return f"Не удалось получить погоду для города «{city}»."

        target = datetime.strptime(date_str, "%d.%m.%Y")
        best = None
        for item in data["list"]:
            dt = datetime.fromtimestamp(item["dt"])
            if dt.date() == target.date():
                best = item
                break

        if not best:
            best = data["list"][0]

        temp = best["main"]["temp"]
        feels = best["main"]["feels_like"]
        desc = best["weather"][0]["description"].capitalize()
        wind = best["wind"]["speed"]
        humidity = best["main"]["humidity"]

        return (
            f"🌤 Погода в *{city}* на {date_str}:\n\n"
            f"🌡 Температура: *{temp:.0f}°C* (ощущается {feels:.0f}°C)\n"
            f"💨 Ветер: {wind:.1f} м/с\n"
            f"💧 Влажность: {humidity}%\n"
            f"☁️ {desc}"
        )
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return "Не удалось получить погоду 😔"


# ════════════════════════════════════════════════════════════════════════════
# ХЭНДЛЕРЫ
# ════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    
    data = get_couple_data(user_id)
    
    # Если это новый пользователь
    if data["planner_id"] == user_id and not data["planner_name"]:
        data["planner_name"] = name
        await update.message.reply_text(
            f"💕 Привет, *{name}*!\n\n"
            "Я помогу вам с партнёром спланировать идеальное свидание!\n\n"
            "🎯 *Как это работает:*\n"
            "1️⃣ Ты планируешь свидание с моей помощью\n"
            "2️⃣ Приглашаешь свою половинку в бота\n"
            "3️⃣ Я помогу ей/ему подготовиться: подскажу что надеть, покажу погоду, дам советы!\n\n"
            "Начнём? 👇",
            parse_mode="Markdown",
            reply_markup=main_keyboard(user_id)
        )
    else:
        await update.message.reply_text(
            f"С возвращением, *{name}*! 💕\n\nВыбери что хочешь сделать:",
            parse_mode="Markdown",
            reply_markup=main_keyboard(user_id)
        )
    
    return MAIN_MENU


async def show_date_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """💕 Наше свидание — показать инфо по-разному для планировщика и партнёра."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    
    if not d:
        if is_planner(user_id):
            await update.message.reply_text(
                "Ты ещё не запланировал свидание.\nНажми *🗓 Запланировать свидание*!",
                parse_mode="Markdown",
                reply_markup=main_keyboard(user_id)
            )
        else:
            await update.message.reply_text(
                "Твой партнёр ещё не запланировал свидание 😊\n"
                "Когда он это сделает, я сразу тебе расскажу!",
                parse_mode="Markdown",
                reply_markup=main_keyboard(user_id)
            )
        return MAIN_MENU
    
    # Для планировщика — просто инфо
    if is_planner(user_id):
        text = (
            "💕 *Ваше свидание:*\n\n"
            f"📅 Дата: *{d.get('date', '—')}*\n"
            f"🕐 Время: *{d.get('time', '—')}*\n"
            f"📍 Город: *{d.get('city', '—')}*\n"
            f"🎭 Активность: *{d.get('activity', '—')}*\n"
        )
    else:
        # Для партнёра — AI рассказывает красиво
        planner_name = data.get("planner_name", "Твой партнёр")
        
        ai_text = await ask_claude(
            f"{planner_name} запланировал для вас свидание:\n"
            f"- Дата: {d['date']}, Время: {d['time']}\n"
            f"- Город: {d['city']}\n"
            f"- Формат: {d['activity']}\n\n"
            "Расскажи об этом свидании романтично и тепло, как будто ты романтический ассистент. "
            "Добавь энтузиазма и предвкушения!",
            system="Ты романтический AI-ассистент. Говори тепло, мило, на русском."
        )
        
        text = f"💕 *{planner_name} приглашает тебя на свидание!*\n\n{ai_text}"
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(user_id))
    return MAIN_MENU


# ─── Планирование свидания (только для планировщика) ─────────────────────────

async def plan_date_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if not is_planner(user_id):
        await update.message.reply_text(
            "Эта функция доступна только тому, кто планирует свидание 😊",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "🗓 *Планируем свидание!*\n\nВведи дату свидания (формат: ДД.ММ.ГГГГ):",
        parse_mode="Markdown"
    )
    return WAITING_DATE


async def plan_date_got_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%d.%m.%Y")
        context.user_data["date"] = text
        await update.message.reply_text("⏰ Отлично! Теперь введи время (например: 19:00):")
        return WAITING_TIME
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введи дату как: 25.03.2026")
        return WAITING_DATE


async def plan_date_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["time"] = update.message.text.strip()
    await update.message.reply_text("📍 Введи город свидания:")
    return WAITING_CITY


async def plan_date_got_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["city"] = update.message.text.strip()
    await update.message.reply_text(
        "🎭 Какой формат свидания планируется?\n"
        "Например: ужин в ресторане, прогулка в парке, кино, пикник, домашний вечер…"
    )
    return WAITING_ACTIVITY_PREF


async def plan_date_got_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    activity = update.message.text.strip()
    
    data = get_couple_data(user_id)
    data["date_info"] = {
        "date": context.user_data["date"],
        "time": context.user_data["time"],
        "city": context.user_data["city"],
        "activity": activity,
    }
    
    await update.message.reply_text("✨ Создаю план свидания с AI...")
    
    d = data["date_info"]
    tips = await ask_claude(
        f"Мы планируем романтическое свидание:\n"
        f"- Дата: {d['date']}, Время: {d['time']}\n"
        f"- Город: {d['city']}\n"
        f"- Формат: {d['activity']}\n\n"
        "Дай 3 коротких совета как сделать это свидание незабываемым.",
        system="Ты романтический ассистент для влюблённых пар. Отвечай на русском."
    )
    
    await update.message.reply_text(
        f"✅ *Свидание запланировано!*\n\n"
        f"📅 {d['date']} в {d['time']}\n"
        f"📍 {d['city']} — {d['activity']}\n\n"
        f"💡 *Советы от AI:*\n{tips}\n\n"
        "Теперь пригласи свою половинку через кнопку *💌 Пригласить партнёра*!",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    # Уведомляем партнёра, если он уже в боте
    partner_id = data.get("partner_id")
    if partner_id:
        try:
            await context.bot.send_message(
                chat_id=partner_id,
                text=f"💕 *{data['planner_name']} запланировал свидание!*\n\n"
                     "Нажми *💕 Наше свидание* чтобы узнать детали!",
                parse_mode="Markdown",
                reply_markup=main_keyboard(partner_id)
            )
        except:
            pass
    
    return MAIN_MENU


# ─── Приглашение партнёра ────────────────────────────────────────────────────

async def invite_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if not is_planner(user_id):
        await update.message.reply_text(
            "Эта функция доступна только планировщику 😊",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    data = get_couple_data(user_id)
    
    # Генерируем уникальную ссылку-приглашение
    bot_username = (await context.bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=join_{user_id}"
    
    await update.message.reply_text(
        f"💌 *Пригласи свою половинку!*\n\n"
        f"Отправь ей/ему эту ссылку:\n"
        f"`{invite_link}`\n\n"
        f"Когда партнёр перейдёт по ссылке, я автоматически свяжу вас и расскажу ей/ему о свидании!",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


# ─── Обработка приглашения ───────────────────────────────────────────────────

async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка перехода по пригласительной ссылке."""
    # Если нет аргументов - это обычный /start
    if not context.args or len(context.args) == 0:
        return await start(update, context)
    
    arg = context.args[0]
    if not arg.startswith("join_"):
        return await start(update, context)
    
    planner_id = int(arg.replace("join_", ""))
    partner_id = update.effective_user.id
    partner_name = update.effective_user.first_name
    
    # Нельзя присоединиться к самому себе
    if planner_id == partner_id:
        await update.message.reply_text(
            "❌ Ты не можешь пригласить сам себя! 😄",
            reply_markup=main_keyboard(partner_id)
        )
        return MAIN_MENU
    
    # Получаем данные планировщика
    planner_data = get_couple_data(planner_id)
    
    # Связываем партнёра
    planner_data["partner_id"] = partner_id
    planner_data["partner_name"] = partner_name
    
    # Создаём данные для партнёра (указывающие на ту же пару)
    couples_data[partner_id] = planner_data
    
    # Приветствуем партнёра
    planner_name = planner_data.get("planner_name", "Твой партнёр")
    d = planner_data.get("date_info", {})
    
    if d:
        ai_welcome = await ask_claude(
            f"{planner_name} пригласил тебя в романтический бот!\n"
            f"Он запланировал свидание:\n"
            f"- Дата: {d.get('date', '—')}, Время: {d.get('time', '—')}\n"
            f"- Город: {d.get('city', '—')}\n"
            f"- Формат: {d.get('activity', '—')}\n\n"
            "Поприветствуй партнёра тепло и романтично! Скажи что поможешь подготовиться к свиданию.",
            system="Ты романтический AI-ассистент. Говори мило и тепло на русском."
        )
    else:
        ai_welcome = f"{planner_name} пригласил тебя! Скоро он запланирует свидание, и я помогу вам обоим подготовиться! 💕"
    
    await update.message.reply_text(
        f"💕 *Привет, {partner_name}!*\n\n{ai_welcome}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(partner_id)
    )
    
    # Уведомляем планировщика
    try:
        await context.bot.send_message(
            chat_id=planner_id,
            text=f"💕 *{partner_name} присоединился к боту!*\n\n"
                 "Теперь вы оба можете готовиться к свиданию вместе!",
            parse_mode="Markdown",
            reply_markup=main_keyboard(planner_id)
        )
    except:
        pass
    
    return MAIN_MENU


# ─── Функции для партнёра ────────────────────────────────────────────────────

async def weather_for_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Погода с AI-советом для партнёра."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    
    if not d.get("city") or not d.get("date"):
        await update.message.reply_text(
            "Партнёр ещё не указал город и дату свидания 😊",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    await update.message.reply_text("🌤 Загружаю погоду...")
    weather = await get_weather(d["city"], d["date"])
    
    ai_advice = await ask_claude(
        f"Погода на свидание: {weather}\n"
        f"Формат свидания: {d.get('activity', 'не указан')}\n\n"
        "Дай практичный совет партнёру как подготовиться с учётом погоды.",
        system="Ты романтический AI-ассистент. Отвечай на русском."
    )
    
    await update.message.reply_text(
        f"{weather}\n\n💕 *Совет для тебя:*\n{ai_advice}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


async def outfit_for_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """AI подбирает образ для партнёра."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    
    if not d:
        await update.message.reply_text(
            "Партнёр ещё не запланировал свидание 😊",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    await update.message.reply_text("✨ AI подбирает образ для тебя...")
    
    # Получаем погоду для контекста
    weather_hint = ""
    if d.get("city") and d.get("date"):
        try:
            weather = await get_weather(d["city"], d["date"])
            weather_hint = f"Погода: {weather}\n"
        except:
            pass
    
    advice = await ask_claude(
        f"Свидание:\n"
        f"- Формат: {d.get('activity', '—')}\n"
        f"- Город: {d.get('city', '—')}\n"
        f"- Дата: {d.get('date', '—')}\n"
        f"{weather_hint}\n"
        "Подбери стильный образ для девушки/парня на это свидание. "
        "Дай конкретные советы по одежде, аксессуарам, обуви. Учти погоду и формат.",
        system="Ты модный стилист для романтических свиданий. Отвечай на русском."
    )
    
    await update.message.reply_text(
        f"👗 *Твой образ на свидание:*\n\n{advice}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


async def where_and_what_for_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """AI рассказывает партнёру куда идти и что делать."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    
    if not d:
        await update.message.reply_text(
            "Партнёр ещё не запланировал свидание 😊",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    await update.message.reply_text("📍 Готовлю информацию...")
    
    info = await ask_claude(
        f"Свидание:\n"
        f"- Дата: {d['date']}, Время: {d['time']}\n"
        f"- Город: {d['city']}\n"
        f"- Формат: {d['activity']}\n\n"
        "Расскажи партнёру что его ждёт, дай советы по маршруту, "
        "предложи что можно взять с собой, как себя вести. Будь тёплым и романтичным.",
        system="Ты романтический AI-ассистент. Отвечай на русском."
    )
    
    await update.message.reply_text(
        f"📍 *Куда мы идём:*\n\n{info}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


async def surprise_for_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """AI предлагает идею сюрприза для партнёра планировщика."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    planner_name = data.get("planner_name", "твоего партнёра")
    
    await update.message.reply_text("✨ Придумываю сюрприз...")
    
    surprise = await ask_claude(
        f"Партнёр приглашает на свидание: {d.get('activity', 'романтическое свидание')} "
        f"в городе {d.get('city', '')}.\n\n"
        f"Придумай 3 милых идеи сюрприза для {planner_name}. "
        "Разные по масштабу: маленький жест, средний сюрприз, особенный момент.",
        system="Ты эксперт по романтике. Отвечай на русском."
    )
    
    await update.message.reply_text(
        f"🎁 *Идеи сюрприза для {planner_name}:*\n\n{surprise}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


async def compliment_for_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """AI генерирует романтичные фразы."""
    user_id = update.effective_user.id
    data = get_couple_data(user_id)
    planner_name = data.get("planner_name", "партнёра")
    
    await update.message.reply_text("✨ Генерирую романтичное сообщение...")
    
    phrase = await ask_claude(
        f"Напиши 3 красивых романтических фразы/комплимента для {planner_name}. "
        "Короткие, искренние, не банальные. На русском языке.",
        system="Ты романтичный поэт. Пиши красиво."
    )
    
    await update.message.reply_text(
        f"💬 *Романтичные фразы:*\n\n{phrase}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(user_id)
    )
    
    return MAIN_MENU


# ─── AI-ассистент (для обоих) ────────────────────────────────────────────────

async def ai_chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🤖 *AI-ассистент активен!*\n\n"
        "Задай любой вопрос о свидании, отношениях, подарках — отвечу!\n"
        "Напиши /stop чтобы вернуться в меню.",
        parse_mode="Markdown"
    )
    return WAITING_AI_CHAT


async def ai_chat_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    question = update.message.text.strip()
    
    if question.lower() in ["/stop", "стоп", "меню", "/menu"]:
        await update.message.reply_text(
            "Возвращаемся в главное меню 💕",
            reply_markup=main_keyboard(user_id)
        )
        return MAIN_MENU
    
    await update.message.reply_text("✨ Думаю...")
    
    data = get_couple_data(user_id)
    d = data.get("date_info", {})
    context_hint = ""
    if d:
        context_hint = (
            f"Контекст: пара планирует свидание {d.get('date','')} в {d.get('time','')} "
            f"в городе {d.get('city','')} — {d.get('activity','')}."
        )
    
    answer = await ask_claude(
        f"{context_hint}\n\nВопрос: {question}",
        system=(
            "Ты романтический AI-ассистент для влюблённой пары. "
            "Отвечай тепло, по-дружески, на русском языке."
        )
    )
    
    await update.message.reply_text(
        f"🤖 {answer}\n\n_(напиши /stop для возврата в меню)_",
        parse_mode="Markdown"
    )
    
    return WAITING_AI_CHAT


# ─── Fallback ────────────────────────────────────────────────────────────────

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Выбери действие из меню 💕",
        reply_markup=main_keyboard(user_id)
    )
    return MAIN_MENU


# ════════════════════════════════════════════════════════════════════════════
# СБОРКА БОТА
# ════════════════════════════════════════════════════════════════════════════

def main():
    # Настройка прокси (раскомментируй если нужен)
    # proxy_url = "http://proxy_address:port"  # или socks5://...
    # app = Application.builder().token(TELEGRAM_TOKEN).proxy_url(proxy_url).build()
    
    # Увеличиваем таймауты для медленного соединения
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", handle_join),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^🗓 Запланировать свидание$"), plan_date_start),
                MessageHandler(filters.Regex("^💌 Пригласить партнёра$"), invite_partner),
                MessageHandler(filters.Regex("^💕 Наше свидание$"), show_date_info),
                MessageHandler(filters.Regex("^☁️ Погода$"), weather_for_partner),
                MessageHandler(filters.Regex("^👗 Что надеть?$"), outfit_for_partner),
                MessageHandler(filters.Regex("^📍 Где и что делать?$"), where_and_what_for_partner),
                MessageHandler(filters.Regex("^🎁 Идея сюрприза$"), surprise_for_partner),
                MessageHandler(filters.Regex("^💬 Романтичная фраза$"), compliment_for_partner),
                MessageHandler(filters.Regex("^🤖 AI-ассистент$"), ai_chat_start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, unknown),
            ],
            WAITING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, plan_date_got_date)
            ],
            WAITING_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, plan_date_got_time)
            ],
            WAITING_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, plan_date_got_city)
            ],
            WAITING_ACTIVITY_PREF: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, plan_date_got_activity)
            ],
            WAITING_AI_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_reply)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    
    app.add_handler(conv_handler)
    
    logger.info("💕 Date Bot для пар запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()