import sqlite3
import requests
import json
import datetime
import uuid
import qrcode
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from SDK import CryptoCloudSDK


api_key = "ВАШ_API_KEY_ОТ_МАГАЗИНА"
sdk = CryptoCloudSDK(api_key)
shop_id = "ВАШ_SHOP_ID"

#Выставление счета
async def pay(amount, user_id):
    
# Пример данных для создания счета
    invoice_data = {
        "amount": int(amount),
        "shop_id": shop_id,
        "currency": "USD",
        "order_id": user_id,
        "add_fields": {
            "time_to_pay": {"minutes": 1},
            "email_to_send": "customer@example.com",
            "available_currencies": ["ETH", "BTC"],
            #"period": "day",
        }
    }

    # Создание счета
    response = sdk.create_invoice(invoice_data)
    print("Invoice Created:", response)

    #Подключяемся к БД и устанавливаем пользователя в режим ожидания оплаты
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE transactions SET status_pay = 1 WHERE user_id = ?", (user_id,))
    
    # Получаем invoice_uuid из ответа
    invoice_uuid = response['result']['uuid']  # Важно знать, что именно API возвращает в поле uuid
    invoice_link = response['result']['link']  # Ссылка на оплату
    cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", (invoice_uuid, user_id))

    cursor.execute("UPDATE transactions SET amount = ? WHERE user_id = ?", (amount, user_id))
    
    conn.commit()
    conn.close()
    print(f"Invoice UUID: {invoice_uuid}")
    return invoice_link

async def check_pay(update, user_id, amount):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT invoice_uuid FROM transactions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    invoice_uuid = row[0]
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
                cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                await update.message.reply_text("Успешный платеж, проверьте баланс командой /balance")
                print(f"Статус счета: {invoice_status}")
                conn.commit()
            elif invoice_status == "overpaid":
                cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                await update.message.reply_text("Платеж был переплачен, проверьте баланс командой /balance")
                print(f"Статус счета: {invoice_status}")
                conn.commit()
            elif invoice_status == "canceled":
                cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                await update.message.reply_text("Платеж был отменен по истечении времени")
                print(f"Статус счета: {invoice_status}")
                conn.commit()
                print("Платеж был отменен.")
            else:
                print(f"Статус счета: {invoice_status}")
                print(response)
                conn.close()
        else:
            print(f"Ошибка при получении информации о платеже. Ответ: {response}")
    except Exception as e:
        print(f"Ошибка при запросе: {e}")

# Обработчик команды /check
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT amount FROM transactions WHERE user_id = ?", (user_id,))
    amount = cursor.fetchone()[0]
    cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
    status_pay = cursor.fetchone()[0]
    if status_pay == 1:
        await check_pay(update, user_id, amount)
        conn.close()
    else:
        await update.message.reply_text("Нет созданных платежей")
        conn.close()
        
# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    ''')
    
    # Таблица транзакций
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            user_id INTEGER PRIMARY KEY,
            amount INTEGER,
            status_pay INTEGER DEFAULT 0,
            invoice_uuid TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Таблица VPN-аккаунтов
    cursor.execute('''
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
    
    conn.commit()
    conn.close()

    conn = sqlite3.connect("user_states.db")
    cursor = conn.cursor()

    # Создаем таблицу, состояний пользователя
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_states (
        user_id INTEGER PRIMARY KEY,
        state TEXT DEFAULT 'None'
    )
    """)
    conn.commit()
    conn.close()

# Функция авторизации в панели VPN
def auth():
    login_url = "http://ВАШ_IP_3X_UI/login"
    login_payload = {"username": "enegonov", "password": "1q2w3e4R!"}
    login_headers = {'Accept': 'application/json;'}
    session = requests.Session()
    login_response = session.post(login_url, data=login_payload, headers=login_headers)
    cookies = session.cookies.get_dict()
    return cookies if '3x-ui' in cookies else None

# Функция добавления клиента
def add_client(days, user_email, cookies):
    url = "http:///ВАШ_IP_3X_UI/panel/api/inbounds/addClient"
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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("Привет! Ты зарегистрирован. Доступные команды: /balance, /create_client, /buy, /start")

# Обработчик команды /balance
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()[0]
    conn.close()
    await update.message.reply_text(f"Твой баланс: {balance} USD.")
    
#Запрос состояния пользователя из БД
def get_user_state(user_id):
    """Получаем состояние пользователя из базы данных."""
    conn = sqlite3.connect("user_states.db")
    cursor = conn.cursor()
    cursor.execute("SELECT state FROM user_states WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

#Изменение состояния пользователя в БД
def set_user_state(user_id, state):
    """Устанавливаем состояние пользователя в базе данных."""
    conn = sqlite3.connect("user_states.db")
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO user_states (user_id, state) VALUES (?, ?)", (user_id, state))
    conn.commit()
    conn.close()

# Обработчик покупки
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на пополнение, если статус 0."""
    user_id = update.message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO transactions (user_id) VALUES (?)", (user_id,))
    cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.commit()
    conn.close()

    if result and result[0] == 0:
        set_user_state(user_id, "awaiting_confirmation")  # <-- УСТАНАВЛИВАЕМ ОЖИДАНИЕ "ДА"/"НЕТ"
        keyboard = [["Да", "Нет"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text("Желаете пополнить аккаунт?", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Ожидается выполнение платежа.")


async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ответ (Да/Нет) и при 'Да' запрашивает сумму."""
    text = update.message.text
    user_id = update.message.from_user.id
    state = get_user_state(user_id)
    print(f"[handle_response] Пользователь {user_id} выбрал: {text}")  # Логируем действие

    if state == "awaiting_confirmation":  # Проверяем, что бот ждал "Да"/"Нет"
        if text == "Да":
            set_user_state(user_id, "awaiting_amount")  # Запоминаем, что ждем сумму
            await update.message.reply_text("Введите сумму для пополнения:", reply_markup=ReplyKeyboardRemove())
        elif text == "Нет":
            set_user_state(user_id, None)  # Сбрасываем состояние
            await update.message.reply_text("Хорошо, обращайтесь, когда будете готовы.", reply_markup=ReplyKeyboardRemove())

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод суммы, если бот ждет ее."""
    user_id = update.message.from_user.id
    # Получаем текущее состояние пользователя из базы данных
    state = get_user_state(user_id)

    print(f"[handle_amount] Пользователь {user_id} вводит сумму, текущее состояние: {state}")  # Логируем

    if state == "awaiting_amount":
        try:
            amount = float(update.message.text)  # Преобразуем в число
            set_user_state(user_id, None)  # Сбрасываем состояние

            await update.message.reply_text(f"Вы ввели сумму {amount} USD. Обрабатываю платеж..., проверить статус платежа /check. Платеж будет автоматически отменен через 15 минут.")

            # код для записи суммы в БД и логики оплаты
            invoice_link = await pay(amount, user_id)
            await update.message.reply_text(f"Ваша ссылка на оплату: {invoice_link}")
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите корректное число.")
    
def vless_get(inbound, user_uuid, user_email, cookies):
    EXTERNAL_IP = "ВАШ_IP_3X_UI"  
    FLOW = "flow=xtls-rprx-vision"  
    SERVER_PORT = 443  
    url = "http://ВАШ_IP_3X_UI/lol/panel/api/inbounds/get/1"

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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()[0]
    if balance < 1:
        await update.message.reply_text("❌ Недостаточно средств (нужно 1 USD). Пополните баланс.")
        conn.close()
        return
    
    # Вычитаем деньги и создаем VPN
    cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id = ?", (user_id,))
    cookies = auth()
    if not cookies:
        await update.message.reply_text("Ошибка авторизации в панели VPN.")
        conn.close()
        return
    
    user_email = f"user_{user_id}@"+str(uuid.uuid4())[:8]
    user_uuid, expiry_time, payload = add_client(30, user_email, cookies)
    
    # Сохраняем в БД
    cursor.execute("INSERT INTO vpn_accounts (user_id, uuid, email, expiry_time) VALUES (?, ?, ?, ?)",
                   (user_id, user_uuid, user_email, expiry_time))
    conn.commit()
    conn.close()
    
    # Генерация ссылки
    connection_string = vless_get(1, user_uuid, user_email, cookies)
    qr_file = generate_qr(connection_string, f"{user_id}.png")
    
    await update.message.reply_text(f"Твой VPN создан! Подключение: {connection_string}")
    await update.message.reply_photo(qr_file, "Твой QR-код!")
    



# Запуск бота
def main():
    init_db()
    app = Application.builder().token("ВАШ_TOKEN_TG_BOT").build()
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(MessageHandler(filters.Regex("^(Да|Нет)$"), handle_response))  # Только "Да" и "Нет"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount))  # Ввод суммы
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("create_client", create_client))
    app.add_handler(CommandHandler("check", check))
    app.run_polling()

if __name__ == "__main__":
    main()
