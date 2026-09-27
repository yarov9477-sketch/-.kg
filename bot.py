import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp
)
from config import settings
import crud

logger = logging.getLogger("posylka_kg.bot")

bot = Bot(token=settings.BOT_TOKEN) if settings.BOT_TOKEN else None
dp = Dispatcher()

async def setup_bot_menu():
    """
    Автоматически настраивает нативную кнопку Web App в левом нижнем углу меню чата
    """
    if not bot or not settings.WEBAPP_URL:
        return
    try:
        url = settings.WEBAPP_URL
        if not url.startswith("https://") and not url.startswith("http://"):
            url = f"https://{url}"
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📦 Посылка.kg",
                web_app=WebAppInfo(url=url)
            )
        )
        logger.info("Chat Menu Button configured with URL: %s", url)
    except Exception as e:
        logger.warning(f"Could not set chat menu button: {e}")


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка для отправки номера телефона"""
    button = KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True)


def get_webapp_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Кнопка для открытия Telegram Web App"""
    url = settings.WEBAPP_URL
    if not url.startswith("https://") and not url.startswith("http://"):
        url = f"https://{url}"

    webapp_url = f"{url}?user_id={telegram_id}"
    
    button = InlineKeyboardButton(
        text="📦 Открыть Посылка.kg",
        web_app=WebAppInfo(url=webapp_url)
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


@dp.message(CommandStart())
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name or message.from_user.username or "Пользователь"

    user = await crud.get_or_create_user(telegram_id=user_id, full_name=full_name)

    # Проверка спам-блокировки
    anti_spam = await crud.check_and_record_anti_spam(user_id, "bot_start")
    if anti_spam.get("blocked"):
        mins = anti_spam["remaining_seconds"] // 60
        await message.answer(
            f"⛔ <b>Доступ временно ограничен</b>\n\n"
            f"Вы совершили слишком много действий. Повторите попытку через {mins} мин.",
            parse_mode="HTML"
        )
        return

    # Если номер телефона еще не привязан — запрашиваем
    if not user.get("phone_number"):
        await message.answer(
            f"Салам, <b>{full_name}</b>!\n\n"
            f"Добро пожаловать в сервис межгородских и международных передач <b>Посылка.kg</b> 🇰🇬\n\n"
            f"Для безопасности сделок и связи с водителями подтвердите ваш номер телефона, нажав кнопку ниже:",
            reply_markup=get_phone_keyboard(),
            parse_mode="HTML"
        )
        return

    # Если статус на модерации
    if user.get("status") == "PUMP_TO_MODERATION":
        await message.answer(
            "⏳ <b>Ваш аккаунт находится на проверке модератором.</b>\n"
            "После подтверждения номера вам откроется полный доступ.",
            parse_mode="HTML"
        )
        return

    # Готовый доступ к приложению
    await message.answer(
        f"Салам, <b>{full_name}</b>!\n\n"
        f"<b>Посылка.kg</b> — быстрые и надежные посылки по Кыргызстану, СНГ и РФ.\n\n"
        f"Нажмите кнопку ниже или используйте кнопку Меню слева внизу, чтобы открыть приложение:",
        reply_markup=get_webapp_keyboard(user_id),
        parse_mode="HTML"
    )


@dp.message(F.contact)
async def handle_contact(message: types.Message):
    contact = message.contact
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Пользователь"

    phone = contact.phone_number
    clean_phone, status = crud.normalize_and_validate_phone(phone)

    # Обновляем в БД
    await crud.get_or_create_user(
        telegram_id=user_id,
        full_name=full_name,
        phone_number=clean_phone
    )

    if status == "PUMP_TO_MODERATION":
        await message.answer(
            f"⚠️ Ваш номер <code>{clean_phone}</code> отправлен на проверку модератору.\n"
            f"Сервис работает с номерами КР (+996), РТ (+992), РУз (+998), РФ/РК (+7).\n"
            f"Ожидайте подтверждения.",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        if settings.ADMIN_ID and bot:
            try:
                await bot.send_message(
                    settings.ADMIN_ID,
                    f"🔔 <b>Требуется проверка номера!</b>\nПользователь: {full_name} (ID: {user_id})\nНомер: {clean_phone}"
                )
            except Exception as e:
                logger.error(f"Error sending to admin: {e}")
        return

    await message.answer(
        f"✅ Номер <b>{clean_phone}</b> успешно подтвержден!\n\n"
        f"Добро пожаловать в <b>Посылка.kg</b>.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await message.answer(
        "Нажмите на кнопку ниже, чтобы открыть приложение:",
        reply_markup=get_webapp_keyboard(user_id)
    )


@dp.message(Command("admin"))
async def handle_admin(message: types.Message):
    if message.from_user.id != settings.ADMIN_ID:
        return
    await message.answer(
        "🛠 <b>Панель администратора Посылка.kg</b>\n\n"
        "БД: kg.db активна.",
        parse_mode="HTML"
    )
