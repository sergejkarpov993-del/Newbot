import asyncio
import logging
import uuid
import urllib.parse
import json
import os
import sys
from datetime import datetime, timedelta
from collections import Counter
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import atexit

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
load_dotenv()

# ========== БЕЗОПАСНАЯ КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET")


# ========== ПРОВЕРКА КОНФИГУРАЦИИ ==========
def check_configuration():
    errors = []
    if not BOT_TOKEN:
        errors.append("❌ BOT_TOKEN не установлен")
    if ADMIN_ID == 0:
        errors.append("❌ ADMIN_ID не установлен")
    if not YOOMONEY_WALLET:
        errors.append("❌ YOOMONEY_WALLET не установлен")
    return errors


config_errors = check_configuration()
if config_errors:
    print("=" * 60)
    print("❌ ОШИБКА КОНФИГУРАЦИИ")
    print("=" * 60)
    for error in config_errors:
        print(error)
    print("\nℹ️  ИНСТРУКЦИЯ:")
    print("1. Создайте файл .env в папке с ботом")
    print("2. Заполните его по примеру из .env.example")
    print("3. Или установите переменные в Railway Dashboard")
    print("=" * 60)
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== БАЗЫ ДАННЫХ ==========
# Файлы для сохранения данных
PHOTOS_DB_FILE = "gallery_photos.json"
APPOINTMENTS_DB_FILE = "appointments_db.json"
USERS_DB_FILE = "users_db.json"
PENDING_PAYMENTS_FILE = "pending_payments.json"
CANCELLED_FILE = "cancelled_appointments.json"

# Хранилища данных (глобальные переменные)
users_db = {}
appointments_db = {}
pending_payments = {}
cancelled_appointments = []
gallery_photos = []


# ========== ЗАГРУЗКА И СОХРАНЕНИЕ ДАННЫХ ==========
def load_all_data():
    """Загружает все данные из файлов"""
    global appointments_db, users_db, pending_payments, cancelled_appointments, gallery_photos

    def load_json(file_path, default):
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"❌ Ошибка загрузки {file_path}: {e}")
                return default
        return default

    appointments_db = load_json(APPOINTMENTS_DB_FILE, {})
    users_db = load_json(USERS_DB_FILE, {})
    pending_payments = load_json(PENDING_PAYMENTS_FILE, {})
    cancelled_appointments = load_json(CANCELLED_FILE, [])
    gallery_photos = load_json(PHOTOS_DB_FILE, [])

    logger.info(
        f"✅ Данные загружены: {len(appointments_db)} записей, {len(users_db)} клиентов, {len(gallery_photos)} фото")


def save_all_data():
    """Сохраняет все данные в файлы"""
    try:
        def save_json(file_path, data):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        save_json(APPOINTMENTS_DB_FILE, appointments_db)
        save_json(USERS_DB_FILE, users_db)
        save_json(PENDING_PAYMENTS_FILE, pending_payments)
        save_json(CANCELLED_FILE, cancelled_appointments)
        save_json(PHOTOS_DB_FILE, gallery_photos)

        logger.info("💾 Все данные сохранены")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения данных: {e}")


# Загружаем данные при старте
load_all_data()

# Автоматическое сохранение при выходе
atexit.register(save_all_data)


# Автоматическое сохранение каждые 5 минут
async def auto_save_task():
    """Автоматическое сохранение каждые 5 минут"""
    while True:
        await asyncio.sleep(300)  # 5 минут
        try:
            save_all_data()
            logger.info("💾 Данные автоматически сохранены")
        except Exception as e:
            logger.error(f"❌ Ошибка автосохранения: {e}")


# ========== УСЛУГИ ==========
services_db = {
    'manicure': {
        'name': 'Маникюр',
        'price': 1500,
        'duration': 60,
        'description': '💅 Комплексный маникюр с покрытием гель-лаком'
    },
    'pedicure': {
        'name': 'Педикюр',
        'price': 2000,
        'duration': 90,
        'description': '🦶 Педикюр + уход за стопами + покрытие'
    },
    'cover': {
        'name': 'Покрытие',
        'price': 800,
        'duration': 30,
        'description': '✨ Обновление покрытия гель-лаком'
    }
}

# ========== ПОЛИТИКА ВОЗВРАТОВ ==========
REFUND_POLICY = {
    'more_than_24h': 1.0,
    '12_to_24h': 0.5,
    '6_to_12h': 0.3,
    '3_to_6h': 0.1,
    'less_than_3h': 0.0
}


# ========== СОСТОЯНИЯ ==========
class AppointmentState(StatesGroup):
    choose_service = State()
    choose_date = State()
    choose_time = State()
    enter_name = State()
    enter_phone = State()
    payment = State()


class CancelState(StatesGroup):
    choose_appointment = State()
    confirm_cancel = State()


class GalleryState(StatesGroup):
    waiting_photo = State()
    waiting_caption = State()
    confirm_delete = State()
    waiting_delete_number = State()


# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_free_slots(date, service_key):
    """Получить свободные слоты на указанную дату"""
    free_slots = []
    start_hour = 10
    end_hour = 20
    interval = 60

    service_duration = services_db[service_key]['duration']
    current_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=start_hour)
    end_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=end_hour)

    while current_time + timedelta(minutes=service_duration) <= end_time:
        time_str = current_time.strftime("%H:%M")
        date_key = date.strftime("%Y-%m-%d")

        is_free = True
        check_time = current_time
        for minute in range(0, service_duration, 30):
            check_slot = (check_time + timedelta(minutes=minute)).strftime("%H:%M")
            if appointments_db.get(date_key, {}).get(check_slot):
                is_free = False
                break

        if is_free:
            free_slots.append(time_str)

        current_time += timedelta(minutes=interval)

    return free_slots


def calculate_refund_amount(appointment_datetime, paid_amount):
    """Рассчитать сумму возврата"""
    time_left = appointment_datetime - datetime.now()
    hours_left = time_left.total_seconds() / 3600

    if hours_left > 24:
        refund_percent = REFUND_POLICY['more_than_24h']
    elif hours_left > 12:
        refund_percent = REFUND_POLICY['12_to_24h']
    elif hours_left > 6:
        refund_percent = REFUND_POLICY['6_to_12h']
    elif hours_left > 3:
        refund_percent = REFUND_POLICY['3_to_6h']
    else:
        refund_percent = REFUND_POLICY['less_than_3h']

    refund_amount = paid_amount * refund_percent
    penalty = paid_amount - refund_amount

    return {
        'refund_amount': round(refund_amount),
        'penalty': round(penalty),
        'percent': int(refund_percent * 100),
        'hours_left': round(hours_left, 1)
    }


def create_yoomoney_payment_link(amount, label, comment=""):
    """Создать ссылку для оплаты через ЮMoney"""
    params = {
        'receiver': YOOMONEY_WALLET,
        'sum': amount,
        'formComment': comment or '💅 Оплата услуги в салоне красоты',
        'short-dest': 'Оплата услуги',
        'label': label,
        'quickpay-form': 'shop',
        'targets': 'Оплата услуги в салоне',
        'paymentType': 'AC',
        'successURL': f'https://t.me/Probnik312Bot?start=payment_success_{label}'
    }

    query_string = urllib.parse.urlencode(params)
    return f"https://yoomoney.ru/quickpay/confirm.xml?{query_string}"


# ========== КЛАВИАТУРЫ ==========
def main_kb():
    """Главное меню"""
    keyboard = [
        [KeyboardButton(text="📅 Записаться")],
        [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="❌ Отменить запись")],
        [KeyboardButton(text="💰 Мои платежи")],
        [KeyboardButton(text="🖼 Наши работы")],
    ]

    if ADMIN_ID:
        keyboard.append([KeyboardButton(text="👑 Админ")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действия 👇"
    )


def services_kb():
    """Выбор услуги"""
    buttons = []
    for key, service in services_db.items():
        buttons.append([KeyboardButton(text=f"💅 {service['name']} - {service['price']}₽")])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def dates_kb():
    """Выбор даты"""
    buttons = []
    today = datetime.now().date()
    for i in range(7):
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m.%Y")
        buttons.append([KeyboardButton(text=date_str)])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def confirm_cancel_kb():
    """Подтверждение отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, отменить")],
            [KeyboardButton(text="❌ Нет, оставить")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )


def admin_main_kb():
    """Главное меню админки"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📋 Все записи")],
            [KeyboardButton(text="💰 Финансы"), KeyboardButton(text="🔄 Управление")],
            [KeyboardButton(text="🖼️ Галерея"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="⬅️ В меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Админ-панель 👑"
    )


def admin_management_kb():
    """Меню управления"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗑 Очистить старые записи")],
            [KeyboardButton(text="📤 Экспорт данных")],
            [KeyboardButton(text="🔄 Сбросить бота")],
            [KeyboardButton(text="📊 Статистика фото")],
            [KeyboardButton(text="⬅️ В админку")]
        ],
        resize_keyboard=True
    )


def gallery_admin_kb():
    """Меню галереи"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Добавить фото")],
            [KeyboardButton(text="🗑 Удалить фото")],
            [KeyboardButton(text="⬅️ В админку")]
        ],
        resize_keyboard=True
    )


# ========== ОСНОВНЫЕ КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    logger.info(f"User {message.from_user.id} started bot")
    args = message.text.split()

    if len(args) > 1 and args[1].startswith("payment_success_"):
        payment_id = args[1].replace("payment_success_", "")
        await handle_payment_success(message, payment_id)
    else:
        await message.answer(
            "✨ *Добро пожаловать в NailStudio!* ✨\n\n"
            "💅 *Премиум уход за ногтями*\n"
            "• Профессиональные мастера\n"
            "• Качественные материалы\n"
            "• Уютная атмосфера\n\n"
            "👇 Выберите действие:",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )


async def handle_payment_success(message: types.Message, payment_id: str):
    """Обработка успешной оплаты"""
    if payment_id in pending_payments:
        payment_data = pending_payments[payment_id]

        date_key = payment_data['date_obj'].strftime("%Y-%m-%d") if isinstance(payment_data['date_obj'], datetime) else \
            payment_data['date_obj']
        time_key = payment_data['time']

        if date_key not in appointments_db:
            appointments_db[date_key] = {}

        appointments_db[date_key][time_key] = {
            'user_id': payment_data['user_id'],
            'name': payment_data['name'],
            'phone': payment_data['phone'],
            'service': payment_data['service_name'],
            'service_key': payment_data['service_key'],
            'price': payment_data['price'],
            'payment_id': payment_id,
            'payment_time': datetime.now().isoformat(),
            'paid': True
        }

        users_db[payment_data['user_id']] = {
            'name': payment_data['name'],
            'phone': payment_data['phone']
        }

        # Сохраняем данные
        save_all_data()

        # Уведомление админу
        admin_text = (
            f"💰 *Новая оплаченная запись!*\n\n"
            f"👤 *Клиент:* {payment_data['name']}\n"
            f"📞 *Телефон:* {payment_data['phone']}\n"
            f"💅 *Услуга:* {payment_data['service_name']}\n"
            f"💰 *Сумма:* {payment_data['price']}₽\n"
            f"📅 *Дата:* {payment_data['date_display']}\n"
            f"⏰ *Время:* {payment_data['time']}\n\n"
            f"🆔 ID платежа: `{payment_id}`"
        )
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")

        # Сообщение клиенту
        await message.answer(
            f"🎉 *Запись успешно оплачена!*\n\n"
            f"✅ *Детали записи:*\n"
            f"• Услуга: {payment_data['service_name']}\n"
            f"• Сумма: {payment_data['price']}₽\n"
            f"• Дата: {payment_data['date_display']}\n"
            f"• Время: {payment_data['time']}\n\n"
            f"📍 *Адрес:* ул. Примерная, д. 1\n"
            f"📞 *Телефон:* +7 (999) 123-45-67\n\n"
            f"⚠️ *Важная информация:*\n"
            f"• Отменить запись можно не позднее чем за 1 час до визита\n"
            f"• При отмене менее чем за 1 час деньги не возвращаются\n\n"
            f"✨ *Ждём вас в салоне!* ✨",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )

        del pending_payments[payment_id]
    else:
        await message.answer(
            "✅ Платеж обработан. Проверьте «📋 Мои записи».",
            reply_markup=main_kb()
        )


# ========== ЗАПИСЬ НА УСЛУГУ ==========
@dp.message(F.text == "📅 Записаться")
async def start_appointment(message: types.Message, state: FSMContext):
    """Начать запись"""
    logger.info(f"User {message.from_user.id} started appointment")
    await state.set_state(AppointmentState.choose_service)
    await message.answer(
        "💅 *Выберите услугу:*\n\n"
        "👇 Нажмите на нужную услугу:",
        reply_markup=services_kb(),
        parse_mode="Markdown"
    )


@dp.message(AppointmentState.choose_service)
async def handle_service_selection(message: types.Message, state: FSMContext):
    """Обработка выбора услуги"""
    logger.info(f"User {message.from_user.id} selected service: {message.text}")

    # Проверка на кнопку "Назад"
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=main_kb())
        return

    for key, service in services_db.items():
        button_text = f"💅 {service['name']} - {service['price']}₽"

        if message.text == button_text:
            await state.update_data(
                service_key=key,
                service_name=service['name'],
                price=service['price'],
                duration=service['duration']
            )

            await state.set_state(AppointmentState.choose_date)

            await message.answer(
                f"✅ *{service['name']}*\n"
                f"💰 *Цена:* {service['price']}₽\n"
                f"⏱ *Длительность:* {service['duration']} мин\n"
                f"📝 *Описание:* {service['description']}\n\n"
                f"👇 *Выберите дату:*",
                reply_markup=dates_kb(),
                parse_mode="Markdown"
            )
            return

    await message.answer("Выберите услугу:", reply_markup=services_kb())


@dp.message(AppointmentState.choose_date)
async def handle_date_selection(message: types.Message, state: FSMContext):
    """Обработка выбора даты"""
    logger.info(f"User {message.from_user.id} selected date: {message.text}")

    # Проверка на кнопку "Назад"
    if message.text == "⬅️ Назад":
        await state.set_state(AppointmentState.choose_service)
        await message.answer("💅 Выберите услугу:", reply_markup=services_kb())
        return

    try:
        # Проверяем, что это дата в правильном формате
        selected_date = datetime.strptime(message.text, "%d.%m.%Y").date()
        today = datetime.now().date()

        # Проверяем, что дата не в прошлом и не дальше 7 дней
        if selected_date < today:
            await message.answer("❌ Нельзя выбрать прошедшую дату. Выберите другую дату:", reply_markup=dates_kb())
            return

        if (selected_date - today).days > 6:
            await message.answer("❌ Можно записываться только на ближайшие 7 дней. Выберите другую дату:",
                                 reply_markup=dates_kb())
            return

        data = await state.get_data()
        service_key = data.get('service_key')

        if not service_key:
            await message.answer("❌ Ошибка: услуга не выбрана. Начните сначала.", reply_markup=main_kb())
            await state.clear()
            return

        free_slots = get_free_slots(selected_date, service_key)

        if not free_slots:
            await message.answer(
                f"❌ *На {message.text} нет свободных слотов*\n\n"
                f"Пожалуйста, выберите другую дату:",
                reply_markup=dates_kb(),
                parse_mode="Markdown"
            )
            return

        await state.update_data(
            date_obj=selected_date,
            date_display=message.text
        )

        # Создаем клавиатуру с временными слотами
        time_buttons = []
        for i in range(0, len(free_slots), 3):
            row = []
            for slot in free_slots[i:i + 3]:
                row.append(KeyboardButton(text=slot))
            time_buttons.append(row)
        time_buttons.append([KeyboardButton(text="⬅️ Назад")])

        time_kb = ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True)

        await state.set_state(AppointmentState.choose_time)
        await message.answer(
            f"✅ *Дата: {message.text}*\n\n"
            f"👇 *Выберите время:*",
            reply_markup=time_kb,
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("❌ Неверный формат даты. Выберите дату из списка:", reply_markup=dates_kb())


@dp.message(AppointmentState.choose_time)
async def handle_time_selection(message: types.Message, state: FSMContext):
    """Обработка выбора времени"""
    logger.info(f"User {message.from_user.id} selected time: {message.text}")

    # Проверка на кнопку "Назад"
    if message.text == "⬅️ Назад":
        await state.set_state(AppointmentState.choose_date)
        await message.answer("📅 Выберите дату:", reply_markup=dates_kb())
        return

    # Проверяем формат времени HH:MM
    if not ":" in message.text or len(message.text) != 5:
        await message.answer("❌ Неверный формат времени. Выберите время из списка.")
        return

    data = await state.get_data()
    selected_date = data.get('date_obj')
    service_key = data.get('service_key')

    if not selected_date or not service_key:
        await message.answer("❌ Ошибка: данные не найдены. Начните сначала.", reply_markup=main_kb())
        await state.clear()
        return

    # Проверяем, что выбранное время доступно
    free_slots = get_free_slots(selected_date, service_key)
    if message.text not in free_slots:
        await message.answer("❌ Это время уже занято. Выберите другое время.")
        return

    await state.update_data(time=message.text)
    await state.set_state(AppointmentState.enter_name)

    await message.answer(
        f"✅ *Время: {message.text}*\n\n"
        f"👤 *Введите ваше имя:*\n"
        f"(например: Анна Иванова)",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(AppointmentState.enter_name)
async def handle_name_input(message: types.Message, state: FSMContext):
    """Обработка ввода имени"""
    logger.info(f"User {message.from_user.id} entered name: {message.text}")

    # Проверка на кнопку "Назад"
    if message.text == "⬅️ Назад":
        data = await state.get_data()

        # Создаем клавиатуру с временными слотами
        free_slots = get_free_slots(data['date_obj'], data['service_key'])
        time_buttons = []
        for i in range(0, len(free_slots), 3):
            row = []
            for slot in free_slots[i:i + 3]:
                row.append(KeyboardButton(text=slot))
            time_buttons.append(row)
        time_buttons.append([KeyboardButton(text="⬅️ Назад")])

        time_kb = ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True)

        await state.set_state(AppointmentState.choose_time)
        await message.answer("⏰ Выберите время:", reply_markup=time_kb)
        return

    # Проверяем, что имя не пустое
    if len(message.text.strip()) < 2:
        await message.answer("❌ Имя должно содержать минимум 2 символа. Введите ваше имя:")
        return

    await state.update_data(name=message.text.strip())
    await state.set_state(AppointmentState.enter_phone)

    await message.answer(
        f"✅ *Имя: {message.text.strip()}*\n\n"
        f"📞 *Введите ваш номер телефона:*\n"
        f"(например: +79161234567 или 89161234567)",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(AppointmentState.enter_phone)
async def handle_phone_input(message: types.Message, state: FSMContext):
    """Обработка ввода телефона"""
    logger.info(f"User {message.from_user.id} entered phone: {message.text}")

    # Проверка на кнопку "Назад"
    if message.text == "⬅️ Назад":
        await state.set_state(AppointmentState.enter_name)
        await message.answer("👤 Введите ваше имя:")
        return

    # Проверяем формат телефона
    phone = message.text.strip()
    # Удаляем все нецифровые символы кроме +
    cleaned_phone = ''.join(filter(lambda x: x.isdigit() or x == '+', phone))

    if len(cleaned_phone) < 10:
        await message.answer("❌ Неверный формат телефона. Введите номер в формате +79161234567 или 89161234567:")
        return

    await state.update_data(phone=cleaned_phone)

    # Получаем все данные
    data = await state.get_data()

    # Генерируем уникальный ID для платежа
    payment_id = str(uuid.uuid4())[:8]

    # Сохраняем данные в ожидании оплаты
    pending_payments[payment_id] = {
        'user_id': message.from_user.id,
        'name': data['name'],
        'phone': data['phone'],
        'service_name': data['service_name'],
        'service_key': data['service_key'],
        'price': data['price'],
        'date_obj': data['date_obj'],
        'date_display': data['date_display'],
        'time': data['time'],
        'created_at': datetime.now().isoformat()
    }

    # Создаем ссылку для оплаты
    payment_link = create_yoomoney_payment_link(
        amount=data['price'],
        label=payment_id,
        comment=f"Оплата услуги {data['service_name']} на {data['date_display']} {data['time']}"
    )

    # Сохраняем данные
    save_all_data()

    # ДОБАВЛЯЕМ ПРЕДУПРЕЖДЕНИЕ ОБ ОТМЕНЕ ЗА 1 ЧАС
    cancellation_warning = (
        f"\n\n⚠️ *Важная информация:*\n"
        f"• Отменить запись можно не позднее чем за 1 час до визита\n"
        f"• При отмене менее чем за 1 час деньги не возвращаются\n"
        f"• При отмене заранее возможен частичный возврат средств"
    )

    # Отправляем сообщение с подтверждением и кнопкой оплаты
    confirmation_text = (
        f"✅ *Все данные заполнены!*\n\n"
        f"📋 *Детали записи:*\n"
        f"• Услуга: {data['service_name']}\n"
        f"• Цена: {data['price']}₽\n"
        f"• Дата: {data['date_display']}\n"
        f"• Время: {data['time']}\n"
        f"• Имя: {data['name']}\n"
        f"• Телефон: {data['phone']}\n\n"
        f"💳 *Для подтверждения записи необходимо произвести оплату.*\n\n"
        f"📍 *После оплаты запишитесь в наше расписание!*\n"
        f"📞 *По всем вопросам: +7 (999) 123-45-67*"
        f"{cancellation_warning}"
    )

    # Создаем inline-кнопку для оплаты
    payment_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить онлайн", url=payment_link)],
            [InlineKeyboardButton(text="✅ Я оплатил (ТЕСТ)", callback_data=f"check_payment_{payment_id}")]
        ]
    )

    await message.answer(
        confirmation_text,
        reply_markup=payment_keyboard,
        parse_mode="Markdown"
    )

    await state.set_state(AppointmentState.payment)


@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment_handler(callback: types.CallbackQuery, state: FSMContext):
    """ТЕСТОВЫЙ РЕЖИМ: обработка нажатия "Я оплатил" - сразу подтверждаем"""
    payment_id = callback.data.replace("check_payment_", "")

    await callback.answer("✅ Оплата подтверждена! Создаю запись...")

    if payment_id in pending_payments:
        payment_data = pending_payments[payment_id]

        date_key = payment_data['date_obj'].strftime("%Y-%m-%d") if isinstance(payment_data['date_obj'], datetime) else \
            payment_data['date_obj']
        time_key = payment_data['time']

        if date_key not in appointments_db:
            appointments_db[date_key] = {}

        # СОЗДАЕМ ЗАПИСЬ В БАЗЕ ДАННЫХ
        appointments_db[date_key][time_key] = {
            'user_id': payment_data['user_id'],
            'name': payment_data['name'],
            'phone': payment_data['phone'],
            'service': payment_data['service_name'],
            'service_key': payment_data['service_key'],
            'price': payment_data['price'],
            'payment_id': payment_id,
            'payment_time': datetime.now().isoformat(),
            'paid': True
        }

        users_db[payment_data['user_id']] = {
            'name': payment_data['name'],
            'phone': payment_data['phone']
        }

        # Сохраняем данные
        save_all_data()

        # УВЕДОМЛЯЕМ АДМИНИСТРАТОРА
        admin_text = (
            f"💰 *Новая оплаченная запись!*\n\n"
            f"👤 *Клиент:* {payment_data['name']}\n"
            f"📞 *Телефон:* {payment_data['phone']}\n"
            f"💅 *Услуга:* {payment_data['service_name']}\n"
            f"💰 *Сумма:* {payment_data['price']}₽\n"
            f"📅 *Дата:* {payment_data['date_display']}\n"
            f"⏰ *Время:* {payment_data['time']}\n\n"
            f"🆔 ID платежа: `{payment_id}`\n"
            f"✅ *Способ:* Тестовый режим (кнопка 'Я оплатил')"
        )
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")

        # Сообщение клиенту
        await callback.message.edit_text(
            f"🎉 *Запись успешно оплачена!*\n\n"
            f"✅ *Детали записи:*\n"
            f"• Услуга: {payment_data['service_name']}\n"
            f"• Сумма: {payment_data['price']}₽\n"
            f"• Дата: {payment_data['date_display']}\n"
            f"• Время: {payment_data['time']}\n\n"
            f"📍 *Адрес:* ул. Примерная, д. 1\n"
            f"📞 *Телефон:* +7 (999) 123-45-67\n\n"
            f"⚠️ *Важная информация:*\n"
            f"• Отменить запись можно не позднее чем за 1 час до визита\n"
            f"• При отмене менее чем за 1 час деньги не возвращаются\n\n"
            f"✨ *Ждём вас в салоне!* ✨",
            parse_mode="Markdown"
        )

        # Отправляем отдельное сообщение с главным меню
        await callback.message.answer(
            "🏠 *Главное меню:*",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )

        del pending_payments[payment_id]
        await state.clear()
    else:
        await callback.answer("❌ Платеж не найден")


@dp.message(F.text == "⬅️ Назад")
async def back_handler(message: types.Message, state: FSMContext):
    """Кнопка Назад"""
    current_state = await state.get_state()

    if not current_state:
        await message.answer("🏠 Главное меню:", reply_markup=main_kb())
        return

    if current_state == AppointmentState.choose_date.state:
        await state.set_state(AppointmentState.choose_service)
        await message.answer("💅 Выберите услугу:", reply_markup=services_kb())

    elif current_state == AppointmentState.choose_time.state:
        await state.set_state(AppointmentState.choose_date)
        await message.answer("📅 Выберите дату:", reply_markup=dates_kb())

    elif current_state == AppointmentState.enter_name.state:
        await state.set_state(AppointmentState.choose_time)
        data = await state.get_data()
        free_slots = get_free_slots(data['date_obj'], data['service_key'])

        time_buttons = []
        for i in range(0, len(free_slots), 3):
            row = []
            for slot in free_slots[i:i + 3]:
                row.append(KeyboardButton(text=slot))
            time_buttons.append(row)
        time_buttons.append([KeyboardButton(text="⬅️ Назад")])

        await message.answer("⏰ Выберите время:",
                             reply_markup=ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True))

    elif current_state == AppointmentState.enter_phone.state:
        await state.set_state(AppointmentState.enter_name)
        await message.answer("👤 Введите ваше имя:")

    elif current_state in [CancelState.choose_appointment.state, CancelState.confirm_cancel.state]:
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=main_kb())

    else:
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=main_kb())


# ========== МОИ ЗАПИСИ ==========
@dp.message(F.text == "📋 Мои записи")
async def my_appointments(message: types.Message):
    """Показать мои записи"""
    user_id = str(message.from_user.id)

    # Ищем записи пользователя
    user_appointments = []
    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if str(appointment.get('user_id')) == user_id:
                try:
                    date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                    status = "✅ Оплачено" if appointment.get('paid') else "⏳ Ожидает оплаты"
                    user_appointments.append(
                        f"📅 {date_display} {time_key}\n"
                        f"💅 {appointment.get('service', 'Неизвестно')}\n"
                        f"💰 {appointment.get('price', 0)}₽ - {status}\n"
                        f"📞 {appointment.get('phone', 'Не указан')}\n"
                    )
                except:
                    continue

    if user_appointments:
        text = "📋 *Ваши записи:*\n\n" + "\n\n".join(user_appointments)
        text += f"\n\nВсего записей: {len(user_appointments)}"
    else:
        text = "📭 У вас нет активных записей.\n\nЗапишитесь на услугу через меню «📅 Записаться»"

    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")


# ========== ОТМЕНА ЗАПИСИ ==========
@dp.message(F.text == "❌ Отменить запись")
async def cancel_appointment_start(message: types.Message, state: FSMContext):
    """Начать отмену записи"""
    user_id = str(message.from_user.id)

    # Ищем записи пользователя
    user_appointments = []
    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if str(appointment.get('user_id')) == user_id and appointment.get('paid'):
                try:
                    date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                    appointment_datetime = datetime.strptime(f"{date_key} {time_key}", "%Y-%m-%d %H:%M")

                    # Рассчитываем возврат
                    refund_info = calculate_refund_amount(appointment_datetime, appointment.get('price', 0))

                    user_appointments.append({
                        'date_key': date_key,
                        'time_key': time_key,
                        'date_display': date_display,
                        'time': time_key,
                        'service': appointment.get('service', 'Неизвестно'),
                        'price': appointment.get('price', 0),
                        'appointment_datetime': appointment_datetime,
                        'refund_info': refund_info,
                        'display': f"📅 {date_display} {time_key} - {appointment.get('service', 'Неизвестно')} - {appointment.get('price', 0)}₽\nВозврат: {refund_info['refund_amount']}₽ ({refund_info['percent']}%)"
                    })
                except Exception as e:
                    logger.error(f"Ошибка обработки записи для отмены: {e}")
                    continue

    if not user_appointments:
        await message.answer(
            "📭 У вас нет оплаченных записей для отмены.\n\n"
            "Отменить можно только оплаченные записи.",
            reply_markup=main_kb()
        )
        return

    # Сохраняем записи в состояние
    await state.update_data(user_appointments=user_appointments)

    # Создаем клавиатуру с записями
    buttons = []
    for i, appt in enumerate(user_appointments[:5]):  # Ограничиваем 5 записями
        buttons.append([KeyboardButton(text=f"❌ {i + 1}. {appt['date_display']} {appt['time']}")])
    buttons.append([KeyboardButton(text="⬅️ Назад")])

    cancel_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await state.set_state(CancelState.choose_appointment)
    await message.answer(
        "🗑 *Выберите запись для отмены:*\n\n"
        "👇 Нажмите на запись, которую хотите отменить:",
        reply_markup=cancel_kb,
        parse_mode="Markdown"
    )


@dp.message(CancelState.choose_appointment)
async def handle_appointment_selection(message: types.Message, state: FSMContext):
    """Обработка выбора записи для отмены"""
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=main_kb())
        return

    data = await state.get_data()
    user_appointments = data.get('user_appointments', [])

    # Пытаемся определить, какую запись выбрал пользователь
    selected_index = -1

    # Проверяем, если текст содержит номер
    for i in range(len(user_appointments)):
        if f"{i + 1}." in message.text:
            selected_index = i
            break

    if selected_index == -1:
        # Ищем по дате и времени
        for i, appt in enumerate(user_appointments):
            if appt['date_display'] in message.text and appt['time'] in message.text:
                selected_index = i
                break

    if selected_index == -1 or selected_index >= len(user_appointments):
        await message.answer("❌ Не удалось определить выбранную запись. Попробуйте еще раз.")
        return

    selected_appointment = user_appointments[selected_index]

    # Сохраняем выбранную запись
    await state.update_data(selected_index=selected_index, selected_appointment=selected_appointment)

    # Показываем информацию о возврате
    refund_info = selected_appointment['refund_info']

    confirmation_text = (
        f"⚠️ *Подтверждение отмены*\n\n"
        f"📋 *Детали записи:*\n"
        f"• Дата: {selected_appointment['date_display']}\n"
        f"• Время: {selected_appointment['time']}\n"
        f"• Услуга: {selected_appointment['service']}\n"
        f"• Сумма: {selected_appointment['price']}₽\n\n"
        f"💰 *Возврат средств:*\n"
        f"• До отмены: {refund_info['hours_left']} часов\n"
        f"• Процент возврата: {refund_info['percent']}%\n"
        f"• Сумма возврата: {refund_info['refund_amount']}₽\n"
        f"• Удерживается: {refund_info['penalty']}₽\n\n"
        f"❓ *Вы уверены, что хотите отменить эту запись?*"
    )

    await state.set_state(CancelState.confirm_cancel)
    await message.answer(
        confirmation_text,
        reply_markup=confirm_cancel_kb(),
        parse_mode="Markdown"
    )


@dp.message(CancelState.confirm_cancel)
async def confirm_cancellation(message: types.Message, state: FSMContext):
    """Подтверждение отмены записи"""
    if message.text == "⬅️ Назад":
        await state.set_state(CancelState.choose_appointment)

        data = await state.get_data()
        user_appointments = data.get('user_appointments', [])

        # Создаем клавиатуру заново
        buttons = []
        for i, appt in enumerate(user_appointments[:5]):
            buttons.append([KeyboardButton(text=f"❌ {i + 1}. {appt['date_display']} {appt['time']}")])
        buttons.append([KeyboardButton(text="⬅️ Назад")])

        cancel_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

        await message.answer(
            "🗑 Выберите запись для отмены:",
            reply_markup=cancel_kb
        )
        return

    if message.text == "✅ Да, отменить":
        data = await state.get_data()
        selected_index = data.get('selected_index')
        selected_appointment = data.get('selected_appointment')
        user_appointments = data.get('user_appointments', [])

        if selected_index is None or not selected_appointment:
            await message.answer("❌ Ошибка: данные о записи не найдены.", reply_markup=main_kb())
            await state.clear()
            return

        try:
            # Удаляем запись из базы
            date_key = selected_appointment['date_key']
            time_key = selected_appointment['time_key']

            if date_key in appointments_db and time_key in appointments_db[date_key]:
                # Сохраняем информацию об отмененной записи
                cancelled_appointment = appointments_db[date_key][time_key].copy()
                cancelled_appointment.update({
                    'cancelled_at': datetime.now().isoformat(),
                    'cancelled_by': message.from_user.id,
                    'refund_amount': selected_appointment['refund_info']['refund_amount'],
                    'penalty': selected_appointment['refund_info']['penalty'],
                    'original_date': date_key,
                    'original_time': time_key
                })

                cancelled_appointments.append(cancelled_appointment)

                # Удаляем запись
                del appointments_db[date_key][time_key]

                # Если на эту дату больше нет записей, удаляем дату
                if not appointments_db[date_key]:
                    del appointments_db[date_key]

                # Сохраняем данные
                save_all_data()

                # Уведомляем админа
                admin_notification = (
                    f"🗑 *Запись отменена!*\n\n"
                    f"👤 *Клиент:* {cancelled_appointment.get('name')}\n"
                    f"📞 *Телефон:* {cancelled_appointment.get('phone')}\n"
                    f"💅 *Услуга:* {cancelled_appointment.get('service')}\n"
                    f"💰 *Было оплачено:* {cancelled_appointment.get('price')}₽\n"
                    f"↩️ *Возврат:* {selected_appointment['refund_info']['refund_amount']}₽\n"
                    f"📅 *Дата:* {selected_appointment['date_display']}\n"
                    f"⏰ *Время:* {selected_appointment['time']}\n"
                    f"⏱ *Отменено через:* {selected_appointment['refund_info']['hours_left']} часов до записи"
                )

                await bot.send_message(ADMIN_ID, admin_notification, parse_mode="Markdown")

                # Сообщение пользователю
                await message.answer(
                    f"✅ *Запись успешно отменена!*\n\n"
                    f"📋 *Детали:*\n"
                    f"• Дата: {selected_appointment['date_display']}\n"
                    f"• Время: {selected_appointment['time']}\n"
                    f"• Услуга: {selected_appointment['service']}\n\n"
                    f"💰 *Возврат средств:*\n"
                    f"• Возвращено: {selected_appointment['refund_info']['refund_amount']}₽\n"
                    f"• Удержано: {selected_appointment['refund_info']['penalty']}₽\n\n"
                    f"📞 *Возврат средств будет осуществлен в течение 3 рабочих дней.*\n"
                    f"По всем вопросам: +7 (999) 123-45-67",
                    reply_markup=main_kb(),
                    parse_mode="Markdown"
                )
            else:
                await message.answer("❌ Запись уже была удалена или не найдена.", reply_markup=main_kb())

        except Exception as e:
            logger.error(f"Ошибка при отмене записи: {e}")
            await message.answer(f"❌ Ошибка при отмене записи: {str(e)[:100]}", reply_markup=main_kb())

        await state.clear()

    elif message.text == "❌ Нет, оставить":
        await message.answer(
            "✅ Отмена отменена. Запись сохранена.",
            reply_markup=main_kb()
        )
        await state.clear()

    else:
        await message.answer(
            "❌ Пожалуйста, выберите действие:\n"
            "• ✅ Да, отменить - для отмены записи\n"
            "• ❌ Нет, оставить - чтобы оставить запись\n"
            "• ⬅️ Назад - чтобы выбрать другую запись"
        )


# ========== МОИ ПЛАТЕЖИ ==========
@dp.message(F.text == "💰 Мои платежи")
async def my_payments(message: types.Message):
    """Показать мои платежи"""
    user_id = str(message.from_user.id)

    # Ищем платежи пользователя
    user_payments = []

    # Платежи из активных записей
    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if str(appointment.get('user_id')) == user_id:
                try:
                    date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                    status = "✅ Оплачено" if appointment.get('paid') else "⏳ Ожидает оплаты"
                    payment_time = appointment.get('payment_time', 'Неизвестно')
                    if payment_time != 'Неизвестно':
                        try:
                            payment_dt = datetime.fromisoformat(payment_time)
                            payment_time = payment_dt.strftime("%d.%m.%Y %H:%M")
                        except:
                            pass

                    user_payments.append(
                        f"💳 *{status}*\n"
                        f"📅 {date_display} {time_key}\n"
                        f"💅 {appointment.get('service', 'Неизвестно')}\n"
                        f"💰 {appointment.get('price', 0)}₽\n"
                        f"🕒 {payment_time}"
                    )
                except:
                    continue

    # Отмененные записи с возвратами
    for appt in cancelled_appointments:
        if str(appt.get('user_id')) == user_id:
            try:
                date_display = appt.get('original_date', 'Неизвестно')
                if date_display != 'Неизвестно':
                    try:
                        date_display = datetime.strptime(date_display, "%Y-%m-%d").strftime("%d.%m.%Y")
                    except:
                        pass

                user_payments.append(
                    f"↩️ *Возврат средств*\n"
                    f"📅 {date_display} {appt.get('original_time', '')}\n"
                    f"💅 {appt.get('service', 'Неизвестно')}\n"
                    f"💰 Возвращено: {appt.get('refund_amount', 0)}₽\n"
                    f"🕒 Отменено: {appt.get('cancelled_at', 'Неизвестно')[:16]}"
                )
            except:
                continue

    if user_payments:
        text = "💰 *История ваших платежей:*\n\n" + "\n\n".join(user_payments)
        text += f"\n\nВсего операций: {len(user_payments)}"
    else:
        text = "📭 У вас еще не было платежей.\n\nОплатите свою первую запись!"

    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")


# ========== НАШИ РАБОТЫ ==========
@dp.message(F.text == "🖼 Наши работы")
async def show_gallery(message: types.Message):
    """Показать галерею работ"""
    if not gallery_photos:
        await message.answer(
            "📭 В галерее пока нет работ.\n\n"
            "Скоро мы добавим фотографии наших прекрасных работ! 💅",
            reply_markup=main_kb()
        )
        return

    await message.answer(
        "🖼 *Наши работы*\n\n"
        f"✨ *Посмотрите примеры наших работ:*\n"
        f"Всего фото в галерее: {len(gallery_photos)}",
        reply_markup=main_kb(),
        parse_mode="Markdown"
    )

    # Отправляем несколько последних фото (максимум 5)
    photos_to_show = min(5, len(gallery_photos))

    for i in range(photos_to_show):
        try:
            photo_data = gallery_photos[-(i + 1)]  # Берем с конца (последние добавленные)

            caption = ""
            if photo_data.get('caption'):
                caption = f"💅 {photo_data.get('caption')}"

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_data['file_id'],
                caption=caption
            )

            await asyncio.sleep(0.5)  # Задержка между отправками

        except Exception as e:
            logger.error(f"Ошибка отправки фото из галереи: {e}")
            continue

    if photos_to_show < len(gallery_photos):
        await message.answer(
            f"✨ *И ещё {len(gallery_photos) - photos_to_show} прекрасных работ в нашей галерее!*\n\n"
            f"💅 *Запишитесь к нам и станьте следующей красавицей в нашей галерее!*",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )


# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(F.text == "👑 Админ")
async def admin_panel(message: types.Message):
    """Главная админ-панель"""
    logger.info(f"User {message.from_user.id} accessed admin panel")

    if str(message.from_user.id) != str(ADMIN_ID):
        logger.warning(f"User {message.from_user.id} tried to access admin panel")
        await message.answer("❌ Доступ запрещен")
        return

    total_appointments = sum(len(times) for times in appointments_db.values())
    paid_appointments = sum(
        1 for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )
    total_revenue = sum(
        appt.get('price', 0)
        for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )

    await message.answer(
        f"👑 *Админ-панель*\n\n"
        f"📊 *Статистика:*\n"
        f"• Активных записей: {total_appointments}\n"
        f"• Оплачено: {paid_appointments}\n"
        f"• Выручка: {total_revenue}₽\n"
        f"• Отмен: {len(cancelled_appointments)}\n"
        f"• Фото в галерее: {len(gallery_photos)}\n\n"
        f"👇 Выберите раздел:",
        reply_markup=admin_main_kb(),
        parse_mode="Markdown"
    )


# ========== СТАТИСТИКА ==========
@dp.message(F.text == "📊 Статистика")
async def admin_statistics(message: types.Message):
    """Детальная статистика"""
    logger.info(f"User {message.from_user.id} accessed statistics")

    if str(message.from_user.id) != str(ADMIN_ID):
        return

    # Вычисляем статистику
    total_appointments = sum(len(times) for times in appointments_db.values())
    paid_appointments = sum(
        1 for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )
    total_revenue = sum(
        appt.get('price', 0)
        for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )

    today = datetime.now().strftime("%Y-%m-%d")
    today_appointments = len(appointments_db.get(today, {}))

    # Исправленный расчет возвратов
    total_refund = 0
    for appt in cancelled_appointments:
        if isinstance(appt, dict):
            refund = appt.get('refund_amount', 0)
            if isinstance(refund, (int, float)):
                total_refund += refund

    stats_text = f"📊 *Детальная статистика*\n\n"
    stats_text += f"📅 *Сегодня ({datetime.now().strftime('%d.%m.%Y')}):*\n"
    stats_text += f"• Записей: {today_appointments}\n"
    stats_text += f"• Оплачено: {sum(1 for appt in appointments_db.get(today, {}).values() if appt.get('paid', False))}\n\n"

    stats_text += f"📈 *Общая:*\n"
    stats_text += f"• Клиентов: {len(users_db)}\n"
    if paid_appointments > 0:
        avg_check = total_revenue // paid_appointments
    else:
        avg_check = 0
    stats_text += f"• Средний чек: {avg_check}₽\n"
    stats_text += f"• Возвраты: {total_refund}₽\n\n"

    stats_text += f"💰 *Финансы:*\n"
    stats_text += f"• Ожидают оплаты: {len(pending_payments)}\n"
    stats_text += f"• ЮMoney кошелек: `{YOOMONEY_WALLET}`\n\n"

    stats_text += f"📊 *Дополнительно:*\n"
    stats_text += f"• Отмененных записей: {len(cancelled_appointments)}\n"
    stats_text += f"• Фото в галерее: {len(gallery_photos)}"

    await message.answer(stats_text, reply_markup=admin_main_kb(), parse_mode="Markdown")


@dp.message(F.text == "📋 Все записи")
async def all_appointments(message: types.Message):
    """Все записи в системе"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    if not appointments_db:
        await message.answer("📭 Нет активных записей.", reply_markup=admin_main_kb())
        return

    all_appts = []
    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            try:
                date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                status = "✅ Оплачено" if appointment.get('paid') else "⏳ Не оплачено"
                all_appts.append(
                    f"📅 {date_display} {time_key} - {appointment.get('service', 'Неизвестно')} - {appointment.get('name', 'Неизвестно')} - {status}")
            except:
                continue

    if all_appts:
        text = "📋 *Все записи:*\n\n" + "\n".join(all_appts[:20])
        if len(all_appts) > 20:
            text += f"\n\n... и ещё {len(all_appts) - 20} записей"
    else:
        text = "📭 Нет записей."

    await message.answer(text, reply_markup=admin_main_kb(), parse_mode="Markdown")


@dp.message(F.text == "💰 Финансы")
async def admin_finances(message: types.Message):
    """Финансовая статистика"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    # Вычисляем переменные внутри функции
    total_revenue = sum(
        appt.get('price', 0)
        for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )

    paid_appointments = sum(
        1 for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )

    total_refund = 0
    for appt in cancelled_appointments:
        if isinstance(appt, dict):
            refund = appt.get('refund_amount', 0)
            if isinstance(refund, (int, float)):
                total_refund += refund

    net_income = total_revenue - total_refund

    finances_text = f"💰 *Финансы*\n\n"
    finances_text += f"💸 *Доходы:*\n"
    finances_text += f"• Общая выручка: {total_revenue}₽\n"
    finances_text += f"• Чистая прибыль: {net_income}₽\n\n"

    finances_text += f"↩️ *Расходы:*\n"
    finances_text += f"• Возвраты: {total_refund}₽\n\n"

    # Исправленная формула для среднего чека
    avg_check = total_revenue // max(paid_appointments, 1) if paid_appointments > 0 else 0

    # Исправленная формула для конверсии
    total_appointments = sum(len(times) for times in appointments_db.values())
    conversion = (paid_appointments / max(total_appointments, 1)) * 100 if total_appointments > 0 else 0

    finances_text += f"📊 *Показатели:*\n"
    finances_text += f"• Средний чек: {avg_check}₽\n"
    finances_text += f"• Конверсия в оплату: {conversion:.1f}%\n\n"

    finances_text += f"📈 *Информация:*\n"
    finances_text += f"• Всего записей: {total_appointments}\n"
    finances_text += f"• Оплачено: {paid_appointments}\n"
    finances_text += f"• Ожидают оплаты: {total_appointments - paid_appointments}"

    await message.answer(finances_text, reply_markup=admin_main_kb(), parse_mode="Markdown")


# ========== УПРАВЛЕНИЕ ==========
@dp.message(F.text == "🔄 Управление")
async def admin_management(message: types.Message):
    """Меню управления"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    logger.info(f"Admin {message.from_user.id} accessed management")
    await message.answer(
        "🔄 *Управление системой*\n\n"
        "✨ *Доступные функции:*\n"
        "• 🗑 Очистить старые записи (старше 30 дней)\n"
        "• 📤 Экспортировать все данные\n"
        "• 🔄 Сбросить бота (удалить все данные)\n"
        "• 📊 Посмотреть статистику фото\n\n"
        "👇 Выберите действие:",
        reply_markup=admin_management_kb(),
        parse_mode="Markdown"
    )


# ========== ОЧИСТКА СТАРЫХ ЗАПИСЕЙ ==========
@dp.message(F.text == "🗑 Очистить старые записи")
async def cleanup_old_appointments(message: types.Message):
    """Очистка старых записей"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    logger.info(f"Admin {message.from_user.id} cleaning old appointments")

    today = datetime.now().date()
    deleted_count = 0
    deleted_dates = []

    # Создаем копию ключей, чтобы не менять словарь во время итерации
    date_keys = list(appointments_db.keys())

    for date_key in date_keys:
        try:
            appointment_date = datetime.strptime(date_key, "%Y-%m-%d").date()
            # Удаляем записи старше 30 дней
            if (today - appointment_date).days > 30:
                deleted_count += len(appointments_db[date_key])
                deleted_dates.append(date_key)
                del appointments_db[date_key]
        except Exception as e:
            logger.error(f"Ошибка парсинга даты {date_key}: {e}")
            continue

    if deleted_count > 0:
        save_all_data()

        dates_str = ', '.join(deleted_dates[:3])
        if len(deleted_dates) > 3:
            dates_str += f" и ещё {len(deleted_dates) - 3} дат"

        await message.answer(
            f"✅ *Очистка завершена!*\n\n"
            f"🗑 *Удалено:*\n"
            f"• Записей: {deleted_count}\n"
            f"• Дат: {dates_str}\n\n"
            f"💾 Все данные сохранены.",
            reply_markup=admin_management_kb(),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "✅ Нет старых записей для удаления.\n"
            "Все записи актуальны (младше 30 дней).",
            reply_markup=admin_management_kb()
        )


# ========== ЭКСПОРТ ДАННЫХ ==========
@dp.message(F.text == "📤 Экспорт данных")
async def export_data(message: types.Message):
    """Экспорт данных"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    logger.info(f"Admin {message.from_user.id} exporting data")

    try:
        # Создаем отчет
        report = "📊 ОТЧЕТ ПО САЛОНУ КРАСОТЫ\n"
        report += "=" * 50 + "\n"
        report += f"Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "=" * 50 + "\n\n"

        # 1. Общая статистика
        total_appointments = sum(len(times) for times in appointments_db.values())
        paid_appointments = sum(
            1 for times in appointments_db.values()
            for appt in times.values()
            if appt.get('paid')
        )
        total_revenue = sum(
            appt.get('price', 0)
            for times in appointments_db.values()
            for appt in times.values()
            if appt.get('paid')
        )

        report += "📈 ОБЩАЯ СТАТИСТИКА:\n"
        report += "-" * 30 + "\n"
        report += f"• Всего записей: {total_appointments}\n"
        report += f"• Оплачено: {paid_appointments}\n"
        report += f"• Выручка: {total_revenue}₽\n"
        report += f"• Клиентов: {len(users_db)}\n"
        report += f"• Фото в галерее: {len(gallery_photos)}\n"
        report += f"• Отмененных: {len(cancelled_appointments)}\n\n"

        # 2. Активные записи
        report += "📅 АКТИВНЫЕ ЗАПИСИ:\n"
        report += "-" * 30 + "\n"
        if appointments_db:
            for date_key, times in appointments_db.items():
                try:
                    date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                    for time_key, appointment in times.items():
                        status = "✅ Оплачено" if appointment.get('paid') else "⏳ Ожидает оплаты"
                        report += f"• {date_display} {time_key} - {appointment.get('service', 'Неизвестно')} - {appointment.get('name', 'Неизвестно')} - {status}\n"
                except:
                    continue
        else:
            report += "Нет активных записей\n"

        # Сохраняем в файл
        filename = f"salon_export_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)

        # Отправляем файл
        await message.answer_document(
            FSInputFile(filename),
            caption=f"📄 Экспорт данных ({datetime.now().strftime('%d.%m.%Y')})",
            reply_markup=admin_management_kb()
        )

        # Удаляем временный файл после отправки
        await asyncio.sleep(2)
        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        logger.error(f"Ошибка экспорта данных: {e}")
        await message.answer(
            f"❌ Ошибка при экспорте данных:\n{str(e)[:100]}",
            reply_markup=admin_management_kb()
        )


# ========== СБРОС БОТА ==========
@dp.message(F.text == "🔄 Сбросить бота")
async def reset_bot(message: types.Message):
    """Сброс бота"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    logger.warning(f"Admin {message.from_user.id} attempting to reset bot")

    # Считаем данные перед сбросом
    total_appointments = sum(len(times) for times in appointments_db.values())
    total_clients = len(users_db)
    total_photos = len(gallery_photos)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить всё", callback_data="reset_confirm")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="reset_cancel")]
    ])

    await message.answer(
        f"⚠️ *ВНИМАНИЕ! ОПАСНАЯ ОПЕРАЦИЯ!*\n\n"
        f"📊 *Сейчас в системе:*\n"
        f"• Записей: {total_appointments}\n"
        f"• Клиентов: {total_clients}\n"
        f"• Фото: {total_photos}\n"
        f"• Отмененных: {len(cancelled_appointments)}\n\n"
        f"🔥 *После сброса будет удалено:*\n"
        f"• Все записи и клиенты\n"
        f"• Вся галерея фото\n"
        f"• История платежей\n\n"
        f"❓ *Вы уверены что хотите продолжить?*\n"
        f"Это действие НЕОБРАТИМО!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "reset_confirm")
async def reset_confirm_handler(callback: types.CallbackQuery):
    """Подтверждение сброса"""
    if str(callback.from_user.id) != str(ADMIN_ID):
        await callback.answer("Доступ запрещен")
        return

    await callback.answer("Начинаю сброс...")

    try:
        # Сохраняем резервную копию
        backup_data = {
            "appointments": appointments_db,
            "users": users_db,
            "pending_payments": pending_payments,
            "cancelled": cancelled_appointments,
            "gallery": gallery_photos,
            "backup_date": datetime.now().isoformat(),
            "total_appointments": sum(len(times) for times in appointments_db.values()),
            "total_clients": len(users_db),
            "total_photos": len(gallery_photos)
        }

        backup_filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_filename, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)

        # Сбрасываем данные
        appointments_db.clear()
        users_db.clear()
        pending_payments.clear()
        cancelled_appointments.clear()
        gallery_photos.clear()

        # Сохраняем пустые данные
        save_all_data()

        await callback.message.edit_text(
            f"✅ *Бот успешно сброшен!*\n\n"
            f"🗑️ *Удалено:*\n"
            f"• Записей: {backup_data['total_appointments']}\n"
            f"• Клиентов: {backup_data['total_clients']}\n"
            f"• Фото: {backup_data['total_photos']}\n\n"
            f"💾 *Создана резервная копия:*\n"
            f"`{backup_filename}`\n\n"
            f"🔄 Бот готов к новой работе!",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Ошибка при сбросе бота: {e}")
        await callback.message.edit_text(
            f"❌ *Ошибка при сбросе!*\n\n"
            f"Произошла ошибка:\n`{str(e)[:100]}`\n\n"
            f"Данные не были удалены.",
            parse_mode="Markdown"
        )


@dp.callback_query(F.data == "reset_cancel")
async def reset_cancel_handler(callback: types.CallbackQuery):
    """Отмена сброса"""
    await callback.answer("Сброс отменен")
    await callback.message.edit_text(
        "❌ Сброс отменен. Данные сохранены.",
        reply_markup=admin_management_kb()
    )


# ========== СТАТИСТИКА ФОТО ==========
@dp.message(F.text == "📊 Статистика фото")
async def gallery_stats(message: types.Message):
    """Статистика галереи"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    if not gallery_photos:
        await message.answer("📭 Нет фото в галерее", reply_markup=admin_management_kb())
        return

    # Собираем даты добавления
    dates = []
    for photo in gallery_photos:
        if isinstance(photo, dict) and 'added_at' in photo:
            date_str = photo['added_at'][:10] if len(photo['added_at']) >= 10 else photo['added_at']
            dates.append(date_str)

    date_counts = Counter(dates) if dates else {}

    stats_text = "📊 *Статистика галереи*\n\n"
    stats_text += f"🖼 *Общее:*\n"
    stats_text += f"• Всего фото: {len(gallery_photos)}\n"
    stats_text += f"• С подписями: {sum(1 for p in gallery_photos if isinstance(p, dict) and p.get('caption'))}\n"
    stats_text += f"• Без подписей: {sum(1 for p in gallery_photos if not (isinstance(p, dict) and p.get('caption')))}\n\n"

    if dates:
        stats_text += f"📅 *Хронология:*\n"
        stats_text += f"• Первое фото: {min(dates) if dates else 'неизвестно'}\n"
        stats_text += f"• Последнее фото: {max(dates) if dates else 'неизвестно'}\n\n"

    if date_counts:
        stats_text += "📈 *По дням (топ-5):*\n"
        for date, count in sorted(date_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            stats_text += f"• {date}: {count} фото\n"

    await message.answer(stats_text, reply_markup=admin_management_kb(), parse_mode="Markdown")


# ========== ГАЛЕРЕЯ ==========
@dp.message(F.text == "🖼️ Галерея")
async def admin_gallery(message: types.Message):
    """Управление галереей"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    last_photo_date = "никогда"
    if gallery_photos:
        try:
            last_photo = gallery_photos[-1]
            if isinstance(last_photo, dict) and 'added_at' in last_photo:
                last_photo_date = last_photo['added_at'][:10]
        except:
            pass

    await message.answer(
        "🖼️ *Управление галереей*\n\n"
        f"📊 *Статистика:*\n"
        f"• Всего фото: {len(gallery_photos)}\n"
        f"• Последнее фото: {last_photo_date}\n\n"
        "👇 Выберите действие:",
        reply_markup=gallery_admin_kb(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "📤 Добавить фото")
async def add_photo_start(message: types.Message, state: FSMContext):
    """Начать добавление фото"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    await state.set_state(GalleryState.waiting_photo)
    await message.answer(
        "📤 *Добавление фото*\n\n"
        "✨ *Отправьте фото:*\n"
        "(максимум 10 МБ)\n\n"
        "📝 После фото можно добавить подпись\n"
        "❌ Для отмены напишите 'отмена'",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(GalleryState.waiting_photo, F.photo)
async def save_admin_photo(message: types.Message, state: FSMContext):
    """Сохранение фото админа"""
    photo_file_id = message.photo[-1].file_id

    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(GalleryState.waiting_caption)

    await message.answer(
        "✅ Фото получено!\n\n"
        "📝 *Добавьте подпись:*\n"
        "(или отправьте 'без подписи')",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="без подписи")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(GalleryState.waiting_caption)
async def save_photo_caption(message: types.Message, state: FSMContext):
    """Сохранение подписи к фото"""
    if message.text.lower() == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Операция отменена", reply_markup=gallery_admin_kb())
        return

    data = await state.get_data()
    photo_file_id = data.get('photo_file_id')

    if not photo_file_id:
        await message.answer("❌ Ошибка: фото не найдено", reply_markup=gallery_admin_kb())
        await state.clear()
        return

    caption = message.text if message.text.lower() != "без подписи" else ""

    photo_data = {
        'file_id': photo_file_id,
        'caption': caption,
        'added_by': message.from_user.id,
        'added_at': datetime.now().isoformat(),
        'message_id': message.message_id
    }

    gallery_photos.append(photo_data)
    save_all_data()

    await message.answer(
        f"✅ *Фото добавлено в галерею!*\n\n"
        f"📝 Подпись: {caption if caption else '(без подписи)'}\n"
        f"🖼 Всего фото в галерее: {len(gallery_photos)}",
        reply_markup=gallery_admin_kb(),
        parse_mode="Markdown"
    )

    # Показываем добавленное фото
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_file_id,
        caption=f"✅ Добавлено в галерею\n{caption}" if caption else "✅ Добавлено в галерею"
    )

    await state.clear()


# ========== УДАЛЕНИЕ ФОТО ==========
@dp.message(F.text == "🗑 Удалить фото")
async def delete_photo_start(message: types.Message, state: FSMContext):
    """Начать удаление фото"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    if not gallery_photos:
        await message.answer("📭 В галерее нет фото для удаления.", reply_markup=gallery_admin_kb())
        return

    # Определяем сколько фото показать (максимум 5)
    photos_to_show = min(5, len(gallery_photos))

    # Сохраняем количество фото в состояние
    await state.update_data(photos_to_show=photos_to_show)

    await message.answer(
        f"🗑 *Удаление фото*\n\n"
        f"📊 Всего фото: {len(gallery_photos)}\n"
        f"👇 Показаны последние {photos_to_show} фото:\n\n"
        f"*ВАЖНО:* Нумерация от 1 до {photos_to_show}\n"
        f"• Кнопка 1 → последнее добавленное фото\n"
        f"• Кнопка {photos_to_show} → самое старое из показанных\n\n"
        f"Выберите номер фото:",
        parse_mode="Markdown"
    )

    # Отправляем фото с ПРАВИЛЬНЫМИ номерами
    for i in range(photos_to_show):
        try:
            # Индекс в массиве: последние фото имеют меньшие индексы в конце массива
            # i=0 → последнее фото (индекс -1)
            # i=1 → предпоследнее фото (индекс -2)
            photo_index = -(i + 1)  # Отрицательные индексы для доступа с конца
            photo_data = gallery_photos[photo_index]

            # Номер для кнопки (от 1 до photos_to_show)
            button_number = i + 1

            caption = f"📸 *Фото {button_number}*\n"
            if photo_data.get('caption'):
                caption += f"📝 {photo_data.get('caption')}\n"

            if 'added_at' in photo_data:
                try:
                    date_str = photo_data['added_at'][:10]
                    caption += f"📅 {date_str}\n"
                except:
                    pass

            # Храним реальный индекс в подписи (для отладки)
            real_index = len(gallery_photos) + photo_index  # Преобразуем в положительный индекс
            caption += f"🔢 *Кнопка: {button_number}* (индекс: {real_index})"

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_data['file_id'],
                caption=caption,
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(f"Ошибка отправки фото {i + 1}: {e}")

    # Создаем клавиатуру
    keyboard_rows = []
    row = []

    # Кнопки от 1 до photos_to_show
    for i in range(1, photos_to_show + 1):
        row.append(KeyboardButton(text=str(i)))
        if len(row) == 3 or i == photos_to_show:
            keyboard_rows.append(row)
            row = []

    keyboard_rows.append([KeyboardButton(text="❌ Отмена")])

    delete_kb = ReplyKeyboardMarkup(
        keyboard=keyboard_rows,
        resize_keyboard=True
    )

    await message.answer(
        f"➡️ *Введите номер фото (1-{photos_to_show}):*\n"
        "Кнопка 1 → последнее фото\n"
        f"Кнопка {photos_to_show} → {photos_to_show}-е с конца",
        reply_markup=delete_kb,
        parse_mode="Markdown"
    )

    await state.set_state(GalleryState.waiting_delete_number)


@dp.message(GalleryState.waiting_delete_number)
async def handle_delete_number(message: types.Message, state: FSMContext):
    """Обработка номера фото для удаления"""

    # Проверка на отмену
    if message.text.lower() in ["❌ отмена", "отмена", "cancel"]:
        await state.clear()
        await message.answer("❌ Удаление отменено.", reply_markup=gallery_admin_kb())
        return

    # Получаем данные из состояния
    state_data = await state.get_data()
    photos_to_show = state_data.get('photos_to_show', min(5, len(gallery_photos)))

    try:
        # Проверяем, что введен номер
        if not message.text.isdigit():
            await message.answer(f"❌ Пожалуйста, введите число от 1 до {photos_to_show}.")
            return

        button_number = int(message.text)

        # Проверяем диапазон
        if button_number < 1 or button_number > photos_to_show:
            await message.answer(f"❌ Пожалуйста, введите число от 1 до {photos_to_show}.")
            return

        # 🎯 ВАЖНО: Преобразуем номер кнопки в индекс массива
        # button_number=1 → последнее фото → индекс -1
        # button_number=2 → предпоследнее фото → индекс -2
        # и т.д.
        array_index = -button_number  # Отрицательный индекс для доступа с конца

        # Получаем фото
        photo_to_delete = gallery_photos[array_index]

        # Создаем сообщение с подтверждением
        confirm_text = f"🗑 *Подтверждение удаления*\n\n"
        confirm_text += f"📸 *Фото #{button_number}*\n"

        if photo_to_delete.get('caption'):
            confirm_text += f"📝 *Подпись:* {photo_to_delete['caption']}\n"

        if 'added_at' in photo_to_delete:
            try:
                date_str = photo_to_delete['added_at'][:10]
                confirm_text += f"📅 *Добавлено:* {date_str}\n"
            except:
                pass

        # Показываем, какое именно фото будет удалено
        position = "последнее" if button_number == 1 else f"{button_number}-е с конца"
        confirm_text += f"\nℹ️ Это {position} добавленное фото\n\n"
        confirm_text += f"❓ *Вы уверены, что хотите удалить это фото?*"

        # Клавиатура подтверждения
        confirm_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Да, удалить"), KeyboardButton(text="❌ Нет, отмена")]
            ],
            resize_keyboard=True
        )

        # Сохраняем индекс для удаления
        await state.update_data(
            delete_array_index=array_index,
            delete_button_number=button_number
        )

        await message.answer(
            confirm_text,
            reply_markup=confirm_kb,
            parse_mode="Markdown"
        )

        await state.set_state(GalleryState.confirm_delete)

    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {str(e)[:50]}")
    except Exception as e:
        logger.error(f"Ошибка при обработке номера: {e}")
        await message.answer(f"❌ Ошибка: {str(e)[:100]}")


@dp.message(GalleryState.confirm_delete)
async def confirm_photo_deletion(message: types.Message, state: FSMContext):
    """Подтверждение удаления фото"""

    if message.text == "❌ Нет, отмена":
        await state.clear()
        await message.answer("❌ Удаление отменено.", reply_markup=gallery_admin_kb())
        return

    elif message.text == "✅ Да, удалить":
        # Получаем данные из состояния
        state_data = await state.get_data()
        array_index = state_data.get('delete_array_index')
        button_number = state_data.get('delete_button_number')

        if array_index is None:
            await message.answer("❌ Ошибка: данные о фото не найдены.", reply_markup=gallery_admin_kb())
            await state.clear()
            return

        try:
            # Получаем фото перед удалением
            photo_to_delete = gallery_photos[array_index]
            caption = photo_to_delete.get('caption', 'без подписи')

            # Удаляем фото по индексу
            deleted_photo = gallery_photos.pop(array_index)

            # Сохраняем изменения
            save_all_data()

            # Показываем результат
            position = "последнее" if button_number == 1 else f"{button_number}-е с конца"

            await message.answer(
                f"✅ *Фото удалено!*\n\n"
                f"📸 Удалено: {position} фото\n"
                f"📝 Подпись: {caption}\n"
                f"🖼 Осталось фото: {len(gallery_photos)}",
                reply_markup=gallery_admin_kb(),
                parse_mode="Markdown"
            )

        except IndexError:
            await message.answer("❌ Ошибка: фото уже удалено.", reply_markup=gallery_admin_kb())
        except Exception as e:
            logger.error(f"Ошибка удаления: {e}")
            await message.answer(f"❌ Ошибка: {str(e)[:100]}", reply_markup=gallery_admin_kb())

        await state.clear()

    else:
        await message.answer(
            "❌ Пожалуйста, выберите действие:\n"
            "• ✅ Да, удалить - для удаления\n"
            "• ❌ Нет, отмена - для отмены"
        )


@dp.message(GalleryState.waiting_photo, F.text)
async def handle_text_in_waiting_photo(message: types.Message, state: FSMContext):
    """Обработка текста в состоянии ожидания фото"""
    if message.text.lower() in ["❌ отмена", "отмена", "cancel"]:
        await state.clear()
        await message.answer("❌ Добавление фото отменено", reply_markup=gallery_admin_kb())
    else:
        await message.answer("📤 Пожалуйста, отправьте фото (не текст)")


# ========== НАСТРОЙКИ ==========
@dp.message(F.text == "⚙️ Настройки")
async def admin_settings(message: types.Message):
    """Настройки системы"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    # Получаем количество данных
    total_appointments = sum(len(times) for times in appointments_db.values())
    total_clients = len(users_db)
    total_photos = len(gallery_photos)

    await message.answer(
        "⚙️ *Настройки системы*\n\n"
        f"🤖 *Бот:* @Probnik312Bot\n"
        f"👑 *Админ ID:* `{ADMIN_ID}`\n"
        f"💰 *ЮMoney кошелек:* `{YOOMONEY_WALLET}`\n\n"
        f"📊 *Данные:*\n"
        f"• Записей: {total_appointments}\n"
        f"• Клиентов: {total_clients}\n"
        f"• Фото: {total_photos}\n\n"
        f"📈 *Версия:* 2.1\n"
        f"🔄 *Обновлено:* {datetime.now().strftime('%d.%m.%Y')}",
        reply_markup=admin_main_kb(),
        parse_mode="Markdown"
    )


# ========== КНОПКИ НАЗАД ==========
@dp.message(F.text == "⬅️ В админку")
async def back_to_admin(message: types.Message, state: FSMContext):
    """Вернуться в админку"""
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    await state.clear()
    await admin_panel(message)


@dp.message(F.text == "⬅️ В меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    """Вернуться в главное меню"""
    await state.clear()
    await message.answer("🏠 Главное меню:", reply_markup=main_kb())


# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция запуска бота"""

    # Запускаем задачу автосохранения
    asyncio.create_task(auto_save_task())

    logger.info("=" * 60)
    logger.info("✨ БОТ ДЛЯ САЛОНА КРАСОТЫ ЗАПУЩЕН ✨")
    logger.info(f"🤖 Токен: {'✅ установлен' if BOT_TOKEN else '❌ НЕТ!'}")
    logger.info(f"👑 Админ ID: {ADMIN_ID}")
    logger.info(f"💰 ЮMoney: {YOOMONEY_WALLET}")
    logger.info(f"📊 Загружено записей: {sum(len(times) for times in appointments_db.values())}")
    logger.info(f"👥 Загружено клиентов: {len(users_db)}")
    logger.info(f"🖼 Загружено фото: {len(gallery_photos)}")
    logger.info("=" * 60)

    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        logger.info("🛑 Бот завершил работу")
        save_all_data()  # Сохраняем данные перед выходом


if __name__ == "__main__":
    asyncio.run(main())
