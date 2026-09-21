#!/usr/bin/env python3
"""ربات کانال Coventryexchange - ارسال روزانه قیمت پوند.

هر روز ساعت 10:00 به وقت لندن اجرا می‌شود:
  - دوشنبه تا شنبه: قیمت پوند را از tgju می‌خواند و پیام ثابت را با قیمت‌ها می‌فرستد
  - یکشنبه: پیام تعطیلی (+ نکته کوتاه)
"""
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ----------------------------------------------------------------- تنظیمات
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@Coventryexchange")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # اختیاری: خطاها را برای شما می‌فرستد
MANUAL_PRICE_RIAL = os.environ.get("MANUAL_PRICE_RIAL", "").strip()  # اختیاری: قیمت دستی به ریال
FORCE_RUN = os.environ.get("FORCE_RUN", "") == "1"  # نادیده گرفتن چک ساعت 10
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"  # فقط چاپ کن، ارسال نکن

SEND_HOUR = 10
TZ = ZoneInfo("Europe/London")

ROUND_STEP_RIAL = 10_000          # رند به پایین تا نزدیک‌ترین 10,000 ریال
# کسر از قیمت سایت (به تومان)
OFFSETS = {
    "hesabi_buy": 20_000,
    "hesabi_sell": 10_000,
    "naghdi_buy": 23_000,
    "naghdi_sell": 13_000,
}

TETHER_BUY = "0.74"
TETHER_SELL = "0.82"
PHONE = "+447918427402"

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
    "سرمایه‌گذاری موفق یعنی صبر؛ تصمیم‌های هیجانی معمولاً گران تمام می‌شوند.",
    "همه تخم‌مرغ‌ها را در یک سبد نگذارید؛ تنوع، ریسک را کم می‌کند.",
    "بازار بالا و پایین می‌شود؛ نقشه‌ی مشخص داشتن مهم‌تر از پیش‌بینی قیمت است.",
    "فقط با پولی سرمایه‌گذاری کنید که در کوتاه‌مدت به آن نیاز ندارید.",
    "ترس و طمع دو دشمن اصلی سرمایه‌گذارند؛ با برنامه تصمیم بگیرید نه با احساس.",
    "خرید و فروش منظم و تدریجی، از تصمیم‌گیری در بدترین لحظه بهتر است.",
    "قبل از هر معامله، سود و زیان قابل تحمل خود را مشخص کنید.",
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


def fetch_from_ajax():
    for url in AJAX_URLS[:2]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            node = data.get("current", data).get("price_gbp")
            if node and node.get("p"):
                v = _to_int(node["p"])
                if _plausible(v):
                    print(f"[price] {url} -> {v}")
                    return v
        except Exception as e:  # noqa: BLE001
            print(f"[price] ajax failed {url}: {e}")
    return None


def fetch_from_html():
    try:
        r = requests.get(PRICE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        html = r.text
        patterns = [
            r'data-col="info\.last_trade\.PDP"[^>]*>\s*([\d,۰-۹]+)',
            r'data-col="info\.last_trade\.PDP"[^>]*>.*?<span[^>]*>\s*([\d,۰-۹]+)',
            r'class="value"[^>]*>\s*<span[^>]*>\s*([\d,۰-۹]+)',
            r'"price_gbp".{0,200}?"p"\s*:\s*"([\d,]+)"',
        ]
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


def get_price_rial() -> int:
    if MANUAL_PRICE_RIAL:
        return _to_int(MANUAL_PRICE_RIAL)
    v = fetch_from_ajax() or fetch_from_html()
    if v is None:
        raise RuntimeError("قیمت پوند از tgju خوانده نشد.")
    return v


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


def build_price_message(now: datetime, p: dict) -> str:
    return f"""{date_header(now)}

خرید و فروش پوند در کاونتری

حسابی:
💷 خرید پوند حسابی: {fmt(p['hesabi_buy'])}
💷 فروش پوند حسابی: {fmt(p['hesabi_sell'])}

نقدی:
💷 خرید پوند نقدی: {fmt(p['naghdi_buy'])}
💷 فروش پوند نقدی: {fmt(p['naghdi_sell'])}

❗️خرید نقدی/حسابی زیر ۱٫۰۰۰ پوند شامل ۵ پوند کارمزد می‌باشد.

🔹خرید تتر به پوند: {TETHER_BUY}
🔹فروش تتر به پوند: {TETHER_SELL}
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
        tip = TIPS[now.toordinal() % len(TIPS)]
        msg += f"\n\n💡 {tip}"
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


def notify_admin(text: str):
    if ADMIN_CHAT_ID and BOT_TOKEN and not DRY_RUN:
        try:
            tg_send(ADMIN_CHAT_ID, text)
        except Exception as e:  # noqa: BLE001
            print("admin notify failed:", e)


# -------------------------------------------------------------------- main
def main() -> int:
    now = datetime.now(TZ)
    # GitHub cron به UTC است؛ دو زمان‌بندی داریم و فقط وقتی لندن ساعت 10 است ادامه می‌دهیم
    if not FORCE_RUN and now.hour != SEND_HOUR:
        print(f"London time is {now:%H:%M}, not {SEND_HOUR}:00 - skipping.")
        return 0

    if not DRY_RUN and not BOT_TOKEN:
        print("BOT_TOKEN تنظیم نشده است.")
        return 1

    try:
        if now.weekday() == 6:  # یکشنبه
            tg_send(CHANNEL, build_closed_message(now))
            print("closed-day message sent.")
            return 0

        price = get_price_rial()
        p = compute_prices(price)
        print(f"site={price} rounded={p['rounded_rial']} base_toman={p['base_toman']}")
        tg_send(CHANNEL, build_price_message(now, p))
        print("price message sent.")
        return 0
    except Exception as e:  # noqa: BLE001
        print("ERROR:", e)
        notify_admin(f"⚠️ ربات قیمت پوند خطا داد:\n{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
