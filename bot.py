import os
import io
import sqlite3
import logging
import shutil
import zipfile
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes, ConversationHandler
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

# Определяем состояния:
# 0: GET_FIRST_NAME, 1: GET_LAST_NAME, 2: GET_CLASS, 3: ADD_CLASS,
# 4: ADD_ADMIN_USERNAME, 5: ADD_ADMIN_ACCESS, 6: UPLOAD_SCREENSHOT
GET_FIRST_NAME, GET_LAST_NAME, GET_CLASS, ADD_CLASS, ADD_ADMIN_USERNAME, ADD_ADMIN_ACCESS, UPLOAD_SCREENSHOT = range(7)

# MAIN_ADMINS согласно требованиям
MAIN_ADMINS = {6897531034, 6176677671, 1552916570, 1040487188, 1380600483 , 7176188474}

PHOTOS_DIR = "photos"
os.makedirs(PHOTOS_DIR, exist_ok=True)
TEMP_ZIP_DIR = "temp_zip"
os.makedirs(TEMP_ZIP_DIR, exist_ok=True)

async def delete_file_after_delay(file_path: str, delay: int):
    await asyncio.sleep(delay)
    try:
        os.remove(file_path)
        logging.info(f"Файл {file_path} удалён после задержки.")
    except Exception as e:
        logging.error(f"Ошибка удаления файла {file_path}: {e}")

def init_db():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            first_name TEXT,
            last_name TEXT,
            class TEXT,
            username TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            class_access TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_path TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ===== РЕГИСТРАЦИЯ / ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM students WHERE user_id = ?", (user_id,))
    student = cursor.fetchone()
    conn.close()
    if student:
        await update.message.reply_text("Вы уже зарегистрированы. Вот ваше меню:")
        await student_menu(update, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text("Введите ваше имя:")
        return GET_FIRST_NAME

async def get_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data['first_name'] = update.message.text.strip()
    await update.message.delete()
    await context.bot.send_message(chat_id, "Введите вашу фамилию:")
    return GET_LAST_NAME

async def get_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data['last_name'] = update.message.text.strip()
    await update.message.delete()
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM classes")
    classes = [row[0] for row in cursor.fetchall()]
    conn.close()
    if not classes:
        await context.bot.send_message(chat_id, "Нет доступных классов. Обратитесь к администратору.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(cls, callback_data=cls)] for cls in classes]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, "Выберите ваш класс:", reply_markup=reply_markup)
    return GET_CLASS

async def get_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    class_name = query.data
    user_id = query.from_user.id
    first_name = context.user_data.get('first_name')
    last_name = context.user_data.get('last_name')
    username = query.from_user.username if query.from_user.username else ""
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO students (user_id, first_name, last_name, class, username)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, first_name, last_name, class_name, username))
    conn.commit()
    conn.close()
    await query.message.delete()
    await query.message.reply_text(f"Спасибо, {first_name} {last_name}! Вы зарегистрированы в классе {class_name}.")
    await student_menu(update, context)
    return ConversationHandler.END

# ===== АДМИН ФУНКЦИОНАЛ =====

async def sql_all_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    admin_entry = cursor.fetchone()
    conn.close()
    if user_id not in MAIN_ADMINS and not admin_entry:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM classes")
    classes = [row[0] for row in cursor.fetchall()]
    conn.close()
    # Компоновка клавиатуры: по 2 кнопки в строке
    keyboard = []
    for i in range(0, len(classes), 2):
        row = []
        row.append(InlineKeyboardButton(classes[i], callback_data=f"class_{classes[i]}"))
        if i+1 < len(classes):
            row.append(InlineKeyboardButton(classes[i+1], callback_data=f"class_{classes[i+1]}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("➕ Добавить класс", callback_data="add_class")])
    keyboard.append([InlineKeyboardButton("👤 Управление администраторами", callback_data="manage_admins")])
    keyboard.append([InlineKeyboardButton("📥 Скачать все фотографии", callback_data="download_all_photos")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

async def admin_add_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите название нового класса:")
    return ADD_CLASS

async def save_new_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    new_class = update.message.text.strip()
    await update.message.delete()
    if not new_class:
        await context.bot.send_message(chat_id, "Название класса не может быть пустым. Попробуйте ещё раз:")
        return ADD_CLASS
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO classes (name) VALUES (?)", (new_class,))
        conn.commit()
        os.makedirs(os.path.join(PHOTOS_DIR, new_class), exist_ok=True)
        await context.bot.send_message(chat_id, f"✅ Класс '{new_class}' успешно добавлен!")
    except sqlite3.IntegrityError:
        await context.bot.send_message(chat_id, f"⚠️ Класс '{new_class}' уже существует!")
    except sqlite3.Error as e:
        logging.error(f"Ошибка при добавлении класса: {e}")
        await context.bot.send_message(chat_id, "❌ Ошибка базы данных. Попробуйте позже.")
    finally:
        conn.close()
    return ConversationHandler.END

async def manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    keyboard = [
        [InlineKeyboardButton("➕ Добавить администратора", callback_data="add_admin")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text("Управление администраторами:", reply_markup=reply_markup)

async def admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите @username нового администратора:")
    return ADD_ADMIN_USERNAME

async def save_admin_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username_input = update.message.text.strip()
    if not username_input.startswith('@'):
        await update.message.reply_text("Неверный формат. Введите @username (например, @example):")
        return ADD_ADMIN_USERNAME
    try:
        chat = await context.bot.get_chat(username_input)
    except Exception as e:
        await update.message.reply_text("Не удалось получить информацию о пользователе. Убедитесь, что пользователь взаимодействовал с ботом.")
        return ADD_ADMIN_USERNAME
    admin_id = chat.id
    context.user_data['new_admin_username'] = username_input
    context.user_data['new_admin_id'] = admin_id
    await update.message.delete()
    await context.bot.send_message(chat_id, "Введите классы, к которым дать доступ (через запятую, или 'all' для доступа ко всем классам):")
    return ADD_ADMIN_ACCESS

async def save_admin_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    access_input = update.message.text.strip()
    await update.message.delete()
    if access_input.lower() == 'all':
        class_access = "all"
    else:
        classes = [cls.strip() for cls in access_input.split(',') if cls.strip()]
        class_access = ",".join(classes)
    admin_id = context.user_data.get('new_admin_id')
    username_input = context.user_data.get('new_admin_username')
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO admins (user_id, username, class_access) VALUES (?, ?, ?)",
                       (admin_id, username_input, class_access))
        conn.commit()
        await context.bot.send_message(chat_id, f"✅ Администратор {username_input} с ID {admin_id} успешно добавлен с доступом: {class_access}")
    except Exception as e:
        logging.error(f"Ошибка при добавлении администратора: {e}")
        await context.bot.send_message(chat_id, "❌ Ошибка при добавлении администратора.")
    finally:
        conn.close()
    MAIN_ADMINS.add(admin_id)
    return ConversationHandler.END

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM classes")
    classes = [row[0] for row in cursor.fetchall()]
    conn.close()
    keyboard = []
    for i in range(0, len(classes), 2):
        row = []
        row.append(InlineKeyboardButton(classes[i], callback_data=f"class_{classes[i]}"))
        if i+1 < len(classes):
            row.append(InlineKeyboardButton(classes[i+1], callback_data=f"class_{classes[i+1]}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("➕ Добавить класс", callback_data="add_class")])
    keyboard.append([InlineKeyboardButton("👤 Управление администраторами", callback_data="manage_admins")])
    keyboard.append([InlineKeyboardButton("📥 Скачать все фотографии", callback_data="download_all_photos")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Выберите действие:", reply_markup=reply_markup)

async def show_class_students(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if not data.startswith("class_"):
        await query.answer("Некорректные данные.", show_alert=True)
        return
    class_name = data[6:]
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.first_name, s.last_name,
               (SELECT MAX(timestamp) FROM screenshots WHERE user_id = s.user_id) as last_upload
        FROM students s
        WHERE s.class = ?
    """, (class_name,))
    students = cursor.fetchall()
    conn.close()
    if not students:
        await query.answer("В этом классе нет учеников.", show_alert=True)
        return
    keyboard = []
    for sid, fn, ln, last_upload in students:
        if last_upload is None:
            last_upload = "Нет данных"
        keyboard.append([InlineKeyboardButton(f"{fn} {ln} (послед. скрин: {last_upload})", callback_data=f"student_{sid}")])
    keyboard.append([InlineKeyboardButton("📥 Скачать все скриншоты", callback_data=f"download_class_{class_name}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Список учеников класса {class_name}:", reply_markup=reply_markup)

async def show_student_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    try:
        student_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректные данные.", show_alert=True)
        return
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT first_name, last_name, class, username, user_id FROM students WHERE id = ?", (student_id,))
    student = cursor.fetchone()
    conn.close()
    if not student:
        await query.answer("Студент не найден.", show_alert=True)
        return
    first_name, last_name, class_name, username, student_user_id = student
    profile_text = (f"Имя: {first_name}\nФамилия: {last_name}\nКласс: {class_name}\nТелеграм: @{username}"
                    if username else
                    f"Имя: {first_name}\nФамилия: {last_name}\nКласс: {class_name}\nТелеграм: Не указан")
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, file_path, timestamp FROM screenshots WHERE user_id = ?", (student_user_id,))
    screenshots = cursor.fetchall()
    conn.close()
    keyboard = []
    for i, (sc_id, _, timestamp) in enumerate(screenshots, start=1):
        keyboard.append([InlineKeyboardButton(f"Скрин {i} ({timestamp})", callback_data=f"view_screenshot_{sc_id}")])
    if screenshots:
        keyboard.append([InlineKeyboardButton("📥 Скачать все скриншоты", callback_data=f"download_student_{student_user_id}")])
    # Новая кнопка для удаления ученика
    keyboard.append([InlineKeyboardButton("❌ Удалить ученика", callback_data=f"delete_student_{student_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"class_{class_name}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(profile_text, reply_markup=reply_markup)

async def view_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        sc_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer("Некорректные данные.", show_alert=True)
        return
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM screenshots WHERE id = ?", (sc_id,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        await query.answer("Скриншот не найден.", show_alert=True)
        return
    file_path = result[0]
    await query.answer()
    await context.bot.send_photo(query.message.chat_id, photo=open(file_path, 'rb'))

async def delete_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        student_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer("Некорректные данные.", show_alert=True)
        return
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    # Получаем класс ученика для обновления списка после удаления
    cursor.execute("SELECT class FROM students WHERE id = ?", (student_id,))
    student = cursor.fetchone()
    if not student:
        await query.answer("Ученик не найден.", show_alert=True)
        conn.close()
        return
    student_class = student[0]
    # Получаем пути к файлам скриншотов ученика
    cursor.execute("SELECT file_path FROM screenshots WHERE user_id = (SELECT user_id FROM students WHERE id = ?)", (student_id,))
    screenshot_files = cursor.fetchall()
    # Удаляем скриншоты из базы
    cursor.execute("DELETE FROM screenshots WHERE user_id = (SELECT user_id FROM students WHERE id = ?)", (student_id,))
    # Удаляем ученика
    cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()
    # Удаляем файлы скриншотов с диска
    for (file_path,) in screenshot_files:
        try:
            os.remove(file_path)
        except Exception as e:
            logging.error(f"Ошибка удаления файла {file_path}: {e}")
    await query.message.reply_text("✅ Ученик и все его скриншоты удалены.")
    # Обновляем список учеников для класса
    await show_class_students(update, context)
async def download_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        student_user_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer("Некорректные данные.", show_alert=True)
        return
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM screenshots WHERE user_id = ?", (student_user_id,))
    files = [row[0] for row in cursor.fetchall()]
    conn.close()
    if not files:
        await query.answer("Нет скриншотов для скачивания.", show_alert=True)
        return
    zip_file = os.path.join(TEMP_ZIP_DIR, f"student_{student_user_id}_screenshots.zip")
    with zipfile.ZipFile(zip_file, 'w') as zf:
        for file in files:
            zf.write(file, arcname=os.path.basename(file))
    await query.message.reply_document(document=open(zip_file, 'rb'),
                                       filename=f"student_{student_user_id}_screenshots.zip")
    await query.answer("Архив отправлен.")
    asyncio.create_task(delete_file_after_delay(zip_file, 300))
async def download_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    class_name = query.data.split("_", 1)[1]
    class_folder = os.path.join(PHOTOS_DIR, class_name)
    if not os.path.isdir(class_folder):
        await query.answer("Нет фотографий для данного класса.", show_alert=True)
        return
    base_name = os.path.join(TEMP_ZIP_DIR, f"{class_name}_screenshots")
    shutil.make_archive(base_name, 'zip', class_folder)
    zip_file = base_name + ".zip"
    await query.message.reply_document(document=open(zip_file, 'rb'),
                                       filename=f"{class_name}_screenshots.zip")
    await query.answer("Архив отправлен.")
    asyncio.create_task(delete_file_after_delay(zip_file, 300))
async def download_all_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    base_name = os.path.join(TEMP_ZIP_DIR, "all_photos")
    shutil.make_archive(base_name, 'zip', PHOTOS_DIR)
    zip_file = base_name + ".zip"
    await query.message.reply_document(document=open(zip_file, 'rb'),
                                       filename="all_photos.zip")
    await query.answer("Архив отправлен.")
    asyncio.create_task(delete_file_after_delay(zip_file, 300))
async def student_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Задания MODO", callback_data="modo_tasks")],
        [InlineKeyboardButton("📂 Мои скриншоты", callback_data="my_screenshots")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "Выберите действие:", reply_markup=reply_markup)
async def modo_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    keyboard = [
        [InlineKeyboardButton("Перейти к заданиям", url="https://class-kz.ru/ucheniku/modo-4-klass/")],
        [InlineKeyboardButton("✅ Я прошел тест", callback_data="upload_screenshot")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(query.message.chat_id, "Выберите действие:", reply_markup=reply_markup)
async def upload_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await context.bot.send_message(query.message.chat_id, "Пришлите скриншот с результатами теста.")
    return UPLOAD_SCREENSHOT
async def save_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    photo = update.message.photo[-1]
    file_id = photo.file_id
    file = await context.bot.get_file(file_id)
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT class FROM students WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    class_name = result[0] if result else "unknown"
    class_folder = os.path.join(PHOTOS_DIR, class_name)
    os.makedirs(class_folder, exist_ok=True)
    file_path = os.path.join(class_folder, f"screenshot_{user_id}_{file_id}.jpg")
    upload_timestamp = datetime.now(ZoneInfo("Asia/Almaty")).strftime("%Y-%m-%d %H:%M")
    await file.download_to_drive(file_path)
    await update.message.delete()
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO screenshots (user_id, file_path, timestamp) VALUES (?, ?, ?)",
                   (user_id, file_path, upload_timestamp))
    conn.commit()
    conn.close()
    await context.bot.send_message(chat_id, f"✅ Скриншот сохранен! (Дата и время: {upload_timestamp})\nВы можете просмотреть его в своем профиле.")
    return ConversationHandler.END
async def my_screenshots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM screenshots WHERE user_id = ?", (user_id,))
    screenshots = cursor.fetchall()
    conn.close()
    await query.message.delete()
    if not screenshots:
        await context.bot.send_message(chat_id, "У вас нет загруженных скриншотов.")
        return
    await context.bot.send_message(chat_id, "📂 Ваши загруженные скриншоты:")
    for (path,) in screenshots:
        with open(path, 'rb') as photo_file:
            await context.bot.send_photo(chat_id, photo=photo_file)
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, "Вернуться в меню", reply_markup=reply_markup)
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await student_menu(update, context)
def main():
    application = ApplicationBuilder().token("7147486797:AAGqeja-HW0NkuvjnUfS35GoUuqgiqlHoOM").build()
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            GET_FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_first_name)],
            GET_LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_last_name)],
            GET_CLASS: [CallbackQueryHandler(get_class)]
        },
        fallbacks=[]
    )
    admin_class_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_class, pattern='^add_class$')],
        states={
            ADD_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_class)]
        },
        fallbacks=[]
    )
    admin_admin_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_admin, pattern='^add_admin$')],
        states={
            ADD_ADMIN_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_admin_username)],
            ADD_ADMIN_ACCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_admin_access)]
        },
        fallbacks=[]
    )
    screenshot_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_screenshot, pattern='^upload_screenshot$')],
        states={
            UPLOAD_SCREENSHOT: [MessageHandler(filters.PHOTO, save_screenshot)]
        },
        fallbacks=[]
    )
    application.add_handler(registration_handler)
    application.add_handler(CommandHandler("sqlallget", sql_all_get))
    application.add_handler(admin_class_handler)
    application.add_handler(admin_admin_handler)
    application.add_handler(CallbackQueryHandler(manage_admins, pattern='^manage_admins$'))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
    application.add_handler(CallbackQueryHandler(show_class_students, pattern='^class_'))
    application.add_handler(CallbackQueryHandler(show_student_profile, pattern='^student_'))
    application.add_handler(CallbackQueryHandler(view_screenshot, pattern='^view_screenshot_'))
    application.add_handler(CallbackQueryHandler(download_student, pattern='^download_student_'))
    application.add_handler(CallbackQueryHandler(download_class, pattern='^download_class_'))
    application.add_handler(CallbackQueryHandler(download_all_photos, pattern='^download_all_photos$'))
    application.add_handler(CommandHandler("menu", student_menu))
    application.add_handler(CallbackQueryHandler(modo_tasks, pattern='^modo_tasks$'))
    application.add_handler(CallbackQueryHandler(my_screenshots, pattern='^my_screenshots$'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='^back_to_menu$'))
    application.add_handler(CallbackQueryHandler(delete_student, pattern='^delete_student_'))
    application.add_handler(screenshot_handler)
    application.run_polling()
if __name__ == '__main__':
    main()
