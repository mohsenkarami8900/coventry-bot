#!/usr/bin/env python3
"""ساخت عکس روزانه‌ی نرخ پوند از روی قالب assets/template.png

این ماژول مستقل از bot.py است و توسط آن (یا مستقیم) صدا زده می‌شود.
خروجی یک فایل PNG است که آماده‌ی ارسال به تلگرام (sendPhoto) است.
"""
import glob
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
TEMPLATE_PATH = os.path.join(ASSETS_DIR, "template.png")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")

POPPINS_BOLD = os.path.join(FONTS_DIR, "Poppins-Bold.ttf")

WHITE = (255, 255, 255)

PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _find_persian_font():
    """فونت فارسی (Vazirmatn) را که در ورک‌فلو نصب می‌شود پیدا می‌کند.
    در صورت نبودن (مثلاً تست محلی)، به فونت‌های جایگزین برمی‌گردد."""
    candidates = []
    for pattern in (
        "/usr/share/fonts/**/Vazirmatn*Bold*.ttf",
        "/usr/share/fonts/**/Vazirmatn*Bold*.otf",
        "/usr/share/fonts/**/*Vazir*Bold*.ttf",
    ):
        candidates += glob.glob(pattern, recursive=True)
    if candidates:
        return sorted(candidates)[0], True
    # جایگزین محلی برای تست (کیفیت پایین‌تر ولی حروف را درست می‌چسباند)
    local_fallback = os.path.join(FONTS_DIR, "unifont_preview_only.otf")
    if os.path.exists(local_fallback):
        return local_fallback, True
    return POPPINS_BOLD, False  # آخرین راه (فارسی درست نمایش داده نمی‌شود)


PERSIAN_FONT_PATH, PERSIAN_FONT_FOUND = _find_persian_font()


def _font(path, size):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


def _draw_centered(draw, box, text, font, fill=WHITE, rtl=False):
    x0, y0, x1, y1 = box
    kwargs = {}
    if rtl:
        kwargs = {"direction": "rtl", "language": "fa"}
    bbox = draw.textbbox((0, 0), text, font=font, **kwargs)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    cx = x0 + (x1 - x0 - w) / 2 - bbox[0]
    cy = y0 + (y1 - y0 - h) / 2 - bbox[1]
    draw.text((cx, cy), text, font=font, fill=fill, **kwargs)


def _fit_font(draw, text, path, box, max_size, min_size=14, rtl=False):
    """بزرگ‌ترین سایز فونتی که متن را داخل box (با کمی حاشیه) جا می‌دهد."""
    x0, y0, x1, y1 = box
    max_w = (x1 - x0) * 0.92
    max_h = (y1 - y0) * 0.85
    kwargs = {"direction": "rtl", "language": "fa"} if rtl else {}
    size = max_size
    while size > min_size:
        f = _font(path, size)
        bbox = draw.textbbox((0, 0), text, font=f, **kwargs)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            return f
        size -= 2
    return _font(path, min_size)


# ------------------------------------------------------------- مختصات قالب
# (x0, y0, x1, y1) هر جعبه که باید متن داخلش وسط‌چین شود
BOXES = {
    "fa_day": (855, 146, 959, 220),
    "fa_month": (975, 142, 1077, 220),
    "fa_year": (1095, 139, 1204, 220),
    "en_day": (859, 320, 1015, 378),
    "en_month": (1041, 320, 1199, 378),
    "hesabi_sell": (96, 600, 607, 766),   # فروش حسابی (چپ، بالا)
    "hesabi_buy": (640, 600, 1144, 766),  # خرید حسابی (راست، بالا)
    "naghdi_sell": (96, 815, 607, 981),   # فروش نقدی (چپ، پایین)
    "naghdi_buy": (640, 816, 1144, 982),  # خرید نقدی (راست، پایین)
}


def build_image(now: datetime, jy: int, jm: int, jd: int, prices: dict, out_path: str):
    im = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(im)

    fa_day = str(jd).translate(PERSIAN_DIGITS)
    fa_year = str(jy).translate(PERSIAN_DIGITS)
    fa_month = PERSIAN_MONTHS[jm - 1]
    en_day = str(now.day)
    en_month = now.strftime("%B")

    f = _fit_font(draw, fa_day, PERSIAN_FONT_PATH, BOXES["fa_day"], 44, rtl=True)
    _draw_centered(draw, BOXES["fa_day"], fa_day, f, rtl=True)

    f = _fit_font(draw, fa_month, PERSIAN_FONT_PATH, BOXES["fa_month"], 34, rtl=True)
    _draw_centered(draw, BOXES["fa_month"], fa_month, f, rtl=True)

    f = _fit_font(draw, fa_year, PERSIAN_FONT_PATH, BOXES["fa_year"], 34, rtl=True)
    _draw_centered(draw, BOXES["fa_year"], fa_year, f, rtl=True)

    f = _fit_font(draw, en_day, POPPINS_BOLD, BOXES["en_day"], 34)
    _draw_centered(draw, BOXES["en_day"], en_day, f)

    f = _fit_font(draw, en_month, POPPINS_BOLD, BOXES["en_month"], 30)
    _draw_centered(draw, BOXES["en_month"], en_month, f)

    for key in ("hesabi_sell", "hesabi_buy", "naghdi_sell", "naghdi_buy"):
        text = f"{prices[key]:,}"
        f = _fit_font(draw, text, POPPINS_BOLD, BOXES[key], 70)
        _draw_centered(draw, BOXES[key], text, f)

    im.save(out_path)
    return out_path


if __name__ == "__main__":
    # تست محلی با داده‌ی نمونه
    now = datetime(2026, 9, 22, 10, 0)
    prices = {
        "hesabi_sell": 299_000,
        "hesabi_buy": 289_000,
        "naghdi_sell": 296_000,
        "naghdi_buy": 286_000,
    }
    build_image(now, 1405, 6, 31, prices, os.path.join(ASSETS_DIR, "preview_output.png"))
    print("saved", PERSIAN_FONT_PATH)
