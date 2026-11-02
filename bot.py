#!/usr/bin/env python3
"""ربات کانال Coventryexchange - ارسال روزانه قیمت پوند.

با متغیر محیطی MODE مشخص می‌شود (پیش‌فرض: both):
  - MODE=both  : قیمت را یک‌بار می‌خواند و بلافاصله هم پیام متنی و هم عکس را
      با همون قیمتِ محاسبه‌شده (نه دو قیمتِ جدا) پشت‌سرهم می‌فرستد؛ دیگه
      منتظر هیچ ساعت خاصی برای عکس نمی‌مونه.
  - MODE=text  : فقط پیام متنی (برای تست دستی)
  - MODE=image : فقط عکس، با قیمتِ لحظه‌ی اجرا (برای تست دستی)
  یکشنبه‌ها (در هر MODE): فقط پیام تعطیلی فرستاده می‌شود، نه قیمت و نه عکس.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import generate_image

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_run.json")

# ----------------------------------------------------------------- تنظیمات
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@Coventryexchange")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # اختیاری: خطاها را برای شما می‌فرستد
MANUAL_PRICE_RIAL = os.environ.get("MANUAL_PRICE_RIAL", "").strip()  # اختیاری: قیمت دستی به ریال
FORCE_RUN = os.environ.get("FORCE_RUN", "") == "1"  # نادیده گرفتن چک ساعت 10
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"  # فقط چاپ کن، ارسال نکن
MODE = (os.environ.get("MODE", "").strip().lower() or "both")  # both / text / image

# --- زمان‌بندیِ ساده: یه تلاشِ خودکار دقیقاً حوالیِ ۱۰:۱۰ صبحِ لندن ---
# daily.yml دو تا cron داره (یکی برای فصل تابستان UK با یکی برای زمستون) که هر دو حوالیِ
# ۱۰:۱۰ لندن اجرا می‌شن؛ این بازه‌ی باریک پایین مشخص می‌کنه کدومش واقعاً مجاز به ارسالِ
# خودکاره (اونی که دقیقاً توی بازه افتاد) و کدوم فقط رد می‌شه بدون ارسال.
AUTO_SEND_START = (10, 10)   # از این دقیقه به بعد خودکار مجاز به ارسال
AUTO_SEND_END = (10, 30)     # از این دقیقه به بعد دیگه خودکار نفرست - خودِ کاربر ساعت ۱۰:۳۰ چک و
                             # در صورت نیاز دستی اجرا می‌کنه (لینک پایین پیام هشدار).
ALERT_HOUR = 10         # اگه تا این ساعت:دقیقه چیزی نرفته بود، به ادمین هشدار بده که دستی اجرا کنه (پشتیبان؛
ALERT_MINUTE = 35       # پشتیبانِ داخلیه، فقط وقتی کار می‌کنه که گیت‌هاب اصلاً اجرا بشه - بک‌آپ اصلی، healthchecks.io خارجیه، پایین‌تر)
TZ = ZoneInfo("Europe/London")

# نوع رویدادی که این اجرا رو شروع کرده: "schedule" (کرون خودِ گیت‌هاب) یا "workflow_dispatch"
# (دکمه‌ی Run workflow - چه با کلیک خودِ کاربر، چه با سرویس خارجی از طریق API).
# فقط برای اجرای "schedule" محدودیتِ بازه‌ی ساعتی (AUTO_SEND_START/END) اعمال می‌شه؛ اجرای دستی
# همیشه و هر ساعتی کار می‌کنه، چون خودِ کاربر عمداً زمانش رو انتخاب کرده - و چون last_run.json
# جلوی ارسال تکراری رو می‌گیره، اگه دستی اجرا کردی و بعداً schedule هم اجرا بشه، دوباره نمی‌فرسته.
EVENT_NAME = os.environ.get("EVENT_NAME", "").strip()
IS_DISPATCH = EVENT_NAME == "workflow_dispatch"

WORKFLOW_DISPATCH_URL = "https://github.com/mohsenkarami8900/coventry-bot/actions/workflows/daily.yml"

# healthchecks.io (یا مشابهش) - یه سرویسِ رایگانِ کاملاً جدا از گیت‌هاب که اگه هر روز تا یه
# ساعتِ مشخص ازش «پینگ موفقیت» نگیره، خودش (نه از طریق بات/گیت‌هاب) به تلگرام شما خبر می‌ده.
# این دقیقاً همون چیزیه که اگه گیت‌هاب امروز هیچ اجرایی نداشته باشه هم کار می‌کنه، چون ارسال‌کننده‌ی
# هشدار، سرور خودِ healthchecks.io هست، نه کدی که توی گیت‌هاب اکشن اجرا می‌شه.
HC_PING_URL = os.environ.get("HC_PING_URL", "").strip()

ROUND_STEP_RIAL = 10_000          # رند به پایین تا نزدیک‌ترین 10,000 ریال
# کسر از قیمت سایت (به تومان)
OFFSETS = {
    "hesabi_buy": 20_000,
    "hesabi_sell": 10_000,
    "naghdi_buy": 23_000,
    "naghdi_sell": 13_000,
}

# نرخ تتر به پوند: خرید = نرخ دلار به پوند (گرد شده به 2 رقم)، فروش = خرید + 0.08
TETHER_SPREAD = 0.08
TETHER_FALLBACK_BUY = 0.74  # اگر نرخ زنده گرفته نشد (و به ادمین خبر داده می‌شود)
FX_URLS = [
    ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["GBP"]),
    ("https://api.frankfurter.app/latest?from=USD&to=GBP", lambda d: d["rates"]["GBP"]),
]
PHONE = "+447769597420"

PRICE_URL = "https://www.tgju.org/profile/price_gbp"
AJAX_URLS = [
    "https://call5.tgju.org/ajax.json",
    "https://call4.tgju.org/ajax.json",
    "https://api.tgju.org/v1/market/indicator/summary-table-data/price_gbp",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fa,en;q=0.8",
}

# نکته‌های کوتاه برای یکشنبه‌ها (اگر نخواستید، USE_TIPS را False کنید)
USE_TIPS = True
TIPS = [
    ("Be fearful when others are greedy, and greedy when others are fearful.", "Warren Buffett"),
    ("Risk comes from not knowing what you're doing.", "Warren Buffett"),
    ("The four most dangerous words in investing are: 'This time it's different.'", "Sir John Templeton"),
    ("Know what you own, and know why you own it.", "Peter Lynch"),
    ("An investment in knowledge pays the best interest.", "Benjamin Franklin"),
    ("The stock market is a device for transferring money from the impatient to the patient.", "Warren Buffett"),
    ("Price is what you pay. Value is what you get.", "Warren Buffett"),
    ("Rule No. 1: Never lose money. Rule No. 2: Never forget rule No. 1.", "Warren Buffett"),
    ("In investing, what is comfortable is rarely profitable.", "Robert Arnott"),
    ("The individual investor should act consistently as an investor and not as a speculator.", "Benjamin Graham"),
]

PERSIAN_WEEKDAYS = {
    0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه",
    4: "جمعه", 5: "شنبه", 6: "یکشنبه",
}
PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


# --------------------------------------------------------------- قیمت‌گیری
def _to_int(s) -> int:
    s = str(s).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return int(re.sub(r"[^\d]", "", s))


def _plausible(v: int) -> bool:
    # پوند بین 1,000,000 تا 20,000,000 ریال
    return 1_000_000 <= v <= 20_000_000


def fetch_ajax_value(field_keys):
    """یک مقدار از price_gbp در ajax.json می‌خواند؛ اولین کلیدی از field_keys که پیدا شود."""
    for url in AJAX_URLS[:2]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            node = data.get("current", data).get("price_gbp")
            if node:
                for key in field_keys:
                    val = node.get(key)
                    if val:
                        v = _to_int(val)
                        if _plausible(v):
                            print(f"[price] {url} ({key}) -> {v}")
                            return v
        except Exception as e:  # noqa: BLE001
            print(f"[price] ajax failed {url}: {e}")
    return None


def fetch_from_ajax():
    """نرخ فعلی (last trade)."""
    return fetch_ajax_value(("p",))


def fetch_low_from_ajax():
    """پایین‌ترین قیمت روز."""
    return fetch_ajax_value(("l", "low", "min"))


def fetch_html_value(patterns):
    try:
        r = requests.get(PRICE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        html = r.text
        for p in patterns:
            m = re.search(p, html, re.S)
            if m:
                v = _to_int(m.group(1))
                if _plausible(v):
                    print(f"[price] html pattern -> {v}")
                    return v
    except Exception as e:  # noqa: BLE001
        print(f"[price] html failed: {e}")
    return None


def fetch_from_html():
    """نرخ فعلی (last trade) از صفحه‌ی html."""
    return fetch_html_value([
        r'data-col="info\.last_trade\.PDP"[^>]*>\s*([\d,۰-۹]+)',
        r'data-col="info\.last_trade\.PDP"[^>]*>.*?<span[^>]*>\s*([\d,۰-۹]+)',
        r'class="value"[^>]*>\s*<span[^>]*>\s*([\d,۰-۹]+)',
        r'"price_gbp".{0,200}?"p"\s*:\s*"([\d,]+)"',
    ])


def fetch_low_from_html():
    """پایین‌ترین قیمت روز از صفحه‌ی html (پشتیبان اگر ajax نداشت)."""
    return fetch_html_value([
        r'data-col="info\.last_trade\.PDL"[^>]*>\s*([\d,۰-۹]+)',
        r'data-col="info\.last_trade\.PDL"[^>]*>.*?<span[^>]*>\s*([\d,۰-۹]+)',
        r'پایین\s*ترین\s*قیمت\s*روز[\s\S]{0,300}?([\d,۰-۹]{6,})',
        r'"price_gbp".{0,200}?"l"\s*:\s*"([\d,]+)"',
    ])


def get_price_rial() -> int:
    if MANUAL_PRICE_RIAL:
        return _to_int(MANUAL_PRICE_RIAL)
    v = fetch_low_from_ajax() or fetch_low_from_html()
    if v is None:
        # اگر پایین‌ترین قیمت روز پیدا نشد، به نرخ فعلی برمی‌گردیم و به ادمین خبر می‌دهیم
        notify_admin("⚠️ پایین‌ترین قیمت روز از tgju پیدا نشد؛ به‌جایش نرخ فعلی استفاده شد.")
        v = fetch_from_ajax() or fetch_from_html()
    if v is None:
        raise RuntimeError("قیمت پوند از tgju خوانده نشد.")
    return v


def get_usd_gbp():
    """چند پوند برای هر 1 دلار (تتر ~ دلار). در صورت خطا None."""
    for url, pick in FX_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            v = float(pick(r.json()))
            if 0.3 < v < 2:
                print(f"[fx] {url} -> {v}")
                return v
        except Exception as e:  # noqa: BLE001
            print(f"[fx] failed {url}: {e}")
    return None


def tether_rates():
    v = get_usd_gbp()
    if v is None:
        notify_admin("⚠️ نرخ دلار به پوند گرفته نشد؛ نرخ تتر پیش‌فرض ارسال شد.")
        buy = TETHER_FALLBACK_BUY
    else:
        buy = round(v, 2)
    sell = round(buy + TETHER_SPREAD, 2)
    return f"{buy:.2f}", f"{sell:.2f}"


# ----------------------------------------------------------------- محاسبه
def compute_prices(price_rial: int) -> dict:
    rounded_rial = (price_rial // ROUND_STEP_RIAL) * ROUND_STEP_RIAL
    base_toman = rounded_rial // 10
    return {k: base_toman - off for k, off in OFFSETS.items()} | {
        "base_toman": base_toman,
        "rounded_rial": rounded_rial,
    }


def fmt(n: int) -> str:
    return f"{n:,}"


# ------------------------------------------------------------------ پیام‌ها
def gregorian_to_jalali(gy: int, gm: int, gd: int):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def date_header(now: datetime) -> str:
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    fa = f"{PERSIAN_WEEKDAYS[now.weekday()]} {jd} {PERSIAN_MONTHS[jm - 1]} {jy}"
    return f"{now.strftime('%A %d %B %Y')}\n{fa}"


def build_price_message(now: datetime, p: dict, tether_buy: str, tether_sell: str) -> str:
    return f"""{date_header(now)}

خرید و فروش پوند در کاونتری

حسابی:
💷 خرید پوند حسابی: {fmt(p['hesabi_buy'])}
💷 فروش پوند حسابی: {fmt(p['hesabi_sell'])}

نقدی:
💷 خرید پوند نقدی: {fmt(p['naghdi_buy'])}
💷 فروش پوند نقدی: {fmt(p['naghdi_sell'])}

❗️خرید نقدی/حسابی زیر ۱٫۰۰۰ پوند شامل ۵ پوند کارمزد می‌باشد.

🔹خرید تتر به پوند: {tether_buy}
🔹فروش تتر به پوند: {tether_sell}
مبلغ زیر ۱۰۰۰ تتر، شامل ۱۰ تتر کارمزد می‌باشد.

ساعت کار :
دوشنبه تا شنبه از ساعت 10:00 الی 17:00

📞شماره تماس:
{PHONE}

🏛آدرس :
Coventry

جهت مشاهده قيمت روزانه👇🏼
https://t.me/Coventryexchange"""


def build_closed_message(now: datetime) -> str:
    msg = f"{date_header(now)}\n\nامروز صرافی تعطیل است.\nقیمت‌ها از فردا (دوشنبه) ساعت 10:00 اعلام می‌شود."
    if USE_TIPS:
        quote, author = TIPS[now.toordinal() % len(TIPS)]
        msg += f'\n\n💡 "{quote}"\n— {author}'
    msg += "\n\nhttps://t.me/Coventryexchange"
    return msg


# ------------------------------------------------------------------ تلگرام
def tg_send(chat_id: str, text: str):
    if DRY_RUN:
        print("----- DRY RUN -> " + chat_id + " -----\n" + text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def tg_send_photo(chat_id: str, photo_path: str):
    if DRY_RUN:
        print(f"----- DRY RUN (photo) -> {chat_id} -----\n{photo_path}")
        return
    with open(photo_path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": chat_id},
            files={"photo": ("rate.png", f, "image/png")},
            timeout=60,
        )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def notify_admin(text: str):
    if ADMIN_CHAT_ID and BOT_TOKEN and not DRY_RUN:
        try:
            tg_send(ADMIN_CHAT_ID, text)
        except Exception as e:  # noqa: BLE001
            print("admin notify failed:", e)


def ping_healthcheck():
    """به healthchecks.io خبر می‌ده که امروز با موفقیت ارسال شد. این سرویس کاملاً خارج از
    گیت‌هابه: اگه این پینگ هر روز تا ساعتِ مشخص‌شده (توی تنظیمات خودِ healthchecks.io) نرسه،
    خودش مستقیم به تلگرام شما پیام می‌ده - حتی اگه گیت‌هاب اکشن امروز اصلاً اجرا نشده باشه."""
    if HC_PING_URL and not DRY_RUN:
        try:
            requests.get(HC_PING_URL, timeout=10)
        except Exception as e:  # noqa: BLE001
            print("healthcheck ping failed:", e)


# -------------------------------------------------------------- وضعیت روزانه
# این فایل (last_run.json) توی خودِ ریپو نگه‌داری و بعد از هر ارسالِ واقعی توسط
# ورک‌فلو کامیت می‌شود. دو تا فایده دارد: (۱) اگر به‌خاطر کرون پشتیبان یا اجرای
# دستی، دوباره اجرا شدیم، پیام/عکسِ تکراری نمی‌فرستیم. (۲) هر وقت بخوایم ببینیم
# آخرین ارسال موفق کِی بوده، کافیه همین فایل رو توی گیت‌هاب باز کنیم.
def load_state(today_key: str) -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") == today_key:
            return state
    except Exception:  # noqa: BLE001
        pass
    return {"date": today_key}


def save_state(state: dict):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        print("state save failed:", e)


# -------------------------------------------------------------------- main
def main() -> int:
    now = datetime.now(TZ)
    # فقط حوالیِ ۱۰:۱۰ صبحِ لندن (AUTO_SEND_START تا AUTO_SEND_END) اجرای خودکار (schedule) مجاز
    # به ارساله. daily.yml دو تا cron (یکی مناسب تابستون UK، یکی زمستون) داره که هر دو هر روز
    # اجرا می‌شن؛ هر کدوم که توی این بازه‌ی باریک نیفته، همینجا بی‌سروصدا رد می‌شه (بدون ارسال) -
    # و اون یکی که درست توی بازه افتاد، ارسال رو انجام می‌ده. اجرای دستی (IS_DISPATCH) از این چک
    # معافه و هر ساعتی که بزنی کار می‌کنه.
    now_t = (now.hour, now.minute)
    if not FORCE_RUN and not IS_DISPATCH and not (AUTO_SEND_START <= now_t < AUTO_SEND_END):
        print(f"London time is {now:%H:%M} (event={EVENT_NAME or 'schedule'}), outside the "
              f"{AUTO_SEND_START[0]:02d}:{AUTO_SEND_START[1]:02d}-{AUTO_SEND_END[0]:02d}:{AUTO_SEND_END[1]:02d} "
              f"auto-send window - skipping (run manually if needed).")
        return 0

    if not DRY_RUN and not BOT_TOKEN:
        print("BOT_TOKEN تنظیم نشده است.")
        return 1

    today_key = now.strftime("%Y-%m-%d")
    state = load_state(today_key)

    def mark_sent(key):
        if not DRY_RUN:
            state[key] = True
            save_state(state)

    try:
        if now.weekday() == 6:  # یکشنبه: فقط پیام تعطیلی، نه قیمت و نه عکس
            if not FORCE_RUN and state.get("closed_sent"):
                print("closed message already sent today - skipping (duplicate run).")
                return 0
            tg_send(CHANNEL, build_closed_message(now))
            print("closed-day message sent.")
            mark_sent("closed_sent")
            return 0

        # قیمت فقط یک‌بار خوانده و محاسبه می‌شود تا پیام متنی (۱۰:۰۰) و عکس (۱۰:۳۰)
        # دقیقاً همین یک قیمت را نشان بدهند، نه دو قیمتِ جدا از دو بار خواندن سایت.
        need_text = MODE in ("text", "both") and (FORCE_RUN or not state.get("text_sent"))
        need_image = MODE in ("image", "both") and (FORCE_RUN or not state.get("image_sent"))
        if not need_text and not need_image:
            print("already sent today (text & image) - skipping (duplicate run, probably the backup cron).")
            return 0

        price = get_price_rial()
        p = compute_prices(price)
        print(f"site_low={price} rounded={p['rounded_rial']} base_toman={p['base_toman']}")

        if MODE in ("text", "both"):
            if not need_text:
                print("text already sent today - skipping.")
            else:
                tb, ts = tether_rates()
                print(f"tether buy={tb} sell={ts}")
                tg_send(CHANNEL, build_price_message(now, p, tb, ts))
                print("price message sent.")
                mark_sent("text_sent")

        if MODE in ("image", "both"):
            if not need_image:
                print("image already sent today - skipping.")
            else:
                if not generate_image.PERSIAN_FONT_FOUND:
                    notify_admin("⚠️ فونت فارسی (Vazirmatn) پیدا نشد؛ تاریخ فارسی روی عکس درست نمایش داده نمی‌شود.")
                jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
                out_path = generate_image.build_image(now, jy, jm, jd, p, "/tmp/rate.png")
                tg_send_photo(CHANNEL, out_path)
                print("image sent.")
                mark_sent("image_sent")

        return 0
    except Exception as e:  # noqa: BLE001
        print("ERROR:", e)
        notify_admin(f"⚠️ ربات قیمت پوند خطا داد ({MODE}):\n{e}")
        return 1
    finally:
        # اگه امروز موفق (پیام تعطیلی یا پیام قیمت) ارسال شده، به healthchecks.io خبر می‌دیم.
        # این سیگنال اصلیه برای "هشدار اگه نرفت" - کاملاً خارج از گیت‌هاب کار می‌کنه (بالا توضیح داده شد).
        try:
            success_today = state.get("closed_sent") if now.weekday() == 6 else state.get("text_sent")
            if success_today:
                ping_healthcheck()
        except Exception as e3:  # noqa: BLE001
            print("healthcheck ping-check failed:", e3)

        # هشدارِ پشتیبانِ داخلی (اضافه بر healthchecks.io): اگه از ساعت ALERT_HOUR:ALERT_MINUTE
        # گذشته و هنوز امروز هیچی نرفته، یه بار (نه هر دفعه) به ادمین با لینک مستقیم اجرای دستی
        # خبر می‌ده. توجه: این فقط وقتی کار می‌کنه که گیت‌هاب اکشن واقعاً همین اجرا رو انجام بده؛
        # اگه گیت‌هاب امروز هیچ اجرایی نداشته باشه، این بخش هم اصلاً فرصت اجرا پیدا نمی‌کنه - برای
        # همون حالت، healthchecks.io (بالا) تنها چیزیه که قابل‌اعتماده.
        try:
            if not DRY_RUN and (now.hour, now.minute) >= (ALERT_HOUR, ALERT_MINUTE) and not state.get("alert_sent"):
                pending = (not state.get("closed_sent")) if now.weekday() == 6 else (not state.get("text_sent"))
                if pending:
                    notify_admin(
                        f"⚠️ تا ساعت {now:%H:%M} لندن هنوز پیام امروز توی کانال فرستاده نشده.\n\n"
                        f"برای ارسال دستی روی لینک زیر بزن و بعد «Run workflow» رو بزن:\n"
                        f"{WORKFLOW_DISPATCH_URL}"
                    )
                    mark_sent("alert_sent")
        except Exception as e2:  # noqa: BLE001
            print("alert check failed:", e2)


if __name__ == "__main__":
    sys.exit(main())
