import requests
import json
import datetime
import uuid
import qrcode
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from SDK import CryptoCloudSDK
import aiosqlite
import asyncio
import logging
import nest_asyncio

nest_asyncio.apply()

# Функция для загрузки конфигурации из JSON
def load_config(file_path):
    with open(file_path, "r") as config_file:
        config = json.load(config_file)
    return config

config = load_config("config.json")
api_key = config.get("api_pay")
sdk = CryptoCloudSDK(api_key)
shop_id = config.get("shop_id")
tg_key = config.get("api_tg")
xui_port = config.get("3xui_port")
xui_ip = config.get("3xui_ip")
xui_patch = config.get("3xui_patch")
xui_pass = config.get("3xui_pass")
xui_login = config.get("3xui_login")

#Выставление счета
async def pay(amount, user_id):
    
# Пример данных для создания счета
    invoice_data = {
        "amount": int(amount),
        "shop_id": shop_id,
        "currency": "USD",
        "order_id": user_id,
        "add_fields": {
            "time_to_pay": {"minutes": 30},
            "available_currencies": ["ETH", "BTC", "TON", "USDT_TRC20, USDT_TON"],
        }
    }

    # Создание счета
    response = sdk.create_invoice(invoice_data)
    print("Invoice Created:", response)

    #Подключяемся к БД и устанавливаем пользователя в режим ожидания оплаты
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("UPDATE transactions SET status_pay = 1 WHERE user_id = ?", (user_id,))
    
        # Получаем invoice_uuid из ответа
        invoice_uuid = response['result']['uuid']  # Важно знать, что именно API возвращает в поле uuid
        invoice_link = response['result']['link']  # Ссылка на оплату
        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", (invoice_uuid, user_id))

        await cursor.execute("UPDATE transactions SET amount = ? WHERE user_id = ?", (amount, user_id))
        
        await conn.commit()
    print(f"Invoice UUID: {invoice_uuid}")
    return invoice_link

async def clear_pay(user_id, update):
    async with aiosqlite.connect("bot_database.db") as conn:
            cursor = await conn.cursor()
            async with conn.cursor() as cursor:
            # Читаем номер платежа
                await cursor.execute("SELECT invoice_uuid FROM transactions WHERE user_id = ?", (user_id,))
                result = await cursor.fetchone()
                invoice_uuid = result[0] if result else None  # Проверяем, есть ли данные
            if not invoice_uuid:
                await update.message.reply_text("Нет активного платежа для отмены.")
                return
            response = sdk.cancel_invoice(invoice_uuid)

            if response.get("status") == "success":
                # Если статус "success", проверяем результат
                if response.get("result") == ["ok"]:
                    await update.message.reply_text("Платеж успешно отменен.")
                    # Дополнительные действия по очистке транзакции в базе данных
                    async with conn.cursor() as cursor:
                        await cursor.execute("UPDATE transactions SET invoice_uuid = 'NONE' WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await conn.commit()
                else:
                    # Если результат не "ok", выводим ошибку
                    await update.message.reply_text(f"Ошибка при отмене платежа: {response.get('result')}")
            elif response.get("status") == "error":
                # Если статус "error", выводим сообщение об ошибке
                error_message = response.get("result", {}).get("validate_error", "Неизвестная ошибка.")
                await update.message.reply_text(f"Ошибка отмены: {error_message}")
            else:
                # Если статус ответа не success и не error
                await update.message.reply_text(f"Неизвестный статус ответа: {response}")

# Обработчик команды /cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await clear_pay(user_id, update)
            
async def check_pay(update, user_id, amount, conn):
            cursor = await conn.cursor()
            
            # Проверяем, есть ли запись о транзакции
            await cursor.execute("SELECT invoice_uuid FROM transactions WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            
            if row:
                invoice_uuid = row[0]
            else:
                await update.message.reply_text("Ошибка: Не найдено платежное поручение для этого пользователя, обратитесь в тех поддержку.")
                return
            try:
                # Получаем информацию о платеже
                response = sdk.get_invoice_info([invoice_uuid])

                # Проверяем статус ответа и наличие результата
                if response.get("status") == "success" and "result" in response and len(response["result"]) > 0:
                    # Получаем первый элемент из списка 'result'
                    invoice_info = response["result"][0]
                        
                    # Получаем статус счета из результата
                    invoice_status = invoice_info.get("status")
                        
                    # Печатаем статус платежа
                    if invoice_status == "paid":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("Успешный платеж, проверьте баланс командой /balance")
                        print(f"Статус счета: {invoice_status}")
                        await conn.commit()
                    elif invoice_status == "overpaid":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("Платеж был переплачен, проверьте баланс командой /balance")
                        print(f"Статус счета: {invoice_status}")
                        await conn.commit()
                    elif invoice_status == "canceled":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("Платеж был отменен по истечении времени")
                        print(f"Статус счета: {invoice_status}")
                        await conn.commit()
                        print("Платеж был отменен.")
                    elif invoice_status == "created":
                        await update.message.reply_text("Платеж создан, оплатите платеж до истечения времени!")
                    else:
                        print(f"Статус счета: {invoice_status}")
                        print(response)
                else:
                    print(f"Ошибка при получении информации о платеже. Ответ: {response}")
            except Exception as e:
                print(f"Ошибка при запросе: {e}")

# Обработчик команды /check
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        async with conn.cursor() as cursor:
            # Проверяем сумму платежа
            await cursor.execute("SELECT amount FROM transactions WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
            amount = result[0] if result else None  # Проверяем, есть ли данные
            
            # Проверяем статус платежа
            await cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
            result2 = await cursor.fetchone()
            status_pay = result2[0] if result2 else None  # Проверяем, есть ли данные
   
        if status_pay == 1:
            async with aiosqlite.connect("bot_database.db") as conn:  # Открываем новое соединение
                await check_pay(update, user_id, amount, conn)
        else:
            await update.message.reply_text("Нет созданных платежей")
        
# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
    
        # Таблица пользователей
        await cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица транзакций
        await cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                user_id INTEGER PRIMARY KEY,
                amount INTEGER,
                status_pay INTEGER DEFAULT 0,
                invoice_uuid TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица VPN-аккаунтов
        await cursor.execute('''
            CREATE TABLE IF NOT EXISTS vpn_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                uuid TEXT,
                email TEXT,
                expiry_time INTEGER,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        await conn.commit()

    async with aiosqlite.connect("user_states.db") as conn:
        cursor = await conn.cursor()

        # Создаем таблицу, состояний пользователя
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT DEFAULT 'None'
        )
        """)
        await conn.commit()

# Функция авторизации в панели VPN
def auth():
    login_url = f"http://{xui_ip}:{xui_port}{xui_patch}login"
    login_payload = {"username": xui_login, "password": xui_pass}
    login_headers = {'Accept': 'application/json;'}
    session = requests.Session()
    login_response = session.post(login_url, data=login_payload, headers=login_headers)
    cookies = session.cookies.get_dict()
    return cookies if '3x-ui' in cookies else None

# Функция добавления клиента
def add_client(days, user_email, cookies):
    url = f"http://{xui_ip}:{xui_port}{xui_patch}panel/api/inbounds/addClient"
    expiry_time = int((datetime.datetime.now() + datetime.timedelta(days=days)).timestamp() * 1000)
    user_uuid = str(uuid.uuid4())
    payload = {
        "id": 1,
        "settings": json.dumps({
            "clients": [{
                "id": user_uuid,
                "flow": "xtls-rprx-vision",
                "email": user_email,
                "limitIp": 3,
                "totalGB": 33000000000,
                "expiryTime": expiry_time,
                "enable": True
            }]
        })
    }
    headers = {'Accept': 'application/json', 'Cookie': f"3x-ui={cookies['3x-ui']}"}
    requests.post(url, headers=headers, json=payload)
    return user_uuid, expiry_time, payload

# Генерация QR-кода
def generate_qr(data, filename):
    qr = qrcode.make(data)
    qr.save(filename)
    return filename

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await conn.commit()
    message = (
        "<b>Привет!</b> 👋\n\n"
        "Ты успешно зарегистрирован в нашем боте. "
        "Теперь ты можешь использовать все доступные команды:\n\n"
        "<u>• /balance</u> — Показывает твой <i>баланс</i> 💰\n"
        "<u>• /create_client</u> — Создает нового <i>клиента</i> 🧑‍💼\n"
        "<u>• /buy</u> — Покупает товар 🛒\n"
        "<u>• /help</u> — Получить <i>помощь</i> ❓\n"
        "<u>• /cancel</u> — Отменяет <i>платеж</i> ❌\n\n"
        "Приятного использования! 😊"
    )
    await update.message.reply_text(message, parse_mode = "HTML")

# Обработчик команды /help
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = (
        "<b>Доступные команды:</b>👋\n\n"
        "<u>• /balance</u> — Показывает твой <i>баланс</i> 💰\n"
        "<u>• /create_client</u> — Создает нового <i>клиента</i> 🧑‍💼\n"
        "<u>• /buy</u> — Покупает товар 🛒\n"
        "<u>• /help</u> — Получить <i>помощь</i> ❓\n"
        "<u>• /cancel</u> — Отменяет <i>платеж</i> ❌\n\n"
        "Приятного использования! 😊"
    )
    await update.message.reply_text(message, parse_mode = "HTML")

# Обработчик команды /balance
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result:
            balance = result[0]
        else:
            balance = 0
        await update.message.reply_text(f"Твой баланс: {balance} USD.")
    
#Запрос состояния пользователя из БД
async def get_user_state(user_id):
    """Получаем состояние пользователя из базы данных."""
    async with aiosqlite.connect("user_states.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT state FROM user_states WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    return result[0] if result else None

#Изменение состояния пользователя в БД
async def set_user_state(user_id, state):
    """Устанавливаем состояние пользователя в базе данных."""
    async with aiosqlite.connect("user_states.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("REPLACE INTO user_states (user_id, state) VALUES (?, ?)", (user_id, state))
        await conn.commit()

# Обработчик покупки
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на пополнение, если статус 0."""
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await cursor.execute("INSERT OR IGNORE INTO transactions (user_id) VALUES (?)", (user_id,))
        await cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        await conn.commit()

    if result and result[0] == 0:
        await set_user_state(user_id, "awaiting_confirmation")  # <-- УСТАНАВЛИВАЕМ ОЖИДАНИЕ "ДА"/"НЕТ"
        keyboard = [["Да", "Нет"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text("Желаете пополнить аккаунт?", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Ожидается выполнение платежа.")


async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ответ (Да/Нет) и при 'Да' запрашивает сумму."""
    text = update.message.text
    user_id = update.message.from_user.id
    state = await get_user_state(user_id)
    print(f"[handle_response] Пользователь {user_id} выбрал: {text}")  # Логируем действие

    if state == "awaiting_confirmation":  # Проверяем, что бот ждал "Да"/"Нет"
        if text == "Да":
            await set_user_state(user_id, "awaiting_amount")  # Запоминаем, что ждем сумму
            await update.message.reply_text("Введите сумму для пополнения:", reply_markup=ReplyKeyboardRemove())
        elif text == "Нет":
            await set_user_state(user_id, None)  # Сбрасываем состояние
            await update.message.reply_text("Хорошо, обращайтесь, когда будете готовы.", reply_markup=ReplyKeyboardRemove())

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод суммы, если бот ждет ее."""
    user_id = update.message.from_user.id
    # Получаем текущее состояние пользователя из базы данных
    state = await get_user_state(user_id)

    print(f"[handle_amount] Пользователь {user_id} вводит сумму, текущее состояние: {state}")  # Логируем

    if state == "awaiting_amount":
        try:
            amount = float(update.message.text)  # Преобразуем в число
            await set_user_state(user_id, None)  # Сбрасываем состояние
            message = (
                f"<b>Вы ввели сумму {amount} USD. Обрабатываю платеж...</b>👋\n\n"
                "<u>• /check</u> — Проверить<i> платеж</i> 💰\n"
                "<u>• /cancel</u> — Отменяет <i> платеж</i> ❌\n\n"
                "ВНИМАНИЕ! После выполнения платежа и подтверждения оплаты от сервиса в окне оплаты, <b>необходимо</b> проверить платеж командой! 😊"
            )
            await update.message.reply_text(message, parse_mode = "HTML")

            # код для записи суммы в БД и логики оплаты
            invoice_link = await pay(amount, user_id)
            await update.message.reply_text(f"Ваша ссылка на оплату: {invoice_link}")
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите корректное число.")
    
def vless_get(inbound, user_uuid, user_email, cookies):
    EXTERNAL_IP = xui_ip  
    FLOW = "flow=xtls-rprx-vision"  
    SERVER_PORT = 443  
    url = f"http://{xui_ip}:{xui_port}{xui_patch}panel/api/inbounds/get/1"

    payload = {}
    headers = {
      'Accept': 'application/json'
    }

    response = requests.request("GET", url, headers=headers, cookies=cookies)

    inbound = response.json()

    settings = inbound["obj"]["streamSettings"]
    reality_settings = json.loads(settings)["realitySettings"]  # Десериализуем строку в JSON (если она строка)

    public_key = reality_settings["settings"]["publicKey"]
    short_id = reality_settings["shortIds"][0]  # первый shortId
    server_name = reality_settings["serverNames"][0]

    connection_string = (
            f"vless://{user_uuid}@{EXTERNAL_IP}:{SERVER_PORT}"
            f"?type=tcp&security=reality&pbk={public_key}&fp=chrome&sni={server_name}"
            f"&sid={short_id}&spx=%2F&{FLOW}#{user_email}"
        )

    print(connection_string)
    return connection_string

# Обработчик создания VPN-аккаунта
async def create_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result:
            balance = result[0]
        else:
            balance = 0
        if balance < 1:
            await update.message.reply_text("❌ Недостаточно средств (нужно 1 USD). Пополните баланс.")
            return
        
        # Вычитаем деньги и создаем VPN
        await cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id = ?", (user_id,))
        cookies = auth()
        if not cookies:
            await update.message.reply_text("Ошибка авторизации в панели VPN.")
            return
    
        user_email = f"user_{user_id}@"+str(uuid.uuid4())[:8]
        user_uuid, expiry_time, payload = add_client(30, user_email, cookies)
        
        # Сохраняем в БД
        await cursor.execute("INSERT INTO vpn_accounts (user_id, uuid, email, expiry_time) VALUES (?, ?, ?, ?)",
                       (user_id, user_uuid, user_email, expiry_time))
        await conn.commit()
        
        # Генерация ссылки
        connection_string = vless_get(1, user_uuid, user_email, cookies)
        qr_file = generate_qr(connection_string, f"{user_id}.png")
        
        await update.message.reply_text(f"Твой VPN создан! Подключение: {connection_string}")
        await update.message.reply_photo(qr_file, "Твой QR-код!")
    
asyncio.run(init_db())

# Запуск бота
async def main():
    app = Application.builder().token(tg_key).build()

    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(MessageHandler(filters.Regex("^(Да|Нет)$"), handle_response))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("create_client", create_client))
    app.add_handler(CommandHandler("check", check))

    print("Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(main())  # Создаём задачу для main, чтобы запустить бота
        loop.run_forever()  # Запускаем event loop
        #Завершение через ^C
    except KeyboardInterrupt:
        print("Остановка бота...")
    finally:
        # Дополнительная очистка или логирование
        print("Завершаем работу...")
        loop.stop()  # Останавливаем event loop
        loop.close()  # Закрываем event loop     
    
