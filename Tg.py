import asyncio
import logging
import uuid
import urllib.parse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
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

# ========== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8413297236:AAFVy3V4B08d2ND-nvm9NLGKvxWwWd2ii4g")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7261798231"))
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "4100119468708609")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Папка для фото галереи
PHOTOS_DIR = "gallery_photos"
Path(PHOTOS_DIR).mkdir(exist_ok=True)

# Файл базы данных фото
PHOTOS_DB_FILE = "gallery_photos.json"
if os.path.exists(PHOTOS_DB_FILE):
    try:
        with open(PHOTOS_DB_FILE, 'r', encoding='utf-8') as f:
            gallery_photos = json.load(f)
    except (json.JSONDecodeError, IOError):
        gallery_photos = []
else:
    gallery_photos = []

# Хранилище данных
users_db = {}
appointments_db = {}
pending_payments = {}
cancelled_appointments = []

# Услуги
services_db = {
    'manicure': {
        'name': 'Маникюр',
        'price': 1500,
        'duration': 60,
        'description': 'Комплексный маникюр с покрытием'
    },
    'pedicure': {
        'name': 'Педикюр',
        'price': 2000,
        'duration': 90,
        'description': 'Педикюр + уход за стопами'
    },
    'cover': {
        'name': 'Покрытие',
        'price': 800,
        'duration': 30,
        'description': 'Обновление покрытия гель-лаком'
    }
}

# Политика возвратов
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


# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def save_gallery_to_file():
    """Сохранить галерею в файл"""
    try:
        with open(PHOTOS_DB_FILE, 'w', encoding='utf-8') as file:
            json.dump(gallery_photos, file, ensure_ascii=False, indent=2)
        logger.info(f"Галерея сохранена ({len(gallery_photos)} фото)")
    except Exception as e:
        logger.error(f"Ошибка сохранения галереи: {e}")


def get_free_slots(date, service_key):
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
    params = {
        'receiver': YOOMONEY_WALLET,
        'sum': amount,
        'formComment': comment or 'Оплата услуги в салоне',
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
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Записаться")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="❌ Отменить запись")],
            [KeyboardButton(text="💰 Мои платежи")],
            [KeyboardButton(text="🖼 Наши работы")],
            [KeyboardButton(text="👑 Админ")] if ADMIN_ID else []
        ],
        resize_keyboard=True
    )

def services_kb():
    buttons = []
    for key, service in services_db.items():
        buttons.append([KeyboardButton(text=f"💅 {service['name']} - {service['price']}₽")])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def dates_kb():
    buttons = []
    today = datetime.now().date()
    for i in range(7):
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m.%Y")
        buttons.append([KeyboardButton(text=date_str)])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def confirm_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, отменить")],
            [KeyboardButton(text="❌ Нет, оставить")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )


def admin_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📋 Все записи")],
            [KeyboardButton(text="💰 Финансы"), KeyboardButton(text="🔄 Управление")],
            [KeyboardButton(text="🖼️ Галерея"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="⬅️ В меню")]
        ],
        resize_keyboard=True
    )


def gallery_admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Добавить фото")],
            [KeyboardButton(text="🗑 Удалить фото")],
            [KeyboardButton(text="📊 Статистика фото")],
            [KeyboardButton(text="⬅️ В админку")]
        ],
        resize_keyboard=True
    )


# ========== ОСНОВНЫЕ КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    args = message.text.split()

    if len(args) > 1 and args[1].startswith("payment_success_"):
        payment_id = args[1].replace("payment_success_", "")
        await handle_payment_success(message, payment_id)
    else:
        await message.answer(
            "💅 *Добро пожаловать в NailStudio!*\n\n"
            "✨ *Премиум уход за ногтями*\n"
            "• Профессиональные мастера\n"
            "• Качественные материалы\n"
            "• Уютная атмосфера\n\n"
            "Выберите действие:",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )


async def handle_payment_success(message: types.Message, payment_id: str):
    if payment_id in pending_payments:
        payment_data = pending_payments[payment_id]

        date_key = payment_data['date_obj'].strftime("%Y-%m-%d")
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

        admin_text = f"💰 *Новая оплаченная запись!*\n\n👤 {payment_data['name']}\n📞 {payment_data['phone']}\n💅 {payment_data['service_name']}\n💰 {payment_data['price']}₽\n📅 {payment_data['date_display']} {payment_data['time']}"
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")

        await message.answer(
            f"✅ *Запись оплачена!*\n\n"
            f"💅 {payment_data['service_name']}\n"
            f"💰 {payment_data['price']}₽\n"
            f"📅 {payment_data['date_display']}\n"
            f"⏰ {payment_data['time']}\n\n"
            f"📍 ул. Примерная, д. 1\n"
            f"📞 +7 (999) 123-45-67\n\n"
            f"*Ждём вас!* 💖",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )

        del pending_payments[payment_id]
    else:
        await message.answer(
            "Платеж обработан. Проверьте «📋 Мои записи».",
            reply_markup=main_kb()
        )


# ========== ЗАПИСЬ ==========
@dp.message(F.text == "📅 Записаться")
async def start_appointment(message: types.Message):
    await message.answer(
        "💅 *Выберите услугу:*",
        reply_markup=services_kb(),
        parse_mode="Markdown"
    )


@dp.message(lambda message: any(
    f"💅 {service['name']} - {service['price']}₽" == message.text
    for service in services_db.values()
))
async def handle_service_button(message: types.Message, state: FSMContext):
    for key, service in services_db.items():
        button_text = f"💅 {service['name']} - {service['price']}₽"

        if message.text == button_text:
            await state.update_data(
                service_key=key,
                service_name=service['name'],
                price=service['price']
            )

            await state.set_state(AppointmentState.choose_date)

            await message.answer(
                f"✅ *{service['name']}*\n"
                f"💰 *{service['price']}₽*\n"
                f"⏱ *{service['duration']} мин*\n\n"
                f"*Выберите дату:*",
                reply_markup=dates_kb(),
                parse_mode="Markdown"
            )
            return

    await message.answer("Выберите услугу:", reply_markup=services_kb())


@dp.message(F.text == "⬅️ Назад")
async def back_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if not current_state:
        await message.answer("Главное меню:", reply_markup=main_kb())
        return

    if current_state == AppointmentState.choose_date.state:
        await state.set_state(AppointmentState.choose_service)
        await message.answer("Выберите услугу:", reply_markup=services_kb())

    elif current_state == AppointmentState.choose_time.state:
        await state.set_state(AppointmentState.choose_date)
        await message.answer("Выберите дату:", reply_markup=dates_kb())

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

        await message.answer("Выберите время:",
                             reply_markup=ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True))

    elif current_state == AppointmentState.enter_phone.state:
        await state.set_state(AppointmentState.enter_name)
        await message.answer("Введите ваше имя:")

    elif current_state in [CancelState.choose_appointment.state, CancelState.confirm_cancel.state]:
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_kb())

    else:
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_kb())


@dp.message(AppointmentState.choose_date)
async def choose_date_handler(message: types.Message, state: FSMContext):
    try:
        selected_date = datetime.strptime(message.text, "%d.%m.%Y").date()
        if selected_date < datetime.now().date():
            await message.answer("Нельзя выбрать прошедшую дату!")
            return

        data = await state.get_data()
        free_slots = get_free_slots(selected_date, data['service_key'])

        if not free_slots:
            await message.answer("На эту дату нет свободных слотов 😔", reply_markup=dates_kb())
            return

        await state.update_data(
            date_display=message.text,
            date_obj=selected_date
        )

        await state.set_state(AppointmentState.choose_time)

        time_buttons = []
        for i in range(0, len(free_slots), 3):
            row = []
            for slot in free_slots[i:i + 3]:
                row.append(KeyboardButton(text=slot))
            time_buttons.append(row)
        time_buttons.append([KeyboardButton(text="⬅️ Назад")])

        await message.answer(
            f"📅 *{message.text}*\n"
            f"💅 *{data['service_name']}*\n\n"
            f"*Выберите время:*",
            reply_markup=ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True),
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Неверный формат. Используйте ДД.ММ.ГГГГ")


@dp.message(AppointmentState.choose_time)
async def choose_time_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    free_slots = get_free_slots(data['date_obj'], data['service_key'])

    if message.text not in free_slots:
        time_buttons = []
        for i in range(0, len(free_slots), 3):
            row = []
            for slot in free_slots[i:i + 3]:
                row.append(KeyboardButton(text=slot))
            time_buttons.append(row)
        time_buttons.append([KeyboardButton(text="⬅️ Назад")])

        await message.answer(
            "Время занято. Выберите из списка:",
            reply_markup=ReplyKeyboardMarkup(keyboard=time_buttons, resize_keyboard=True)
        )
        return

    await state.update_data(time=message.text)
    await state.set_state(AppointmentState.enter_name)
    await message.answer(
        f"📋 *Детали записи:*\n\n"
        f"💅 {data['service_name']}\n"
        f"💰 {data['price']}₽\n"
        f"📅 {data['date_display']}\n"
        f"⏰ {message.text}\n\n"
        f"*Введите ваше имя:*",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )


@dp.message(AppointmentState.enter_name)
async def enter_name_handler(message: types.Message, state: FSMContext):
    if len(message.text) < 2:
        await message.answer("Имя должно содержать хотя бы 2 символа:")
        return

    await state.update_data(name=message.text)
    await state.set_state(AppointmentState.enter_phone)
    await message.answer(
        f"👤 *{message.text}*\n\n"
        f"*Введите телефон:*\n"
        f"Пример: 79161234567",
        parse_mode="Markdown"
    )


@dp.message(AppointmentState.enter_phone)
async def enter_phone_handler(message: types.Message, state: FSMContext):
    phone = ''.join(filter(str.isdigit, message.text))
    if len(phone) != 11 or not phone.startswith(('7', '8')):
        await message.answer("Неверный формат. Введите 11 цифр:")
        return

    data = await state.get_data()

    payment_id = f"pay_{uuid.uuid4().hex[:10]}"

    pending_payments[payment_id] = {
        **data,
        'user_id': message.from_user.id,
        'phone': phone
    }

    payment_link = create_yoomoney_payment_link(
        amount=data['price'],
        label=payment_id,
        comment=f"Оплата {data['service_name']} на {data['date_display']} {data['time']}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить сейчас", url=payment_link)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"confirm_pay_{payment_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")]
    ])

    await state.set_state(AppointmentState.payment)
    await message.answer(
        f"💳 *Оплата записи*\n\n"
        f"📋 *Детали:*\n"
        f"• Услуга: {data['service_name']}\n"
        f"• Сумма: {data['price']}₽\n"
        f"• Дата: {data['date_display']}\n"
        f"• Время: {data['time']}\n"
        f"• Имя: {data['name']}\n"
        f"• Телефон: {phone}\n\n"
        f"*Для подтверждения оплатите полную стоимость.*\n\n"
        f"📌 *Инструкция:*\n"
        f"1. Нажмите «💳 Оплатить сейчас»\n"
        f"2. Оплатите через ЮMoney\n"
        f"3. Вернитесь и нажмите «✅ Я оплатил»",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("confirm_pay_"))
async def confirm_payment_handler(callback: types.CallbackQuery, state: FSMContext):
    payment_id = callback.data.replace("confirm_pay_", "")
    await callback.answer("Проверяем оплату...")

    await callback.message.edit_text(
        f"⏳ *Проверяем оплату...*\n\n"
        f"Если вы оплатили:\n"
        f"1. Закройте это окно\n"
        f"2. Нажмите /start",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔄 Проверить",
                url=f"https://t.me/Probnik312Bot?start=payment_success_{payment_id}"
            )]
        ]),
        parse_mode="Markdown"
    )

    await state.clear()


@dp.callback_query(F.data == "cancel_booking")
async def cancel_booking_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "Запись отменена.",
        reply_markup=main_kb()
    )
    await state.clear()


# ========== НАШИ РАБОТЫ ==========
@dp.message(F.text == "🖼 Наши работы")
async def show_gallery(message: types.Message):
    if not gallery_photos:
        await message.answer(
            "🖼 *Наша галерея*\n\n"
            "Пока здесь нет фото 😔\n"
            "Скоро мы добавим наши лучшие работы!",
            reply_markup=main_kb(),
            parse_mode="Markdown"
        )
        return

    await message.answer(
        f"🖼 *Наши работы*\n\n"
        f"Всего фото: {len(gallery_photos)}\n\n"
        f"Смотрите наши лучшие работы 👇",
        reply_markup=main_kb(),
        parse_mode="Markdown"
    )

    for i, photo_data in enumerate(gallery_photos[:10]):
        try:
            if photo_data.get('file_id'):
                await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=photo_data['file_id'],
                    caption=photo_data.get('caption', '✨ Наша работа')
                )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка отправки фото: {e}")
            continue

    if len(gallery_photos) > 10:
        await message.answer(
            f"И ещё {len(gallery_photos) - 10} фото в галерее!",
            reply_markup=main_kb()
        )


# ========== МОИ ЗАПИСИ ==========
@dp.message(F.text == "📋 Мои записи")
async def my_appointments_list(message: types.Message):
    user_appointments = []

    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if appointment['user_id'] == message.from_user.id:
                date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                status = "✅ Оплачено" if appointment.get('paid') else "⏳ Ожидает оплаты"
                user_appointments.append(f"{date_display} {time_key} - {appointment['service']} - {status}")

    if user_appointments:
        text = "📋 *Ваши записи:*\n\n" + "\n".join(user_appointments)
    else:
        text = "У вас нет активных записей."

    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")


# ========== ОТМЕНА ЗАПИСИ ==========
@dp.message(F.text == "❌ Отменить запись")
async def cancel_appointment_start(message: types.Message, state: FSMContext):
    user_appointments = []

    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if appointment['user_id'] == message.from_user.id:
                appointment_datetime = datetime.strptime(
                    f"{date_key} {time_key}", "%Y-%m-%d %H:%M"
                )

                refund_info = calculate_refund_amount(appointment_datetime, appointment.get('price', 0))

                user_appointments.append({
                    'date_key': date_key,
                    'time_key': time_key,
                    'date_display': datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y"),
                    'time': time_key,
                    'service': appointment['service'],
                    'price': appointment.get('price', 0),
                    'paid': appointment.get('paid', False),
                    'datetime': appointment_datetime,
                    'refund_info': refund_info
                })

    if not user_appointments:
        await message.answer("У вас нет активных записей.", reply_markup=main_kb())
        return

    keyboard_buttons = []
    for i, appt in enumerate(user_appointments, 1):
        # Определяем статус для кнопки
        if appt['paid']:
            status_text = f"💳 {appt['refund_info']['percent']}% возврат"
        else:
            status_text = "⏳ Не оплачено"

        # Создаем текст кнопки
        button_text = f"{i}. {appt['date_display']} {appt['time']} ({status_text})"

        # Добавляем кнопку
        keyboard_buttons.append([KeyboardButton(text=button_text)])

    await state.update_data(appointments_list=user_appointments)
    await state.set_state(CancelState.choose_appointment)

    await message.answer(
        "📋 *Ваши записи:*\n\n"
        "💳 - оплаченная запись\n"
        "⏳ - неоплаченная запись\n\n"
        "Выберите запись:",
        reply_markup=ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True),
        parse_mode="Markdown"
    )


@dp.message(CancelState.choose_appointment)
async def select_appointment_for_cancel(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_kb())
        return

    try:
        appointment_num = int(message.text.split('.')[0]) - 1

        data = await state.get_data()
        appointments_list = data.get('appointments_list', [])

        if appointment_num < 0 or appointment_num >= len(appointments_list):
            await message.answer("Неверный номер.", reply_markup=main_kb())
            await state.clear()
            return

        selected_appt = appointments_list[appointment_num]
        refund_info = selected_appt['refund_info']

        message_text = f"📋 *Детали отмены:*\n\n"
        message_text += f"📅 {selected_appt['date_display']}\n"
        message_text += f"⏰ {selected_appt['time']}\n"
        message_text += f"💅 {selected_appt['service']}\n"
        message_text += f"💰 {selected_appt['price']}₽\n\n"

        if selected_appt['paid']:
            message_text += f"💳 Статус: *Оплачено*\n"
            message_text += f"⏱ До записи: {refund_info['hours_left']}ч\n"
            message_text += f"📊 Возврат: *{refund_info['percent']}%*\n"
            message_text += f"💸 К возврату: *{refund_info['refund_amount']}₽*\n"
            message_text += f"⚠️ Штраф: {refund_info['penalty']}₽\n\n"

            if refund_info['refund_amount'] > 0:
                message_text += f"✅ Деньги вернутся за 1-3 дня\n"
            else:
                message_text += f"❌ Возврат не предусмотрен\n"
        else:
            message_text += f"💳 Статус: *Не оплачено*\n"
            message_text += f"⚠️ Без возврата\n\n"

        message_text += f"*Вы уверены?*"

        await state.update_data(
            selected_appointment=selected_appt,
            refund_info=refund_info
        )

        await message.answer(
            message_text,
            reply_markup=confirm_cancel_kb(),
            parse_mode="Markdown"
        )

        await state.set_state(CancelState.confirm_cancel)

    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_kb())
        await state.clear()


@dp.message(CancelState.confirm_cancel)
async def confirm_cancellation(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await state.set_state(CancelState.choose_appointment)
        data = await state.get_data()
        appointments_list = data.get('appointments_list', [])

        keyboard_buttons = []
        for i, appt in enumerate(appointments_list, 1):
            button_text = f"{i}. {appt['date_display']} {appt['time']}"
            keyboard_buttons.append([KeyboardButton(text=button_text)])

        keyboard_buttons.append([KeyboardButton(text="⬅️ Назад")])

        await message.answer(
            "Выберите запись:",
            reply_markup=ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
        )
        return

    if message.text != "✅ Да, отменить":
        await message.answer("Отмена отменена 😊", reply_markup=main_kb())
        await state.clear()
        return

    data = await state.get_data()
    selected_appt = data.get('selected_appointment')
    refund_info = data.get('refund_info', {})

    if not selected_appt:
        await message.answer("Ошибка.", reply_markup=main_kb())
        await state.clear()
        return

    date_key = selected_appt['date_key']
    time_key = selected_appt['time_key']

    if date_key in appointments_db and time_key in appointments_db[date_key]:
        appointment_data = appointments_db[date_key][time_key]

        del appointments_db[date_key][time_key]
        if not appointments_db[date_key]:
            del appointments_db[date_key]

        cancelled_appointments.append({
            **appointment_data,
            'cancelled_at': datetime.now(),
            'refund_amount': refund_info.get('refund_amount', 0),
            'cancelled_by': message.from_user.id
        })

        client_msg = f"✅ *Запись отменена!*\n\n"
        client_msg += f"📅 {selected_appt['date_display']} {selected_appt['time']}\n"
        client_msg += f"💅 {selected_appt['service']}\n"

        if selected_appt['paid']:
            if refund_info.get('refund_amount', 0) > 0:
                client_msg += f"\n💰 *К возврату:* {refund_info['refund_amount']}₽\n"
                client_msg += f"⏰ *Срок:* 1-3 рабочих дня\n"
                client_msg += f"📞 *Контакты:* +7 (999) 123-45-67"
            else:
                client_msg += f"\n⚠️ *Без возврата*\n"
                client_msg += f"(менее 3 часов до записи)"
        else:
            client_msg += f"\n💳 Запись не была оплачена"

        await message.answer(client_msg, reply_markup=main_kb(), parse_mode="Markdown")

        admin_msg = f"🚨 *ОТМЕНА ЗАПИСИ!*\n\n"
        admin_msg += f"👤 {appointment_data.get('name')}\n"
        admin_msg += f"📞 {appointment_data.get('phone')}\n"
        admin_msg += f"📅 {selected_appt['date_display']}\n"
        admin_msg += f"⏰ {selected_appt['time']}\n"
        admin_msg += f"💅 {selected_appt['service']}\n"
        admin_msg += f"💰 {selected_appt['price']}₽\n"
        admin_msg += f"💳 Оплачено: {'✅ Да' if selected_appt['paid'] else '❌ Нет'}\n\n"

        if selected_appt['paid']:
            admin_msg += f"📊 *ВОЗВРАТ:*\n"
            admin_msg += f"• До записи: {refund_info.get('hours_left', 0)}ч\n"
            admin_msg += f"• Процент: {refund_info.get('percent', 0)}%\n"
            admin_msg += f"• Сумма: *{refund_info.get('refund_amount', 0)}₽*\n"
            admin_msg += f"• Штраф: {refund_info.get('penalty', 0)}₽\n\n"

            if refund_info.get('refund_amount', 0) > 0:
                admin_msg += f"⚠️ *ТРЕБУЕТСЯ ВОЗВРАТ!*\n"
                admin_msg += f"Верните: *{refund_info['refund_amount']}₽*\n"
                admin_msg += f"На номер: *{appointment_data.get('phone')}*"

        await bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")

    await state.clear()


# ========== МОИ ПЛАТЕЖИ ==========
@dp.message(F.text == "💰 Мои платежи")
async def my_payments_list(message: types.Message):
    user_payments = []

    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            if appointment['user_id'] == message.from_user.id and appointment.get('paid'):
                date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
                user_payments.append(
                    f"📅 {date_display} {time_key}\n"
                    f"💅 {appointment['service']}\n"
                    f"💰 {appointment.get('price', 0)}₽ оплачено"
                )

    if user_payments:
        text = "💰 *Ваши оплаты:*\n\n" + "\n".join(user_payments)
    else:
        text = "У вас нет оплаченных записей."

    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")


# ========== АДМИН ПАНЕЛЬ ==========
@dp.message(F.text == "👑 Админ")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Доступ запрещен")
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
        f"Выберите раздел:",
        reply_markup=admin_main_kb(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "📊 Статистика")
async def admin_statistics(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    today_appointments = len(appointments_db.get(today, {}))

    total_refund = sum(
        appt.get('refund_amount', 0)
        for appt in cancelled_appointments
    )

    stats_text = f"📊 *Детальная статистика*\n\n"
    stats_text += f"📅 *Сегодня ({datetime.now().strftime('%d.%m.%Y')}):*\n"
    stats_text += f"• Записей: {today_appointments}\n"
    stats_text += f"• Оплачено: {sum(1 for appt in appointments_db.get(today, {}).values() if appt.get('paid'))}\n\n"

    stats_text += f"📈 *Общая:*\n"
    stats_text += f"• Клиентов: {len(users_db)}\n"
    stats_text += f"• Средний чек: {total_revenue // max(paid_appointments, 1) if paid_appointments > 0 else 0}₽\n"
    stats_text += f"• Возвратов: {total_refund}₽\n\n"

    stats_text += f"💰 *Финансы:*\n"
    stats_text += f"• Ожидают оплаты: {len(pending_payments)}\n"
    stats_text += f"• ЮMoney кошелек: {YOOMONEY_WALLET}"

    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(F.text == "📋 Все записи")
async def all_appointments(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not appointments_db:
        await message.answer("Нет активных записей.")
        return

    all_appts = []
    for date_key, times in appointments_db.items():
        for time_key, appointment in times.items():
            date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
            status = "✅ Оплачено" if appointment.get('paid') else "⏳ Не оплачено"
            all_appts.append(
                f"📅 {date_display} {time_key} - {appointment['service']} - {appointment.get('name')} - {status}")

    if all_appts:
        text = "📋 *Все записи:*\n\n" + "\n".join(all_appts[:20])
        if len(all_appts) > 20:
            text += f"\n\n... и ещё {len(all_appts) - 20} записей"
    else:
        text = "Нет записей."

    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "💰 Финансы")
async def admin_finances(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_revenue = sum(
        appt.get('price', 0)
        for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid')
    )

    total_refund = sum(
        appt.get('refund_amount', 0)
        for appt in cancelled_appointments
    )

    net_income = total_revenue - total_refund

    finances_text = f"💰 *Финансы*\n\n"
    finances_text += f"💸 *Доходы:*\n"
    finances_text += f"• Общая выручка: {total_revenue}₽\n"
    finances_text += f"• Чистая прибыль: {net_income}₽\n\n"

    finances_text += f"↩️ *Расходы:*\n"
    finances_text += f"• Возвраты: {total_refund}₽\n\n"

    finances_text += f"📊 *Показатели:*\n"
    finances_text += f"• Средний чек: {total_revenue // max(len([appt for times in appointments_db.values() for appt in times.values() if appt.get('paid')]), 1)}₽\n"
    finances_text += f"• Конверсия в оплату: {len([appt for times in appointments_db.values() for appt in times.values() if appt.get('paid')]) / max(len([appt for times in appointments_db.values() for appt in times.values()]), 1) * 100:.1f}%"

    await message.answer(finances_text, parse_mode="Markdown")


@dp.message(F.text == "🔄 Управление")
async def admin_management(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "🔄 *Управление системой*\n\n"
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🗑 Очистить старые записи")],
                [KeyboardButton(text="📤 Экспорт данных")],
                [KeyboardButton(text="🔄 Сбросить бота")],
                [KeyboardButton(text="⬅️ В админку")]
            ],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(F.text == "🖼️ Галерея")
async def admin_gallery(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "🖼️ *Управление галереей*\n\n"
        f"Всего фото: {len(gallery_photos)}\n"
        f"Последнее фото: {gallery_photos[-1]['added_at'][:10] if gallery_photos else 'никогда'}\n\n"
        "Выберите действие:",
        reply_markup=gallery_admin_kb(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "📤 Добавить фото")
async def add_photo_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.set_state(GalleryState.waiting_photo)
    await message.answer(
        "📤 *Добавление фото*\n\n"
        "Отправьте фото:\n\n"
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
    photo_file_id = message.photo[-1].file_id

    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(GalleryState.waiting_caption)

    await message.answer(
        "✅ Фото получено!\n\n"
        "Добавьте подпись:\n"
        "(или отправьте 'без подписи')",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="без подписи")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )


@dp.message(GalleryState.waiting_caption)
async def save_photo_caption(message: types.Message, state: FSMContext):
    if message.text.lower() == "❌ отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=gallery_admin_kb())
        return

    data = await state.get_data()
    photo_file_id = data.get('photo_file_id')

    if not photo_file_id:
        await message.answer("Ошибка", reply_markup=gallery_admin_kb())
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
    save_gallery_to_file()

    await message.answer(
        f"✅ Фото добавлено!\n\n"
        f"📝 Подпись: {caption if caption else '(без подписи)'}\n"
        f"🖼 Всего фото: {len(gallery_photos)}",
        reply_markup=gallery_admin_kb(),
        parse_mode="Markdown"
    )

    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_file_id,
        caption=f"✅ Добавлено\n{caption}" if caption else "✅ Добавлено"
    )

    await state.clear()


@dp.message(F.text == "🗑 Удалить фото")
async def delete_photo_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if not gallery_photos:
        await message.answer("Нет фото для удаления", reply_markup=gallery_admin_kb())
        return

    await message.answer(
        "🗑 *Удаление фото*\n\n"
        "Последние фото:",
        reply_markup=gallery_admin_kb(),
        parse_mode="Markdown"
    )

    for i, photo_data in enumerate(gallery_photos[-5:], 1):
        try:
            index = len(gallery_photos) - 5 + i - 1
            caption = f"{i}. {photo_data.get('caption', 'Фото')}\n"
            caption += f"📅 {photo_data['added_at'][:10]}\n"
            caption += f"ID: {index}"

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_data['file_id'],
                caption=caption
            )
            await asyncio.sleep(0.3)
        except (telegram.error.TelegramError, IOError, TypeError) as e:
            print(f"Ошибка отправки фото: {e}")
            continue

    await message.answer(
        "Для удаления отправьте номер (1-5):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
                [KeyboardButton(text="4"), KeyboardButton(text="5")],
                [KeyboardButton(text="❌ Отмена")]
            ],
            resize_keyboard=True
        )
    )

    await state.set_state(GalleryState.confirm_delete)


@dp.message(GalleryState.confirm_delete)
async def confirm_delete_photo(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=gallery_admin_kb())
        return

    try:
        photo_num = int(message.text)
        if 1 <= photo_num <= 5:
            index = len(gallery_photos) - 5 + (photo_num - 1)

            if 0 <= index < len(gallery_photos):
                deleted_photo = gallery_photos.pop(index)
                save_gallery_to_file()

                await message.answer(
                    f"✅ Фото удалено!\n\n"
                    f"Подпись: {deleted_photo.get('caption', '(без подписи)')}\n"
                    f"Осталось: {len(gallery_photos)}",
                    reply_markup=gallery_admin_kb()
                )
            else:
                await message.answer("Неверный индекс", reply_markup=gallery_admin_kb())
        else:
            await message.answer("Введите число 1-5", reply_markup=gallery_admin_kb())
    except ValueError:
        await message.answer("Введите число 1-5", reply_markup=gallery_admin_kb())

    await state.clear()


@dp.message(F.text == "📊 Статистика фото")
async def gallery_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not gallery_photos:
        await message.answer("Нет фото", reply_markup=gallery_admin_kb())
        return

    from collections import Counter
    dates = [photo['added_at'][:10] for photo in gallery_photos]
    date_counts = Counter(dates)

    stats_text = "📊 *Статистика галереи*\n\n"
    stats_text += f"Всего фото: {len(gallery_photos)}\n"
    stats_text += f"С подписями: {sum(1 for p in gallery_photos if p.get('caption'))}\n"
    stats_text += f"Первое фото: {min(dates)}\n"
    stats_text += f"Последнее фото: {max(dates)}\n\n"

    stats_text += "📅 По дням:\n"
    for date, count in sorted(date_counts.items(), reverse=True)[:5]:
        stats_text += f"• {date}: {count} фото\n"

    await message.answer(stats_text, reply_markup=gallery_admin_kb(), parse_mode="Markdown")


@dp.message(F.text == "⚙️ Настройки")
async def admin_settings(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "⚙️ *Настройки системы*\n\n"
        f"🤖 Бот: @Probnik312Bot\n"
        f"👑 Админ ID: {ADMIN_ID}\n"
        f"💰 ЮMoney: {YOOMONEY_WALLET}\n\n"
        "📊 Версия: 2.0\n"
        "🔄 Последнее обновление: 25.12.2024",
        reply_markup=admin_main_kb(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "⬅️ В админку")
@dp.message(F.text == "⬅️ В меню")
async def back_to_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    await admin_panel(message)


# ========== ОБРАБОТКА ОТМЕН В ГАЛЕРЕЕ ==========
@dp.message(GalleryState.waiting_photo, F.text == "❌ Отмена")
@dp.message(GalleryState.waiting_caption, F.text == "❌ Отмена")
async def cancel_gallery_operation(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Операция отменена", reply_markup=gallery_admin_kb())


# ========== ЗАПУСК ==========
async def main():
    logger.info("=" * 60)
    logger.info("💅 БОТ ДЛЯ НОГТЕВОЙ СТУДИИ ЗАПУЩЕН НА RAILWAY")
    logger.info(f"🤖 Токен: {'установлен' if BOT_TOKEN else 'НЕТ!'}")
    logger.info(f"👑 Админ ID: {ADMIN_ID}")
    logger.info(f"💰 ЮMoney: {YOOMONEY_WALLET}")
    logger.info("=" * 60)

    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        logger.info("Бот завершил работу")

if __name__ == "__main__":
    asyncio.run(main())
