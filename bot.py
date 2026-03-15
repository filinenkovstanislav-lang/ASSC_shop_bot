import asyncio
import logging
import json
import os
import html
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import aiosqlite

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Чтение токена и ID администратора из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задан токен бота! Установите переменную окружения BOT_TOKEN")
if ADMIN_ID == 0:
    raise ValueError("Не задан ID администратора! Установите переменную окружения ADMIN_ID")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Состояния FSM ---
class AddProduct(StatesGroup):
    name = State()
    description = State()
    price = State()
    photo = State()

class EditProduct(StatesGroup):
    name = State()
    description = State()
    price = State()
    photo = State()

class Order(StatesGroup):
    contact = State()

class EditContacts(StatesGroup):
    waiting_for_field = State()
    waiting_for_value = State()

class SetNotificationUser(StatesGroup):
    waiting_for_username = State()

# --- Работа с базой данных ---
DB_PATH = "shop.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                is_admin INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL DEFAULT 0,
                photo_file_id TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cart (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                contact TEXT,
                items TEXT,
                total_price REAL,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # Удаляем старые ключи email и phone, если они есть
        await db.execute("DELETE FROM settings WHERE key IN ('email', 'phone')")
        # Вставляем новые ключи, если их нет
        await db.execute('''
            INSERT OR IGNORE INTO settings (key, value) VALUES 
                ('contact_info', '@shop_support'),
                ('work_hours', 'Круглосуточно'),
                ('notification_username', '')
        ''')
        await db.commit()

async def register_user(telegram_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username)
        )
        await db.commit()

async def is_admin(telegram_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT is_admin FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()
        return row is not None and row[0] == 1

async def get_contacts():
    """Возвращает словарь с контактами из БД (contact_info, work_hours)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT key, value FROM settings WHERE key IN ('contact_info', 'work_hours')"
        )
        rows = await cursor.fetchall()
        return {key: value for key, value in rows}

async def get_notification_username():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = 'notification_username'")
        row = await cursor.fetchone()
        return row[0] if row else ""

async def set_notification_username(username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('notification_username', ?)",
            (username,)
        )
        await db.commit()

async def get_notification_chat_id() -> int:
    """Определяем chat_id получателя уведомлений: если указан username и он есть в users, берём его telegram_id, иначе ADMIN_ID"""
    username = await get_notification_username()
    if username:
        # удаляем @ если есть
        clean_username = username.lstrip('@')
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM users WHERE username = ?",
                (clean_username,)
            )
            row = await cursor.fetchone()
            if row:
                return row[0]
    return ADMIN_ID

# --- Клавиатуры ---
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🛍 Меню товаров")],
        [KeyboardButton(text="🛒 Корзина")],
        [KeyboardButton(text="📞 Контакты")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="🔧 Админ панель")])
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )
    return keyboard

# --- Команда /start ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await register_user(message.from_user.id, message.from_user.username)
    keyboard = get_main_keyboard(message.from_user.id)
    await message.answer(
        "Добро пожаловать в магазин!\n"
        "Используйте кнопки ниже для навигации.",
        reply_markup=keyboard
    )

# --- Команда /addproduct ---
@dp.message(Command("addproduct"))
async def cmd_addproduct(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав администратора.")
        return
    await state.set_state(AddProduct.name)
    await message.answer("Введите название товара:")

@dp.message(AddProduct.name)
async def addproduct_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddProduct.description)
    await message.answer("Введите описание товара:")

@dp.message(AddProduct.description)
async def addproduct_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AddProduct.price)
    await message.answer("Введите цену товара (только число, например 199.99):")

@dp.message(AddProduct.price)
async def addproduct_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное положительное число.")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.photo)
    await message.answer("Отправьте фото товара:")

@dp.message(AddProduct.photo, F.photo)
async def addproduct_photo(message: Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    data = await state.get_data()
    name = data['name']
    description = data['description']
    price = data['price']

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO products (name, description, price, photo_file_id) VALUES (?, ?, ?, ?)",
            (name, description, price, photo_file_id)
        )
        await db.commit()

    await state.clear()
    keyboard = get_main_keyboard(message.from_user.id)
    await message.answer("✅ Товар успешно добавлен!", reply_markup=keyboard)

@dp.message(AddProduct.photo)
async def addproduct_photo_invalid(message: Message):
    await message.answer("Пожалуйста, отправьте фото.")

# --- Команда /menu ---
@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await register_user(message.from_user.id, message.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name, description, price, photo_file_id FROM products")
        products = await cursor.fetchall()

    if not products:
        await message.answer("Товаров пока нет.")
        return

    for prod in products:
        prod_id, name, desc, price, photo_id = prod
        safe_name = html.escape(name)
        safe_desc = html.escape(desc)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_{prod_id}")]
            ]
        )
        caption = f"<b>{safe_name}</b>\n{safe_desc}\n💰 Цена: {price:.2f} руб."
        if photo_id:
            await message.answer_photo(
                photo=photo_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)

# --- Добавление в корзину ---
@dp.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: CallbackQuery):
    product_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ?",
            (user_id, product_id)
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE cart SET quantity = quantity + 1 WHERE id = ?",
                (row[0],)
            )
        else:
            await db.execute(
                "INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)",
                (user_id, product_id)
            )
        await db.commit()

    await callback.answer("✅ Товар добавлен в корзину!")

# --- Команда /cart ---
@dp.message(Command("cart"))
async def cmd_cart(message: Message):
    user_id = message.from_user.id
    await show_cart(message, user_id)

async def show_cart(message: Message, user_id: int, edit: bool = False, callback_query: CallbackQuery = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT p.id, p.name, c.quantity, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (user_id,))
        items = await cursor.fetchall()

    if not items:
        text = "Ваша корзина пуста."
        if edit and callback_query:
            await callback_query.message.edit_text(text)
        else:
            await message.answer(text)
        return

    text = "🛒 <b>Ваша корзина:</b>\n"
    total = 0.0
    keyboard_buttons = []

    for prod_id, name, qty, price in items:
        item_total = price * qty
        total += item_total
        safe_name = html.escape(name)
        text += f"• {safe_name} — {qty} шт. × {price:.2f} руб. = {item_total:.2f} руб.\n"
        keyboard_buttons.append([InlineKeyboardButton(
            text=f"❌ Удалить {safe_name}",
            callback_data=f"remove_{prod_id}"
        )])

    text += f"\n<b>Итого: {total:.2f} руб.</b>"

    keyboard_buttons.append([
        InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"),
        InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if edit and callback_query:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

# --- Удаление из корзины ---
@dp.callback_query(F.data.startswith("remove_"))
async def remove_from_cart(callback: CallbackQuery):
    product_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM cart WHERE user_id = ? AND product_id = ?",
            (user_id, product_id)
        )
        await db.commit()

    await callback.answer("Товар удалён из корзины.")
    await show_cart(callback.message, user_id, edit=True, callback_query=callback)

# --- Очистка корзины ---
@dp.callback_query(F.data == "clear_cart")
async def clear_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.answer("Корзина очищена.")
    await callback.message.edit_text("🗑 Корзина очищена.")

# --- Оформление заказа ---
@dp.callback_query(F.data == "checkout")
async def checkout_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM cart WHERE user_id = ?", (user_id,))
        count = await cursor.fetchone()
        if count[0] == 0:
            await callback.answer("Корзина пуста!")
            return

    await state.set_state(Order.contact)
    await callback.message.answer("Введите ваш контакт для обратной связи (телефон или @username):")
    await callback.answer()

@dp.message(Order.contact)
async def process_contact(message: Message, state: FSMContext):
    contact = message.text
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT p.name, c.quantity, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (user_id,))
        items = await cursor.fetchall()

        items_list = []
        total = 0.0
        for name, qty, price in items:
            items_list.append({"name": name, "quantity": qty, "price": price})
            total += price * qty

        items_json = json.dumps(items_list, ensure_ascii=False)

        await db.execute(
            "INSERT INTO orders (user_id, contact, items, total_price) VALUES (?, ?, ?, ?)",
            (user_id, contact, items_json, total)
        )
        await db.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        await db.commit()

    # Определяем получателя уведомления
    notify_chat_id = await get_notification_chat_id()
    items_text = "\n".join([f"  - {html.escape(item['name'])} × {item['quantity']} = {item['price']*item['quantity']:.2f} руб." for item in items_list])
    try:
        await bot.send_message(
            notify_chat_id,
            f"🛒 <b>Новый заказ</b> от @{html.escape(message.from_user.username or message.from_user.first_name)}\n"
            f"📞 Контакт: {html.escape(contact)}\n"
            f"📦 Товары:\n{items_text}\n"
            f"💰 Итого: {total:.2f} руб.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление получателю {notify_chat_id}: {e}")

    await state.clear()
    keyboard = get_main_keyboard(message.from_user.id)
    await message.answer("✅ Заказ оформлен! Мы свяжемся с вами в ближайшее время.", reply_markup=keyboard)

# --- Обработчики кнопок главного меню ---
@dp.message(F.text == "🛍 Меню товаров")
async def menu_button_handler(message: Message):
    await cmd_menu(message)

@dp.message(F.text == "🛒 Корзина")
async def cart_button_handler(message: Message):
    await cmd_cart(message)

@dp.message(F.text == "📞 Контакты")
async def contacts_button_handler(message: Message):
    contacts = await get_contacts()
    text = (
        "📞 <b>Наши контакты</b>\n"
        f"📞 Связь: {html.escape(contacts.get('contact_info', 'не указано'))}\n"
        f"🕒 Часы работы: {html.escape(contacts.get('work_hours', 'не указано'))}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🔧 Админ панель")
async def admin_panel_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав администратора.")
        return
    await show_admin_panel(message)

# --- Админ панель: основная ---
async def show_admin_panel(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name, price FROM products ORDER BY id")
        products = await cursor.fetchall()

    text = "🔧 <b>Админ-панель</b>\n\n"
    keyboard_buttons = []

    if products:
        text += "Список товаров:\n"
        for prod_id, name, price in products:
            safe_name = html.escape(name)
            text += f"🆔 {prod_id}. {safe_name} — {price:.2f} руб.\n"
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"✏️ Ред. {safe_name}", callback_data=f"edit_product_{prod_id}"),
                InlineKeyboardButton(text=f"❌ Удалить {safe_name}", callback_data=f"del_{prod_id}")
            ])
    else:
        text += "Товаров пока нет.\n"

    # Кнопки управления
    keyboard_buttons.append([InlineKeyboardButton(text="📝 Редактировать контакты", callback_data="edit_contacts")])
    keyboard_buttons.append([InlineKeyboardButton(text="📋 Активные заказы", callback_data="active_orders")])
    keyboard_buttons.append([InlineKeyboardButton(text="📊 Статистика", callback_data="statistics")])
    keyboard_buttons.append([InlineKeyboardButton(text="🔔 Настройка уведомлений", callback_data="notification_settings")])
    keyboard_buttons.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_addproduct")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

# --- Добавление товара из админки ---
@dp.callback_query(F.data == "admin_addproduct")
async def admin_addproduct_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.delete()
    await state.set_state(AddProduct.name)
    await callback.message.answer("Введите название товара:")
    await callback.answer()

# --- Удаление товара ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_product(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    product_id = int(callback.data.split("_")[1])
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_{product_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del")
            ]
        ]
    )
    await callback.message.edit_text(
        f"Вы уверены, что хотите удалить товар ID {product_id}?",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_del_"))
async def confirm_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    product_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()
    await callback.message.edit_text(f"✅ Товар ID {product_id} удалён.")
    await callback.answer()
    await show_admin_panel(callback.message)

@dp.callback_query(F.data == "cancel_del")
async def cancel_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.delete()
    await show_admin_panel(callback.message)
    await callback.answer()

# --- Редактирование товара ---
@dp.callback_query(F.data.startswith("edit_product_"))
async def edit_product_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(EditProduct.name)
    await callback.message.answer("Введите новое название товара (или отправьте 'пропустить'):")
    await callback.message.delete()
    await callback.answer()

@dp.message(EditProduct.name)
async def edit_product_name(message: Message, state: FSMContext):
    text = message.text
    if text.lower() != "пропустить":
        await state.update_data(name=text)
    await state.set_state(EditProduct.description)
    await message.answer("Введите новое описание (или 'пропустить'):")

@dp.message(EditProduct.description)
async def edit_product_description(message: Message, state: FSMContext):
    text = message.text
    if text.lower() != "пропустить":
        await state.update_data(description=text)
    await state.set_state(EditProduct.price)
    await message.answer("Введите новую цену (число, или 'пропустить'):")

@dp.message(EditProduct.price)
async def edit_product_price(message: Message, state: FSMContext):
    text = message.text
    if text.lower() != "пропустить":
        try:
            price = float(text)
            if price <= 0:
                raise ValueError
            await state.update_data(price=price)
        except ValueError:
            await message.answer("Пожалуйста, введите корректное положительное число или 'пропустить'.")
            return
    await state.set_state(EditProduct.photo)
    await message.answer("Отправьте новое фото (или отправьте 'пропустить', чтобы оставить старое):")

@dp.message(EditProduct.photo, F.photo)
async def edit_product_photo(message: Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo=photo_file_id)
    await finish_edit(message, state)

@dp.message(EditProduct.photo, F.text)
async def edit_product_photo_skip(message: Message, state: FSMContext):
    if message.text.lower() == "пропустить":
        await finish_edit(message, state, skip_photo=True)
    else:
        await message.answer("Пожалуйста, отправьте фото или напишите 'пропустить'.")

async def finish_edit(message: Message, state: FSMContext, skip_photo=False):
    data = await state.get_data()
    product_id = data.get('product_id')
    if not product_id:
        await message.answer("Ошибка: ID товара не найден.")
        await state.clear()
        return
    updates = []
    params = []
    if 'name' in data:
        updates.append("name = ?")
        params.append(data['name'])
    if 'description' in data:
        updates.append("description = ?")
        params.append(data['description'])
    if 'price' in data:
        updates.append("price = ?")
        params.append(data['price'])
    if 'photo' in data:
        updates.append("photo_file_id = ?")
        params.append(data['photo'])
    if not updates:
        await message.answer("Никаких изменений не внесено.")
        await state.clear()
        return
    params.append(product_id)
    query = f"UPDATE products SET {', '.join(updates)} WHERE id = ?"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()
    await state.clear()
    keyboard = get_main_keyboard(message.from_user.id)
    await message.answer("✅ Товар успешно обновлён!", reply_markup=keyboard)
    await show_admin_panel(message)

# --- Редактирование контактов ---
@dp.callback_query(F.data == "edit_contacts")
async def edit_contacts_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    contacts = await get_contacts()
    text = (
        "📝 <b>Редактирование контактов</b>\n\n"
        "Текущие значения:\n"
        f"📞 Связь: {html.escape(contacts.get('contact_info', ''))}\n"
        f"🕒 Часы работы: {html.escape(contacts.get('work_hours', ''))}\n\n"
        "Выберите, что хотите изменить:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Связь", callback_data="edit_field_contact_info")],
        [InlineKeyboardButton(text="🕒 Часы работы", callback_data="edit_field_work_hours")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_field_"))
async def edit_field_choice(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    field = callback.data.split("_")[2]  # contact_info или work_hours
    await state.update_data(field=field)
    await state.set_state(EditContacts.waiting_for_value)
    field_names = {"contact_info": "Связь", "work_hours": "Часы работы"}
    display_name = field_names.get(field, field)
    await callback.message.edit_text(f"Введите новое значение для поля '{display_name}':")
    await callback.answer()

@dp.message(EditContacts.waiting_for_value)
async def save_new_contact(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав администратора.")
        return
    data = await state.get_data()
    field = data['field']
    value = message.text
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            (value, field)
        )
        await db.commit()
    await state.clear()
    keyboard = get_main_keyboard(message.from_user.id)
    field_names = {"contact_info": "Связь", "work_hours": "Часы работы"}
    display_name = field_names.get(field, field)
    await message.answer(f"✅ Поле '{display_name}' обновлено!", reply_markup=keyboard)
    await show_admin_panel(message)

# --- Настройка уведомлений ---
@dp.callback_query(F.data == "notification_settings")
async def notification_settings(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    current = await get_notification_username()
    text = (
        "🔔 <b>Настройка уведомлений</b>\n\n"
        f"Текущий получатель: {current if current else 'администратор (по умолчанию)'}\n\n"
        "Вы можете указать username пользователя (например, @username или просто username),\n"
        "которому будут приходить уведомления о новых заказах.\n"
        "Этот пользователь должен хотя бы раз написать боту, чтобы он был зарегистрирован."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Указать username", callback_data="set_notification_user")],
        [InlineKeyboardButton(text="↩️ Сбросить на администратора", callback_data="reset_notification_user")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "set_notification_user")
async def set_notification_user_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(SetNotificationUser.waiting_for_username)
    await callback.message.edit_text("Введите username получателя уведомлений (можно с @ или без):")
    await callback.answer()

@dp.message(SetNotificationUser.waiting_for_username)
async def set_notification_user_finish(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав администратора.")
        return
    username = message.text.strip()
    # Проверим, есть ли пользователь с таким username в базе
    clean_username = username.lstrip('@')
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_id FROM users WHERE username = ?",
            (clean_username,)
        )
        row = await cursor.fetchone()
    if not row:
        await message.answer("Пользователь с таким username не найден в базе. Убедитесь, что он написал боту хотя бы раз.")
        return
    await set_notification_username(username)
    await state.clear()
    keyboard = get_main_keyboard(message.from_user.id)
    await message.answer(f"✅ Получатель уведомлений установлен: {username}", reply_markup=keyboard)
    await show_admin_panel(message)

@dp.callback_query(F.data == "reset_notification_user")
async def reset_notification_user(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    await set_notification_username("")
    await callback.message.edit_text("✅ Получатель уведомлений сброшен на администратора.")
    await callback.answer()
    await show_admin_panel(callback.message)

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.delete()
    await show_admin_panel(callback.message)
    await callback.answer()

# --- Активные заказы ---
@dp.callback_query(F.data == "active_orders")
async def show_active_orders(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT id, user_id, contact, total_price, created_at
            FROM orders
            WHERE status = 'new'
            ORDER BY created_at DESC
            LIMIT 10
        ''')
        orders = await cursor.fetchall()

    if not orders:
        await callback.message.edit_text("Нет активных заказов.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]]
        ))
        await callback.answer()
        return

    text = "📋 <b>Активные заказы (последние 10):</b>\n\n"
    keyboard_buttons = []
    for order in orders:
        order_id, user_id, contact, total, created = order
        short_contact = contact if len(contact) <= 20 else contact[:17] + "..."
        safe_short_contact = html.escape(short_contact)
        text += f"🆔 {order_id} | {safe_short_contact} | {total:.2f} руб. | {created[:10]}\n"
        keyboard_buttons.append([InlineKeyboardButton(
            text=f"🔍 Заказ #{order_id}",
            callback_data=f"order_details_{order_id}"
        )])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("order_details_"))
async def order_details(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    order_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT user_id, contact, items, total_price, created_at, status
            FROM orders WHERE id = ?
        ''', (order_id,))
        row = await cursor.fetchone()
    if not row:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    user_id, contact, items_json, total, created, status = row
    items = json.loads(items_json)
    items_text = "\n".join([f"  - {html.escape(item['name'])} × {item['quantity']} = {item['price']*item['quantity']:.2f} руб." for item in items])
    text = (
        f"📦 <b>Заказ #{order_id}</b>\n"
        f"📞 Контакт: {html.escape(contact)}\n"
        f"📅 Дата: {created}\n"
        f"📦 Товары:\n{items_text}\n"
        f"💰 Итого: {total:.2f} руб.\n"
        f"🔄 Статус: {status}\n"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить выполненным", callback_data=f"mark_done_{order_id}")],
        [InlineKeyboardButton(text="🔙 К списку заказов", callback_data="active_orders")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

# --- Отметка заказа выполненным с уведомлением пользователя ---
@dp.callback_query(F.data.startswith("mark_done_"))
async def mark_order_done(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return
    order_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
        if not row:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        user_id = row[0]
        await db.execute(
            "UPDATE orders SET status = 'completed' WHERE id = ?",
            (order_id,)
        )
        await db.commit()

    try:
        await bot.send_message(
            user_id,
            "✅ Ваш заказ отправлен! Спасибо за покупку."
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    await callback.answer("Заказ отмечен как выполненный, пользователь уведомлен!", show_alert=True)
    await show_active_orders(callback)

# --- Статистика ---
@dp.callback_query(F.data == "statistics")
async def show_statistics(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM orders")
        total_orders = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT SUM(total_price) FROM orders")
        total_revenue = (await cursor.fetchone())[0] or 0.0
        cursor = await db.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
        completed_orders = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'")
        active_orders = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM products")
        products_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]

    text = (
        "📊 <b>Статистика магазина</b>\n\n"
        f"📦 Всего заказов: {total_orders}\n"
        f"💰 Общая выручка: {total_revenue:.2f} руб.\n"
        f"✅ Выполненных заказов: {completed_orders}\n"
        f"🕒 Активных заказов: {active_orders}\n"
        f"🛍 Товаров в каталоге: {products_count}\n"
        f"👥 Зарегистрированных пользователей: {users_count}\n"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

# --- Команда /admin ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав администратора.")
        return
    await show_admin_panel(message)

# --- Запуск бота ---
async def main():
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, is_admin) VALUES (?, 1)",
            (ADMIN_ID,)
        )
        await db.commit()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())