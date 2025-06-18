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


# تابعی برای بارگذاری پیکربندی از فایل JSON
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

#صدور صورت‌حساب
async def pay(amount, user_id):
    
# نمونه‌ای از داده‌ها برای ایجاد صورت‌حساب
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

    # ایجاد صورت‌حساب
    response = sdk.create_invoice(invoice_data)
    print("Invoice Created:", response)

    #اتصال به پایگاه داده و قرار دادن کاربر در حالت انتظار برای پرداخت
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("UPDATE transactions SET status_pay = 1 WHERE user_id = ?", (user_id,))
    
        # دریافت invoice_uuid از پاسخ
        invoice_uuid = response['result']['uuid']  # مهم است که بدانید API دقیقاً چه چیزی را در فیلد uuid برمی‌گرداند.
        invoice_link = response['result']['link']  # لینک پرداخت
        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", (invoice_uuid, user_id))

        await cursor.execute("UPDATE transactions SET amount = ? WHERE user_id = ?", (amount, user_id))
        
        await conn.commit()
    print(f"Invoice UUID: {invoice_uuid}")
    return invoice_link

async def clear_pay(user_id, update):
    async with aiosqlite.connect("bot_database.db") as conn:
            cursor = await conn.cursor()
            async with conn.cursor() as cursor:
            # خواندن شماره پرداخت
                await cursor.execute("SELECT invoice_uuid FROM transactions WHERE user_id = ?", (user_id,))
                result = await cursor.fetchone()
                invoice_uuid = result[0] if result else None  # بررسی وجود داده
            if not invoice_uuid:
                await update.message.reply_text("هیچ پرداخت فعالی برای لغو وجود ندارد.")
                return
            response = sdk.cancel_invoice(invoice_uuid)

            if response.get("status") == "success":
                # اگر وضعیت "موفق" باشد، نتیجه را بررسی می‌کنیم
                if response.get("result") == ["ok"]:
                    await update.message.reply_text("پرداخت با موفقیت لغو شد.")
                    # مراحل اضافی برای پاکسازی یک تراکنش در پایگاه داده
                    async with conn.cursor() as cursor:
                        await cursor.execute("UPDATE transactions SET invoice_uuid = 'NONE' WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await conn.commit()
                else:
                    # اگر نتیجه "ok" نباشد، خطایی نمایش می‌دهیم.
                    await update.message.reply_text(f"خطا در هنگام لغو پرداخت: {response.get('result')}")
            elif response.get("status") == "error":
                # اگر وضعیت "خطا" باشد، یک پیام خطا نمایش می‌دهیم.
                error_message = response.get("result", {}).get("validate_error", "Неизвестная ошибка.")
                await update.message.reply_text(f"خطای لغو: {error_message}")
            else:
                # اگر وضعیت پاسخ نه موفقیت‌آمیز باشد و نه خطا
                await update.message.reply_text(f"وضعیت ناشناخته پاسخ: {response}")

# /cancel کنترل کننده فرمان
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await clear_pay(user_id, update)
            
async def check_pay(update, user_id, amount, conn):
            cursor = await conn.cursor()
            
            # بررسی اینکه آیا رکوردی برای تراکنش وجود دارد
            await cursor.execute("SELECT invoice_uuid FROM transactions WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            
            if row:
                invoice_uuid = row[0]
            else:
                await update.message.reply_text("خطا: دستور پرداخت برای این کاربر یافت نشد، لطفا با پشتیبانی فنی تماس بگیرید.")
                return
            try:
                # دریافت اطلاعات مربوط به پرداخت
                response = sdk.get_invoice_info([invoice_uuid])

                # ما وضعیت پاسخ و وجود نتیجه را بررسی می‌کنیم
                if response.get("status") == "success" and "result" in response and len(response["result"]) > 0:
                    #اولین عنصر را از لیست «نتیجه» دریافت کن.
                    invoice_info = response["result"][0]
                        
                    # وضعیت حساب را از نتیجه دریافت کنید
                    invoice_status = invoice_info.get("status")
                        
                    # چاپ وضعیت پرداخت
                    if invoice_status == "paid":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("پرداخت موفقیت‌آمیز بود، با دستور /balance موجودی را بررسی کنید")
                        print(f"وضعیت حساب: {invoice_status}")
                        await conn.commit()
                    elif invoice_status == "overpaid":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("پرداخت بیش از مقدار مورد نیاز انجام شده است، با دستور /balance موجودی را بررسی کنید")
                        print(f"وضعیت حساب: {invoice_status}")
                        await conn.commit()
                    elif invoice_status == "canceled":
                        await cursor.execute("UPDATE transactions SET status_pay = 0 WHERE user_id = ?", (user_id,))
                        await cursor.execute("UPDATE transactions SET invoice_uuid = ? WHERE user_id = ?", ('NONE', user_id))
                        await cursor.execute("UPDATE transactions SET amount = 0 WHERE user_id = ?", (user_id,))
                        await update.message.reply_text("پرداخت به دلیل پایان زمان لغو شده است")
                        print(f"وضعیت حساب: {invoice_status}")
                        await conn.commit()
                        print("پرداخت لغو شد.")
                    elif invoice_status == "created":
                        await update.message.reply_text("پرداخت ایجاد شد، لطفاً قبل از اتمام زمان پرداخت را انجام دهید!")
                    else:
                        print(f"وضعیت حساب: {invoice_status}")
                        print(response)
                else:
                    print(f"خطا در دریافت اطلاعات پرداخت. پاسخ:: {response}")
            except Exception as e:
                print(f"خطا در هنگام درخواست: {e}")

# Обработчик команды /check
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        async with conn.cursor() as cursor:
            # بررسی مبلغ پرداختی
            await cursor.execute("SELECT amount FROM transactions WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
            amount = result[0] if result else None  # بررسی وجود داده
            
            # بررسی وضعیت پرداخت
            await cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
            result2 = await cursor.fetchone()
            status_pay = result2[0] if result2 else None  # بررسی وجود داده
   
        if status_pay == 1:
            async with aiosqlite.connect("bot_database.db") as conn:  # باز کردن یک اتصال جدید
                await check_pay(update, user_id, amount, conn)
        else:
            await update.message.reply_text("هیچ پرداختی ایجاد نشده است")
        
# مقداردهی اولیه پایگاه داده
async def init_db():
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
    
        # جدول کاربران
        await cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0
            )
        ''')
        
        # جدول تراکنش‌ها
        await cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                user_id INTEGER PRIMARY KEY,
                amount INTEGER,
                status_pay INTEGER DEFAULT 0,
                invoice_uuid TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # جدول حساب‌های VPN
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

        # ایجاد جدول وضعیت‌های کاربران
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT DEFAULT 'None'
        )
        """)
        await conn.commit()

# تابع ورود به پنل VPN
def auth():
    login_url = f"http://{xui_ip}:{xui_port}{xui_patch}login"
    login_payload = {"username": xui_login, "password": xui_pass}
    login_headers = {'Accept': 'application/json;'}
    session = requests.Session()
    login_response = session.post(login_url, data=login_payload, headers=login_headers)
    cookies = session.cookies.get_dict()
    return cookies if '3x-ui' in cookies else None

# تابع افزودن کاربر
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

# تولید کد QR
def generate_qr(data, filename):
    qr = qrcode.make(data)
    qr.save(filename)
    return filename

# کنترل‌کننده‌ی فرمان /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await conn.commit()
    message = (
        "<b>سلام!</b> 👋\n\n"
        "شما با موفقیت در بات ثبت‌نام شدید. "
        "Теперь ты можешь использовать все доступные команды:\n\n"
        "<u>• /balance</u> — Показывает твой <i>баланс</i> 💰\n"
        "<u>• /create_client</u> — Создает нового <i>клиента</i> 🧑‍💼\n"
        "<u>• /buy</u> — خرید محصول 🛒\n"
        "<u>• /help</u> — Получить <i>помощь</i> ❓\n"
        "<u>• /cancel</u> — Отменяет <i>платеж</i> ❌\n\n"
        "استفاده لذت‌بخشی داشته باشید! 😊"
    )
    await update.message.reply_text(message, parse_mode = "HTML")

# کنترل‌کننده‌ی فرمان /help
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = (
        "<b>دستورات در دسترس:</b>👋\n\n"
        "<u>• /balance</u> — نمایش <i>موجودی</i> 💰\n"
        "<u>• /create_client</u> — ایجاد <i>کاربر جدید</i> 🧑‍💼\n"
        "<u>• /buy</u> — خرید <i>محصول</i> 🛒\n"
        "<u>• /help</u> — دریافت <i>راهنما</i> ❓\n"
        "<u>• /cancel</u> — لغو <i>پرداخت</i> ❌\n\n"
        "استفاده لذت‌بخشی داشته باشید! 😊"
    )
    await update.message.reply_text(message, parse_mode = "HTML")

# کنترل‌کننده‌ی فرمان /balance
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
        await update.message.reply_text(f"موجودی شما: {balance} USD.")
    
#درخواست وضعیت کاربر از پایگاه داده
async def get_user_state(user_id):
    """دریافت وضعیت کاربر از پایگاه داده."""
    async with aiosqlite.connect("user_states.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT state FROM user_states WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
    return result[0] if result else None

#تغییر وضعیت کاربر در پایگاه داده
async def set_user_state(user_id, state):
    """تنظیم وضعیت کاربر در پایگاه داده."""
    async with aiosqlite.connect("user_states.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("REPLACE INTO user_states (user_id, state) VALUES (?, ?)", (user_id, state))
        await conn.commit()

# پردازنده خرید
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست شارژ در صورتی که وضعیت ۰ باشد."""
    user_id = update.message.from_user.id
    async with aiosqlite.connect("bot_database.db") as conn:
        cursor = await conn.cursor()
        await cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await cursor.execute("INSERT OR IGNORE INTO transactions (user_id) VALUES (?)", (user_id,))
        await cursor.execute("SELECT status_pay FROM transactions WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        await conn.commit()

    if result and result[0] == 0:
        await set_user_state(user_id, "awaiting_confirmation")  # <-- قرار دادن در وضعیت انتظار پاسخ بله/خیر
        keyboard = [["Да", "Нет"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text("آیا مایل به شارژ حساب هستید؟", reply_markup=reply_markup)
    else:
        await update.message.reply_text("در انتظار انجام پرداخت است.")


async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ (بله/خیر) را پردازش می‌کند و اگر «بله» باشد، مبلغ را می‌پرسد."""
    text = update.message.text
    user_id = update.message.from_user.id
    state = await get_user_state(user_id)
    print(f"[handle_response] کاربر {user_id} انتخاب کرد: {text}")  # ثبت اقدامات

    if state == "awaiting_confirmation":  # بررسی کنید که ربات منتظر «بله»/«خیر» بوده است
        if text == "بله":
            await set_user_state(user_id, "awaiting_amount")  # به یاد داشته باشید که ما منتظر مبلغ هستیم
            await update.message.reply_text("لطفاً مبلغ مورد نظر برای شارژ را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        elif text == "خیر":
            await set_user_state(user_id, None)  # تنظیم مجدد وضعیت
            await update.message.reply_text("باشه، هر وقت آماده بودید پیام دهید.", reply_markup=ReplyKeyboardRemove())

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورود مبلغ را پردازش می‌کند، اگر ربات منتظر آن باشد."""
    user_id = update.message.from_user.id
    # ما وضعیت فعلی کاربر را از پایگاه داده دریافت می‌کنیم
    state = await get_user_state(user_id)

    print(f"[handle_amount] کاربر {user_id} مقدار و وضعیت فعلی را وارد می‌کند: {state}")  # Логируем

    if state == "awaiting_amount":
        try:
            amount = float(update.message.text)  # بیایید آن را به عدد تبدیل کنیم
            await set_user_state(user_id, None)  # تنظیم مجدد وضعیت
            message = (
                f"<b>شما مبلغ {amount} دلار وارد کردید. در حال پردازش پرداخت...</b>👋\n\n"
                "<u>• /check</u> — بررسی <i>وضعیت پرداخت</i> 💰\n"
                "<u>• /cancel</u> — لغو <i>پرداخت</i> ❌\n\n"
                "⚠️ <b>توجه!</b> پس از انجام پرداخت و تأیید آن توسط درگاه، <b>حتماً</b> با استفاده از دستور /check وضعیت پرداخت را بررسی کنید! 😊"
            )
            await update.message.reply_text(message, parse_mode = "HTML")

            # کد ثبت مبلغ در پایگاه داده و منطق پرداخت
            invoice_link = await pay(amount, user_id)
            await update.message.reply_text(f"لینک پرداخت شما: {invoice_link}")
        except ValueError:
            await update.message.reply_text("لطفاً یک عدد صحیح وارد کنید.")
    
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
    reality_settings = json.loads(settings)["realitySettings"]  # رشته را به JSON تبدیل (deserialize) کنید (اگر رشته است)

    public_key = reality_settings["settings"]["publicKey"]
    short_id = reality_settings["shortIds"][0]  # اولین shortId
    server_name = reality_settings["serverNames"][0]

    connection_string = (
            f"vless://{user_uuid}@{EXTERNAL_IP}:{SERVER_PORT}"
            f"?type=tcp&security=reality&pbk={public_key}&fp=chrome&sni={server_name}"
            f"&sid={short_id}&spx=%2F&{FLOW}#{user_email}"
        )

    print(connection_string)
    return connection_string

# کنترل کننده ایجاد حساب VPN
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
            await update.message.reply_text("❌ موجودی کافی نیست (معادل 200 هزار تومان نیاز دارم). موجودی خود را شارژ کنید.")
            return
        
        # پول را کم کنید و یک VPN ایجاد کنید
        await cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id = ?", (user_id,))
        cookies = auth()
        if not cookies:
            await update.message.reply_text("خطای احراز هویت در پنل VPN.")
            return
    
        user_email = f"user_{user_id}@"+str(uuid.uuid4())[:8]
        user_uuid, expiry_time, payload = add_client(30, user_email, cookies)
        
        # ذخیره در پایگاه داده
        await cursor.execute("INSERT INTO vpn_accounts (user_id, uuid, email, expiry_time) VALUES (?, ?, ?, ?)",
                       (user_id, user_uuid, user_email, expiry_time))
        await conn.commit()
        
        # تولید لینک
        connection_string = vless_get(1, user_uuid, user_email, cookies)
        qr_file = generate_qr(connection_string, f"{user_id}.png")
        
        await update.message.reply_text(f"VPN شما ساخته شد! اطلاعات اتصال:: {connection_string}")
        await update.message.reply_photo(qr_file, "کد QR شما!")
    
asyncio.run(init_db())

# راه اندازی ربات
def main():
    #logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = Application.builder().token(tg_key).build()
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(MessageHandler(filters.Regex("^(بله|خیر)$"), handle_response))  # Только "Да" и "Нет"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount))  # Ввод суммы
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("create_client", create_client))
    app.add_handler(CommandHandler("check", check))
    loop.run_until_complete(app.run_polling())
    
if __name__ == "__main__":
    main()
