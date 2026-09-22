#!/usr/bin/env python3
"""ربات کانال Coventryexchange - ارسال روزانه قیمت پوند.

دو حالت اجرا (با متغیر محیطی MODE مشخص می‌شود):
  - MODE=text  (ساعت 10:00 لندن): پیام متنی با قیمت‌ها و نرخ تتر
      دوشنبه تا شنبه: پیام قیمت | یکشنبه: پیام تعطیلی (+ نکته کوتاه)
  - MODE=image (ساعت 10:30 لندن): عکس نرخ روزانه (از روی قالب assets/template.png)
      یکشنبه‌ها عکسی ارسال نمی‌شود (صرافی تعطیل است)
"""
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import generate_image

# ----------------------------------------------------------------- تنظیمات
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@Coventryexchange")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # اختیاری: خطاها را برای شما می‌فرستد
MANUAL_PRICE_RIAL = os.environ.get("MANUAL_PRICE_RIAL", "").strip()  # اختیاری: قیمت دستی به ریال
FORCE_RUN = os.environ.get("FORCE_RUN", "") == "1"  # نادیده گرفتن چک ساعت 10
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"  # فقط چاپ کن، ارسال نکن
MODE = os.environ.get("MODE", "text").strip().lower()  # text یا image

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
        if MODE == "image":
            if now.weekday() == 6:  # یکشنبه - صرافی تعطیل، عکسی ارسال نمی‌شود
                print("Sunday - skipping image.")
                return 0
            if not generate_image.PERSIAN_FONT_FOUND:
                notify_admin("⚠️ فونت فارسی (Vazirmatn) پیدا نشد؛ تاریخ فارسی روی عکس درست نمایش داده نمی‌شود.")
            price = get_price_rial()
            p = compute_prices(price)
            print(f"site={price} rounded={p['rounded_rial']} base_toman={p['base_toman']}")
            jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
            out_path = generate_image.build_image(now, jy, jm, jd, p, "/tmp/rate.png")
            tg_send_photo(CHANNEL, out_path)
            print("image sent.")
            return 0

        if now.weekday() == 6:  # یکشنبه
            tg_send(CHANNEL, build_closed_message(now))
            print("closed-day message sent.")
            return 0

        price = get_price_rial()
        p = compute_prices(price)
        print(f"site={price} rounded={p['rounded_rial']} base_toman={p['base_toman']}")
        tb, ts = tether_rates()
        print(f"tether buy={tb} sell={ts}")
        tg_send(CHANNEL, build_price_message(now, p, tb, ts))
        print("price message sent.")
        return 0
    except Exception as e:  # noqa: BLE001
        print("ERROR:", e)
        notify_admin(f"⚠️ ربات قیمت پوند خطا داد ({MODE}):\n{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
