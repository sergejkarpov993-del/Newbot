import asyncio
import logging
import uuid
import json
import os
from datetime import datetime, timedelta
from collections import Counter
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text
from dotenv import load_dotenv
import atexit

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
load_dotenv()

# ========== БЕЗОПАСНАЯ КОНФИГУРАЦИЯ ==========
VK_TOKEN = os.getenv("VK_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_VK_ID", "0"))
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "")

# ========== ПРОВЕРКА КОНФИГУРАЦИИ ==========
def check_configuration():
    errors = []
    if not VK_TOKEN:
        errors.append("❌ VK_TOKEN не установлен")
    if ADMIN_ID == 0:
        errors.append("❌ ADMIN_VK_ID не установлен")
    return errors

config_errors = check_configuration()
if config_errors:
    print("=" * 60)
    print("❌ ОШИБКА КОНФИГУРАЦИИ")
    print("=" * 60)
    for error in config_errors:
        print(error)
    print("\nℹ️  ИНСТРУКЦИЯ:")
    print("1. Создайте файл .env")
    print("2. Добавьте VK_TOKEN=ваш_токен_группы")
    print("3. Добавьте ADMIN_VK_ID=ваш_vk_id")
    print("=" * 60)
    import sys
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=VK_TOKEN)

# ========== БАЗЫ ДАННЫХ ==========
APPOINTMENTS_DB_FILE = "vk_appointments_db.json"
USERS_DB_FILE = "vk_users_db.json"
PENDING_PAYMENTS_FILE = "vk_pending_payments.json"

users_db = {}
appointments_db = {}
pending_payments = {}
user_states = {}


# ========== ЗАГРУЗКА И СОХРАНЕНИЕ ==========
def load_all_data():
    global appointments_db, users_db, pending_payments

    def load_json(file_path, default):
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return default
        return default

    appointments_db = load_json(APPOINTMENTS_DB_FILE, {})
    users_db = load_json(USERS_DB_FILE, {})
    pending_payments = load_json(PENDING_PAYMENTS_FILE, {})

    logger.info(f"✅ Загружено: {len(appointments_db)} записей, {len(users_db)} клиентов")


def save_all_data():
    try:
        with open(APPOINTMENTS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(appointments_db, f, ensure_ascii=False, indent=2, default=str)
        with open(USERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(users_db, f, ensure_ascii=False, indent=2, default=str)
        with open(PENDING_PAYMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(pending_payments, f, ensure_ascii=False, indent=2, default=str)

        total = sum(len(times) for times in appointments_db.values())
        logger.info(f"💾 Сохранено: {total} записей, {len(users_db)} клиентов")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")


load_all_data()
atexit.register(save_all_data)


# ========== УСЛУГИ ==========
services_db = {
    'manicure': {'name': 'Маникюр', 'price': 1500, 'duration': 60},
    'pedicure': {'name': 'Педикюр', 'price': 2000, 'duration': 90},
    'cover': {'name': 'Покрытие', 'price': 800, 'duration': 30}
}


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_free_slots(date, service_key):
    free_slots = []
    service_duration = services_db[service_key]['duration']
    current_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=10)
    end_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=20)

    while current_time + timedelta(minutes=service_duration) <= end_time:
        time_str = current_time.strftime("%H:%M")
        date_key = date.strftime("%Y-%m-%d")

        is_free = True
        for minute in range(0, service_duration, 30):
            check_slot = (current_time + timedelta(minutes=minute)).strftime("%H:%M")
            if appointments_db.get(date_key, {}).get(check_slot):
                is_free = False
                break

        if is_free:
            free_slots.append(time_str)

        current_time += timedelta(minutes=60)

    return free_slots


def create_payment_link(amount, label, comment):
    import urllib.parse
    if not YOOMONEY_WALLET:
        return "https://example.com/pay"
    
    params = {
        'receiver': YOOMONEY_WALLET,
        'quickpay-form': 'shop',
        'targets': comment,
        'sum': amount,
        'label': label
    }
    return f"https://yoomoney.ru/quickpay/confirm.xml?{urllib.parse.urlencode(params)}"


# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    kb = Keyboard(one_time=False)
    kb.add(Text("📅 Записаться"), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Text("📋 Мои записи"))
    return kb.get_json()


def admin_keyboard():
    kb = Keyboard(one_time=False)
    kb.add(Text("📊 Статистика"))
    kb.row()
    kb.add(Text("📅 Все записи"))
    kb.add(Text("👥 Клиенты"))
    kb.row()
    kb.add(Text("⬅️ В меню"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def services_keyboard():
    kb = Keyboard(one_time=True)
    for key, service in services_db.items():
        kb.add(Text(f"{service['name']} - {service['price']}₽"))
        kb.row()
    kb.add(Text("⬅️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def dates_keyboard():
    kb = Keyboard(one_time=True)
    today = datetime.now().date()
    
    for i in range(7):
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        if i == 0:
            label = f"Сегодня ({date_str})"
        elif i == 1:
            label = f"Завтра ({date_str})"
        else:
            day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date.weekday()]
            label = f"{day_name} ({date_str})"
        kb.add(Text(label))
        kb.row()
    
    kb.add(Text("⬅️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def times_keyboard(slots):
    kb = Keyboard(one_time=True)
    for i in range(0, len(slots), 3):
        for slot in slots[i:i+3]:
            kb.add(Text(slot))
        kb.row()
    kb.add(Text("⬅️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


# ========== ОБРАБОТЧИКИ ==========
@bot.on.message(text=["Начать", "/start", "начать"])
async def start_handler(message: Message):
    user_id = message.from_id
    
    try:
        user_info = await bot.api.users.get(user_ids=[user_id])
        user_name = user_info[0].first_name
    except:
        user_name = "Пользователь"
    
    if user_id == ADMIN_ID:
        text = f"👑 Привет, {user_name}!\n\nВы вошли как администратор"
        await message.answer(text, keyboard=admin_keyboard())
    else:
        text = (
            f"👋 Привет, {user_name}!\n\n"
            f"💅 Добро пожаловать в салон красоты!\n\n"
            f"Здесь вы можете:\n"
            f"• 📅 Записаться на услуги\n"
            f"• 📋 Посмотреть свои записи"
        )
        await message.answer(text, keyboard=main_keyboard())


@bot.on.message(text="📅 Записаться")
async def booking_start(message: Message):
    user_states[message.from_id] = {'step': 'choose_service'}
    await message.answer("💅 Выберите услугу:", keyboard=services_keyboard())


@bot.on.message(text="📋 Мои записи")
async def my_appointments(message: Message):
    user_id = message.from_id
    
    user_appts = []
    for date_key, times in appointments_db.items():
        for time_key, appt in times.items():
            if appt.get('user_id') == user_id:
                user_appts.append({
                    'date': date_key,
                    'time': time_key,
                    'service': appt.get('service'),
                    'price': appt.get('price'),
                    'paid': appt.get('paid', False)
                })
    
    if not user_appts:
        await message.answer(
            "📋 У вас пока нет записей.\n\n"
            "Нажмите 📅 Записаться!",
            keyboard=main_keyboard()
        )
        return
    
    user_appts.sort(key=lambda x: (x['date'], x['time']))
    
    text = "📋 Ваши записи:\n\n"
    for i, appt in enumerate(user_appts, 1):
        date_display = datetime.strptime(appt['date'], "%Y-%m-%d").strftime("%d.%m.%Y")
        status = "✅ Оплачено" if appt['paid'] else "⏳ Ожидает"
        
        text += (
            f"{i}. {date_display} в {appt['time']}\n"
            f"   💅 {appt['service']}\n"
            f"   💰 {appt['price']}₽ | {status}\n\n"
        )
    
    await message.answer(text, keyboard=main_keyboard())


@bot.on.message(text="📊 Статистика")
async def stats_handler(message: Message):
    if message.from_id != ADMIN_ID:
        return
    
    total_appts = sum(len(times) for times in appointments_db.values())
    paid_appts = sum(
        1 for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid', False)
    )
    revenue = sum(
        appt.get('price', 0)
        for times in appointments_db.values()
        for appt in times.values()
        if appt.get('paid', False)
    )
    
    text = (
        f"📊 Статистика:\n\n"
        f"📅 Всего записей: {total_appts}\n"
        f"✅ Оплачено: {paid_appts}\n"
        f"💰 Выручка: {revenue}₽\n"
        f"👥 Клиентов: {len(users_db)}"
    )
    
    await message.answer(text, keyboard=admin_keyboard())


@bot.on.message(text="📅 Все записи")
async def all_appointments(message: Message):
    if message.from_id != ADMIN_ID:
        return
    
    if not appointments_db:
        await message.answer("📅 Записей нет", keyboard=admin_keyboard())
        return
    
    text = "📅 Все записи:\n\n"
    for date_key in sorted(appointments_db.keys())[:5]:  # Первые 5 дней
        date_display = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m.%Y")
        text += f"📆 {date_display}:\n"
        
        for time_key in sorted(appointments_db[date_key].keys()):
            appt = appointments_db[date_key][time_key]
            status = "✅" if appt.get('paid') else "⏳"
            text += f"  {status} {time_key} - {appt['name']} ({appt['service']})\n"
        text += "\n"
    
    await message.answer(text, keyboard=admin_keyboard())


@bot.on.message(text="👥 Клиенты")
async def clients_handler(message: Message):
    if message.from_id != ADMIN_ID:
        return
    
    if not users_db:
        await message.answer("👥 Клиентов нет", keyboard=admin_keyboard())
        return
    
    text = f"👥 Всего клиентов: {len(users_db)}\n\n"
    for user_id, user_data in list(users_db.items())[:10]:
        appts_count = sum(
            1 for times in appointments_db.values()
            for appt in times.values()
            if str(appt.get('user_id')) == str(user_id)
        )
        text += f"👤 {user_data['name']} | 📞 {user_data['phone']} | 📅 {appts_count}\n"
    
    await message.answer(text, keyboard=admin_keyboard())


@bot.on.message(text="⬅️ В меню")
async def back_to_menu(message: Message):
    user_id = message.from_id
    if user_id in user_states:
        del user_states[user_id]
    
    if user_id == ADMIN_ID:
        await message.answer("🏠 Админ-панель:", keyboard=admin_keyboard())
    else:
        await message.answer("🏠 Главное меню:", keyboard=main_keyboard())


@bot.on.message()
async def message_handler(message: Message):
    user_id = message.from_id
    text = message.text
    
    if user_id not in user_states:
        await message.answer(
            "❓ Используйте меню:",
            keyboard=main_keyboard() if user_id != ADMIN_ID else admin_keyboard()
        )
        return
    
    state = user_states[user_id]
    step = state.get('step')
    
    # Назад
    if text == "⬅️ Назад":
        if step == 'choose_service':
            del user_states[user_id]
            await message.answer("🏠 Главное меню:", keyboard=main_keyboard())
        elif step == 'choose_date':
            state['step'] = 'choose_service'
            await message.answer("💅 Выберите услугу:", keyboard=services_keyboard())
        elif step == 'choose_time':
            state['step'] = 'choose_date'
            await message.answer("📅 Выберите дату:", keyboard=dates_keyboard())
        elif step in ['enter_name', 'enter_phone']:
            state['step'] = 'choose_time'
            free_slots = get_free_slots(state['date_obj'], state['service_key'])
            await message.answer("⏰ Выберите время:", keyboard=times_keyboard(free_slots))
        return
    
    # Выбор услуги
    if step == 'choose_service':
        for key, service in services_db.items():
            if service['name'] in text:
                state.update({
                    'service_key': key,
                    'service_name': service['name'],
                    'price': service['price'],
                    'step': 'choose_date'
                })
                await message.answer(
                    f"✅ {service['name']} - {service['price']}₽\n\n"
                    f"📅 Выберите дату:",
                    keyboard=dates_keyboard()
                )
                return
        await message.answer("❌ Выберите из списка:", keyboard=services_keyboard())
        return
    
    # Выбор даты
    if step == 'choose_date':
        try:
            today = datetime.now().date()
            
            if "Сегодня" in text:
                selected_date = today
            elif "Завтра" in text:
                selected_date = today + timedelta(days=1)
            else:
                import re
                match = re.search(r'\((\d{2}\.\d{2})\)', text)
                if match:
                    date_str = match.group(1)
                    day, month = map(int, date_str.split('.'))
                    year = today.year
                    if month < today.month:
                        year += 1
                    selected_date = datetime(year, month, day).date()
                else:
                    raise ValueError()
            
            free_slots = get_free_slots(selected_date, state['service_key'])
            
            if not free_slots:
                await message.answer("❌ Нет свободных мест. Выберите другую дату:", keyboard=dates_keyboard())
                return
            
            state.update({
                'date_obj': selected_date,
                'date_display': selected_date.strftime("%d.%m.%Y"),
                'step': 'choose_time'
            })
            
            await message.answer(
                f"✅ Дата: {state['date_display']}\n\n⏰ Выберите время:",
                keyboard=times_keyboard(free_slots)
            )
            
        except:
            await message.answer("❌ Выберите из списка:", keyboard=dates_keyboard())
        return
    
    # Выбор времени
    if step == 'choose_time':
        if ":" not in text or len(text) != 5:
            await message.answer("❌ Выберите время из списка")
            return
        
        free_slots = get_free_slots(state['date_obj'], state['service_key'])
        if text not in free_slots:
            await message.answer("❌ Время занято")
            return
        
        state.update({'time': text, 'step': 'enter_name'})
        
        kb = Keyboard(one_time=True)
        kb.add(Text("⬅️ Назад"), color=KeyboardButtonColor.NEGATIVE)
        
        await message.answer(
            f"✅ Время: {text}\n\n👤 Введите ваше имя:",
            keyboard=kb.get_json()
        )
        return
    
    # Ввод имени
    if step == 'enter_name':
        if len(text.strip()) < 2:
            await message.answer("❌ Минимум 2 символа")
            return
        
        state.update({'name': text.strip(), 'step': 'enter_phone'})
        
        kb = Keyboard(one_time=True)
        kb.add(Text("⬅️ Назад"), color=KeyboardButtonColor.NEGATIVE)
        
        await message.answer(
            f"✅ Имя: {text}\n\n📞 Введите телефон:",
            keyboard=kb.get_json()
        )
        return
    
    # Ввод телефона
    if step == 'enter_phone':
        phone = ''.join(filter(lambda x: x.isdigit() or x == '+', text))
        
        if len(phone) < 10:
            await message.answer("❌ Неверный формат")
            return
        
        state['phone'] = phone
        
        # Генерируем ID
        payment_id = str(uuid.uuid4())[:8]
        
        # Сохраняем
        pending_payments[payment_id] = {
            'user_id': user_id,
            'name': state['name'],
            'phone': state['phone'],
            'service_name': state['service_name'],
            'service_key': state['service_key'],
            'price': state['price'],
            'date_obj': state['date_obj'].isoformat(),
            'date_display': state['date_display'],
            'time': state['time'],
            'created_at': datetime.now().isoformat()
        }
        
        payment_link = create_payment_link(
            state['price'],
            payment_id,
            f"Оплата {state['service_name']}"
        )
        
        save_all_data()
        
        confirmation = (
            f"✅ Данные заполнены!\n\n"
            f"📋 Детали:\n"
            f"• {state['service_name']}\n"
            f"• {state['price']}₽\n"
            f"• {state['date_display']} в {state['time']}\n"
            f"• {state['name']}\n"
            f"• {state['phone']}\n\n"
            f"💳 Ссылка для оплаты:\n{payment_link}\n\n"
            f"⚠️ После оплаты напишите 'Оплатил'\n"
            f"🆔 ID: {payment_id}"
        )
        
        kb = Keyboard(one_time=True)
        kb.add(Text(f"✅ Я оплатил (ТЕСТ)"), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Text("⬅️ Отмена"), color=KeyboardButtonColor.NEGATIVE)
        
        await message.answer(confirmation, keyboard=kb.get_json())
        
        state.update({'step': 'waiting_payment', 'payment_id': payment_id})
        return
    
    # Ожидание оплаты
    if step == 'waiting_payment':
        if "оплатил" in text.lower() or "ТЕСТ" in text:
            payment_id = state.get('payment_id')
            
            if payment_id and payment_id in pending_payments:
                await process_payment(message, payment_id)
                del user_states[user_id]
            else:
                await message.answer("❌ Платёж не найден", keyboard=main_keyboard())
                del user_states[user_id]
        else:
            await message.answer("⏳ Ожидаю подтверждения...")
        return


async def process_payment(message: Message, payment_id: str):
    user_id = message.from_id
    
    logger.info(f"🔔 Оплата {payment_id} от {user_id}")
    
    if payment_id not in pending_payments:
        await message.answer("❌ Платёж не найден", keyboard=main_keyboard())
        return
    
    try:
        payment_data = pending_payments[payment_id]
        
        if isinstance(payment_data['date_obj'], str):
            date_obj = datetime.fromisoformat(payment_data['date_obj']).date()
            date_key = date_obj.strftime("%Y-%m-%d")
        else:
            date_key = payment_data['date_obj']
        
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
            'paid': True,
            'created_at': datetime.now().isoformat(),
            'payment_method': 'test'
        }
        
        users_db[str(payment_data['user_id'])] = {
            'name': payment_data['name'],
            'phone': payment_data['phone'],
            'last_appointment': datetime.now().isoformat()
        }
        
        save_all_data()
        
        # Уведомление админу
        admin_text = (
            f"💰 Новая запись!\n\n"
            f"👤 {payment_data['name']}\n"
            f"📞 {payment_data['phone']}\n"
            f"💅 {payment_data['service_name']}\n"
            f"💰 {payment_data['price']}₽\n"
            f"📅 {payment_data['date_display']} в {payment_data['time']}\n\n"
            f"🆔 {payment_id}"
        )
        
        try:
            await bot.api.messages.send(
                user_id=ADMIN_ID,
                message=admin_text,
                random_id=0
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу: {e}")
        
        # Клиенту
        success_text = (
            f"🎉 Запись успешно оплачена!\n\n"
            f"✅ Детали:\n"
            f"• {payment_data['service_name']}\n"
            f"• {payment_data['price']}₽\n"
            f"• {payment_data['date_display']} в {payment_data['time']}\n\n"
            f"📍 Адрес: ул. Примерная, д. 1\n"
            f"📞 Телефон: +7 (999) 123-45-67\n\n"
            f"✨ Ждём вас!"
        )
        
        await message.answer(success_text, keyboard=main_keyboard())
        
        del pending_payments[payment_id]
        save_all_data()
        
    except Exception as e:
        logger.error(f"Ошибка обработки оплаты: {e}")
        await message.answer("❌ Ошибка. Попробуйте ещё раз.", keyboard=main_keyboard())


# ========== ЗАПУСК ==========
async def main():
    logger.info("=" * 60)
    logger.info("✨ VK БОТ ЗАПУЩЕН ✨")
    logger.info(f"👑 Админ: {ADMIN_ID}")
    logger.info(f"📊 Записей: {sum(len(times) for times in appointments_db.values())}")
    logger.info("=" * 60)
    
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
