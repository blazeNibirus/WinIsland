# -*- coding: utf-8 -*-
"""
WinIsland 2.1 — «Dynamic Island» для Windows 10/11 в стиле Apple.

Раскрытый остров = панель вкладок сверху + содержимое:
  [ Медиа ] [ Быстрые настройки ] [ Таймер / Секундомер ] [ Заметки ]

Ещё умеет:
  * остров (капсула) или чёлка как на iPhone 13 (с динамиком и камерой);
  * уведомления Windows (Telegram, WhatsApp, почта…) как баннер в острове;
  * таймер и секундомер (в свёрнутом острове — мини-таймер);
  * загрузки файлов с процентом, погода с выбором города, полка для файлов;
  * скрывается, когда открыто полноэкранное приложение (настройка в трее).
"""

import sys
import os
import json
import math
import time
import threading
import asyncio
import datetime
import tempfile
import shutil
import sqlite3
import subprocess
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import (Qt, QTimer, QRectF, QPointF, QPoint, QUrl, QMimeData, QObject,
                          pyqtSignal, QFileInfo, QLockFile, QDir, QPropertyAnimation,
                          QEasingCurve, QParallelAnimationGroup)
from PyQt6.QtGui import (QPainter, QColor, QPainterPath, QFont, QPixmap, QPen, QDrag,
                         QGuiApplication, QCursor, QIcon, QAction, QActionGroup)
from PyQt6.QtWidgets import (QApplication, QWidget, QSystemTrayIcon, QMenu, QFileIconProvider,
                             QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QTextEdit)

# ---- плеер Windows (System Media Transport Controls) ----
WINRT_ERROR = ""
try:
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as MediaManager
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    HAVE_WINRT = True
except Exception as _e:  # noqa
    HAVE_WINRT = False
    WINRT_ERROR = str(_e)

# ---- Wi-Fi / Bluetooth (радиомодули) ----
RADIO_ERROR = ""
try:
    from winrt.windows.devices.radios import Radio, RadioState
    HAVE_RADIO = True
except Exception as _e:  # noqa
    HAVE_RADIO = False
    RADIO_ERROR = str(_e)

# =====================================================================
#                           НАСТРОЙКИ
# =====================================================================
TOP_MARGIN = 6            # отступ капсулы от верхнего края экрана
HOVER_OPEN_DELAY = 0.25   # через сколько секунд наведения остров раскрывается
DL_PORT = 47831           # порт для расширения браузера (загрузки)
EXTRA_WATCH_DIRS = []     # дополнительные папки для слежения за загрузками, например [r"D:\Загрузки"]

# размеры состояний (px)
W_IDLE, H_IDLE = 150, 36
W_IDLE_NOTCH = 214
W_MEDIA, H_MEDIA = 236, 38
W_CTIMER, H_CTIMER = 180, 38
T0 = 56                   # где начинается содержимое вкладки (под панелью вкладок)
W_EXP, H_EXP = 440, 246   # вкладка «Медиа»
SHELF_H = 96
DL_H = 52
W_DROP, H_DROP = 440, 116
W_NOTIF, H_NOTIF = 400, 78
W_DL, H_DL = 340, 52
FLARE = 14                # «плечи» чёлки по бокам

APP_NAME = "WinIsland"
TABS = ("media", "quick", "timer", "notes")
LAYERS = ("idle", "media", "ctimer", "expanded", "drop", "notif", "download")

AL = Qt.AlignmentFlag
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
PARTIAL_EXT = (".crdownload", ".part", ".opdownload", ".download", ".partial", ".aria2", ".tdownload")

ORANGE = (255, 159, 10)
GREEN = (48, 209, 88)
RED = (255, 69, 58)
BLUE = (10, 132, 255)

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")
NOTES_PATH = os.path.join(SETTINGS_DIR, "notes.json")
DEFAULT_SETTINGS = {"style": "island", "notch_decor": True, "close_delay": 10,
                    "notifications": True, "downloads": True, "city": None,
                    "hide_fullscreen": True}


class Settings:
    def __init__(self):
        self.d = dict(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                self.d.update(json.load(f))
        except Exception:
            pass

    def __getitem__(self, k):
        return self.d.get(k, DEFAULT_SETTINGS.get(k))

    def __setitem__(self, k, v):
        self.d[k] = v
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


class NoteStore:
    """Заметки хранятся в файле notes.json (старые — первыми)."""

    def __init__(self, path=None):
        self.path = path or NOTES_PATH
        self.items = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.items = [str(x) for x in data]
        except Exception:
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add(self, text):
        self.items.append(text)
        self.save()

    def update(self, idx, text):
        if 0 <= idx < len(self.items):
            self.items[idx] = text
            self.save()

    def remove(self, idx):
        if 0 <= idx < len(self.items):
            del self.items[idx]
            self.save()

    def rev(self):
        """[(индекс, текст)] — самые новые сверху."""
        return [(i, self.items[i]) for i in range(len(self.items) - 1, -1, -1)]


class ClockTools:
    """Таймер и секундомер (чистая логика, без рисования)."""

    def __init__(self):
        self.mode = "timer"          # какой вид открыт: timer | sw
        self.t_dur = 300.0
        self.t_state = "idle"        # idle | running | paused
        self.t_end = 0.0
        self.t_left = 300.0
        self.sw_state = "idle"       # idle | running | stopped
        self.sw_start = 0.0
        self.sw_acc = 0.0
        self.laps = []

    # --- таймер ---
    def t_remaining(self, now=None):
        now = time.monotonic() if now is None else now
        if self.t_state == "running":
            return max(0.0, self.t_end - now)
        if self.t_state == "paused":
            return self.t_left
        return self.t_dur

    def t_adjust(self, delta_min):
        if self.t_state == "idle":
            self.t_dur = float(max(60, min(99 * 3600, self.t_dur + delta_min * 60)))

    def t_set(self, minutes):
        if self.t_state == "idle":
            self.t_dur = float(minutes * 60)

    def t_start(self, now=None):
        now = time.monotonic() if now is None else now
        if self.t_state == "idle":
            self.t_left = self.t_dur
        self.t_end = now + self.t_left
        self.t_state = "running"

    def t_pause(self, now=None):
        now = time.monotonic() if now is None else now
        if self.t_state == "running":
            self.t_left = max(0.0, self.t_end - now)
            self.t_state = "paused"

    def t_cancel(self):
        self.t_state = "idle"

    def t_check(self, now=None):
        """True один раз — в момент, когда таймер дошёл до нуля."""
        now = time.monotonic() if now is None else now
        if self.t_state == "running" and now >= self.t_end:
            self.t_state = "idle"
            return True
        return False

    # --- секундомер ---
    def sw_elapsed(self, now=None):
        now = time.monotonic() if now is None else now
        if self.sw_state == "running":
            return self.sw_acc + (now - self.sw_start)
        return self.sw_acc

    def sw_toggle(self, now=None):
        now = time.monotonic() if now is None else now
        if self.sw_state == "running":
            self.sw_acc += now - self.sw_start
            self.sw_state = "stopped"
        else:
            self.sw_start = now
            self.sw_state = "running"

    def sw_secondary(self, now=None):
        """Пока идёт — «Круг», когда остановлен — «Сброс»."""
        now = time.monotonic() if now is None else now
        if self.sw_state == "running":
            self.laps.append(self.sw_elapsed(now))
        elif self.sw_acc > 0 or self.sw_state == "stopped":
            self.sw_state = "idle"
            self.sw_acc = 0.0
            self.laps = []

    # --- общее ---
    def active(self):
        return self.t_state in ("running", "paused") or self.sw_state == "running"

    def mini_text(self, now=None):
        now = time.monotonic() if now is None else now
        if self.t_state in ("running", "paused"):
            return fmt_clock(math.ceil(self.t_remaining(now) - 1e-6))
        if self.sw_state == "running":
            return fmt_clock(int(self.sw_elapsed(now)))
        return ""


# =====================================================================
#                           Вспомогательное
# =====================================================================
def ru_date(d):
    return "%s, %d %s" % (WEEKDAYS[d.weekday()].capitalize(), d.day, MONTHS[d.month - 1])


def fmt_time(sec):
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def fmt_clock(sec):
    """Для таймера: 05:00 или 1:05:00."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def fmt_sw(sec):
    """Для секундомера: 01:23,45."""
    sec = max(0.0, float(sec))
    total_cs = int(sec * 100)
    cs = total_cs % 100
    s = (total_cs // 100) % 60
    m = total_cs // 6000
    return "%02d:%02d,%02d" % (m, s, cs)


def fmt_size(n):
    n = float(max(0, n))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            s = ("%.0f" % n) if unit == "Б" else ("%.1f" % n)
            return s.replace(".", ",") + " " + unit
        n /= 1024.0
    return "0 Б"


def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t),
                  int(a.alpha() + (b.alpha() - a.alpha()) * t))


def weather_info(code, is_day):
    if code == 0:
        return ("☀️" if is_day else "🌙"), "Ясно"
    if code == 1:
        return ("🌤️" if is_day else "🌙"), "Малооблачно"
    if code == 2:
        return ("⛅" if is_day else "☁️"), "Облачно с прояснениями"
    if code == 3:
        return "☁️", "Пасмурно"
    if code in (45, 48):
        return "🌫️", "Туман"
    if code in (51, 53, 55, 56, 57):
        return "🌦️", "Морось"
    if code in (61, 63, 65, 66, 67):
        return "🌧️", "Дождь"
    if code in (71, 73, 75, 77):
        return "❄️", "Снег"
    if code in (80, 81, 82):
        return "🌦️", "Ливень"
    if code in (85, 86):
        return "🌨️", "Снегопад"
    if code in (95, 96, 99):
        return "⛈️", "Гроза"
    return "🌡️", ""


def partial_base(path):
    low = path.lower()
    for ext in PARTIAL_EXT:
        if low.endswith(ext):
            return path[:-len(ext)]
    return None


def display_name(path):
    base = os.path.basename(path)
    pb = partial_base(base)
    if pb is not None:
        base = pb
        if base.lower().startswith("unconfirmed "):
            base = "Файл из браузера"
    return base or "Файл"


def app_label(pid):
    """Человеческое имя приложения из идентификатора уведомления."""
    raw = (pid or "").split("!")[0]
    low = raw.lower()
    known = [("telegram", "Telegram"), ("whatsapp", "WhatsApp"), ("discord", "Discord"),
             ("viber", "Viber"), ("skype", "Skype"), ("slack", "Slack"), ("spotify", "Spotify"),
             ("yandex", "Яндекс"), ("chrome", "Chrome"), ("msedge", "Edge"), ("edge", "Edge"),
             ("firefox", "Firefox"), ("outlook", "Outlook"), ("mail", "Почта"),
             ("powershell", "Windows"), ("vk", "VK"), ("steam", "Steam"), ("teams", "Teams"),
             ("zoom", "Zoom")]
    for key, name in known:
        if key in low:
            return name
    raw = raw.split("_")[0]
    last = raw.split(".")[-1] if raw else ""
    return (last[:1].upper() + last[1:]) if last else "Уведомление"


def parse_toast(payload):
    """Достаёт (заголовок, текст, путь к картинке) из XML уведомления Windows."""
    if isinstance(payload, (bytes, bytearray)):
        txt = None
        for enc in ("utf-8", "utf-16"):
            try:
                txt = bytes(payload).decode(enc)
                break
            except Exception:
                continue
        if txt is None:
            return None
    else:
        txt = str(payload)
    txt = txt.strip("\ufeff\x00 \r\n\t")
    root = ET.fromstring(txt)
    texts = [(t.text or "").strip() for t in root.iter("text")]
    texts = [t for t in texts if t]
    img = None
    for im in root.iter("image"):
        src = im.get("src") or ""
        if not src:
            continue
        if src.lower().startswith("file:///"):
            src = urllib.parse.unquote(src[8:]).replace("/", "\\")
        if os.path.isfile(src):
            img = src
            break
    title = texts[0] if texts else ""
    body = " ".join(texts[1:3])
    return title, body, img


TEST_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$n = $t.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($t.CreateTextNode('WinIsland тест')) > $null;"
    "$n.Item(1).AppendChild($t.CreateTextNode('Если вы видите это в острове — уведомления работают')) > $null;"
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($t);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($toast)"
)


def send_test_toast():
    if sys.platform != "win32":
        return
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", TEST_TOAST_PS],
                         creationflags=0x08000000)
    except Exception as ex:
        print("[WinIsland] тестовое уведомление:", ex)


def beep_alarm():
    """Звук окончания таймера (системный сигнал Windows), в отдельном потоке."""
    def run():
        try:
            import ctypes
            for _ in range(4):
                ctypes.windll.user32.MessageBeep(0x40)
                time.sleep(0.8)
        except Exception:
            pass
    if sys.platform == "win32":
        threading.Thread(target=run, daemon=True).start()


# =====================================================================
#                Шрифты и значки в стиле SF Symbols (рисуем сами)
# =====================================================================
def F(px, weight=QFont.Weight.Normal):
    f = QFont()
    f.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Helvetica Neue", "Arial"])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def EF(px):
    f = QFont()
    f.setFamilies(["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"])
    f.setPixelSize(px)
    return f


def draw_text(p, rect, s, font, color, align=AL.AlignLeft | AL.AlignVCenter, elide=True):
    p.setFont(font)
    p.setPen(color)
    if elide:
        s = p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(rect.width()))
    p.drawText(rect, align, s)


def _stroke(p, color, w=1.8):
    p.setPen(QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _poly(p, pts, color, width=3.0):
    path = QPainterPath()
    path.moveTo(pts[0][0], pts[0][1])
    for q in pts[1:]:
        path.lineTo(q[0], q[1])
    path.closeSubpath()
    p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(color)
    p.drawPath(path)


def icon_play(p, cx, cy, s, color):
    k = s / 2
    _poly(p, [(cx - k * 0.65, cy - k * 0.9), (cx - k * 0.65, cy + k * 0.9), (cx + k * 0.9, cy)], color)


def icon_pause(p, cx, cy, s, color):
    k = s / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(cx - k * 0.78, cy - k * 0.95, k * 0.6, k * 1.9), 2.2, 2.2)
    p.drawRoundedRect(QRectF(cx + k * 0.18, cy - k * 0.95, k * 0.6, k * 1.9), 2.2, 2.2)


def icon_next(p, cx, cy, s, color):
    k = s / 2
    cx -= k * 0.1
    _poly(p, [(cx - k, cy - k * 0.8), (cx - k, cy + k * 0.8), (cx, cy)], color)
    _poly(p, [(cx, cy - k * 0.8), (cx, cy + k * 0.8), (cx + k, cy)], color)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(cx + k * 1.05, cy - k * 0.85, k * 0.3, k * 1.7), 1.5, 1.5)


def icon_prev(p, cx, cy, s, color):
    p.save()
    p.translate(cx, cy)
    p.scale(-1, 1)
    icon_next(p, 0, 0, s, color)
    p.restore()


def icon_wifi(p, cx, cy, color):
    p.setPen(QPen(color, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for r in (6, 11, 16):
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 45 * 16, 90 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), 2.3, 2.3)


def icon_bt(p, cx, cy, color):
    pts = [(-5, -6), (5, 4), (0, 9), (0, -9), (5, -4), (-5, 6)]
    path = QPainterPath()
    path.moveTo(cx + pts[0][0], cy + pts[0][1])
    for q in pts[1:]:
        path.lineTo(cx + q[0], cy + q[1])
    _stroke(p, color, 2.4)
    p.drawPath(path)


def icon_airplane(p, cx, cy, color):
    pts = [(0, -10), (2, -4.5), (10, 1.5), (10, 4), (2, 2), (1.4, 7.5), (4.2, 9.3), (4.2, 10.8),
           (0, 9.8), (-4.2, 10.8), (-4.2, 9.3), (-1.4, 7.5), (-2, 2), (-10, 4), (-10, 1.5), (-2, -4.5)]
    p.save()
    p.translate(cx, cy)
    p.rotate(45)
    _poly(p, pts, color, 1.4)
    p.restore()


def icon_speaker(p, cx, cy, color, muted=False):
    p.save()
    p.translate(cx, cy)
    _poly(p, [(-9, -3.2), (-5, -3.2), (0.5, -8), (0.5, 8), (-5, 3.2), (-9, 3.2)], color, 1.6)
    if muted:
        _stroke(p, color, 1.9)
        p.drawLine(QPointF(-10, -10), QPointF(10, 10))
    else:
        _stroke(p, color, 1.8)
        for r in (5, 9):
            p.drawArc(QRectF(1.5 - r, -r, 2 * r, 2 * r), -45 * 16, 90 * 16)
    p.restore()


def icon_sun(p, cx, cy, color):
    p.save()
    p.translate(cx, cy)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(0, 0), 4.2, 4.2)
    _stroke(p, color, 1.8)
    for i in range(8):
        a = i * math.pi / 4
        p.drawLine(QPointF(7 * math.cos(a), 7 * math.sin(a)), QPointF(10 * math.cos(a), 10 * math.sin(a)))
    p.restore()


def icon_tab_media(p, cx, cy, color):
    _stroke(p, color, 1.8)
    path = QPainterPath()
    path.moveTo(cx + 2.5, cy + 4.5)
    path.lineTo(cx + 2.5, cy - 7)
    path.cubicTo(cx + 2.5, cy - 4.2, cx + 7, cy - 4.8, cx + 7, cy - 1.0)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx - 0.5, cy + 5), 3.4, 2.7)


def icon_tab_quick(p, cx, cy, color):
    _stroke(p, color, 1.8)
    p.drawLine(QPointF(cx - 8, cy - 4.5), QPointF(cx + 8, cy - 4.5))
    p.drawLine(QPointF(cx - 8, cy + 4.5), QPointF(cx + 8, cy + 4.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx - 3, cy - 4.5), 3.0, 3.0)
    p.drawEllipse(QPointF(cx + 3.5, cy + 4.5), 3.0, 3.0)


def icon_tab_timer(p, cx, cy, color):
    _stroke(p, color, 1.8)
    p.drawEllipse(QPointF(cx, cy + 1.5), 7, 7)
    p.drawLine(QPointF(cx - 2.5, cy - 8.3), QPointF(cx + 2.5, cy - 8.3))
    p.drawLine(QPointF(cx, cy + 1.5), QPointF(cx + 3, cy - 1.5))
    p.drawLine(QPointF(cx + 5.8, cy - 4.8), QPointF(cx + 7.4, cy - 6.4))


def icon_tab_notes(p, cx, cy, color):
    _stroke(p, color, 1.8)
    p.drawRoundedRect(QRectF(cx - 6.5, cy - 8, 13, 16), 3, 3)
    p.drawLine(QPointF(cx - 3, cy - 3), QPointF(cx + 3, cy - 3))
    p.drawLine(QPointF(cx - 3, cy + 1), QPointF(cx + 3, cy + 1))
    p.drawLine(QPointF(cx - 3, cy + 5), QPointF(cx + 0.5, cy + 5))


# =====================================================================
#                      Пружинная анимация (как у Apple)
# =====================================================================
class Spring:
    def __init__(self, value, stiffness=170.0, damping=22.0):
        self.x = float(value)
        self.v = 0.0
        self.target = float(value)
        self.k = stiffness
        self.c = damping

    def step(self, dt):
        n = max(1, int(dt / 0.003))
        h = dt / n
        for _ in range(n):
            a = self.k * (self.target - self.x) - self.c * self.v
            self.v += a * h
            self.x += self.v * h

    def moving(self):
        return abs(self.target - self.x) > 0.04 or abs(self.v) > 0.4

    def settle(self):
        self.x = self.target
        self.v = 0.0


def smooth(a):
    return a * a * (3.0 - 2.0 * a)


# =====================================================================
#                         Плеер (фоновый поток)
# =====================================================================
class MediaBackend(QObject):
    changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.loop = None
        self._key = None
        self._retry = 0
        self._have_thumb = False

    def start(self):
        if not HAVE_WINRT:
            print("[WinIsland] Управление плеером недоступно:", WINRT_ERROR)
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll())

    async def _read_thumb(self, info):
        try:
            ref = info.thumbnail
            if ref is None:
                return None
            stream = await ref.open_read_async()
            size = stream.size
            if not size:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            reader = DataReader.from_buffer(buf)
            data = bytearray(buf.length)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return None

    async def _poll(self):
        mgr = None
        while True:
            try:
                if mgr is None:
                    mgr = await MediaManager.request_async()
                s = mgr.get_current_session()
                if s is None:
                    self._key = None
                    self.changed.emit({"has": False})
                else:
                    info = await s.try_get_media_properties_async()
                    title = info.title or ""
                    artist = info.artist or ""
                    key = (title, artist, s.source_app_user_model_id)
                    if key != self._key:
                        self._key = key
                        self._retry = 0
                        self._have_thumb = False
                    thumb = None
                    if not self._have_thumb and self._retry < 8:
                        self._retry += 1
                        thumb = await self._read_thumb(info)
                        if thumb:
                            self._have_thumb = True

                    pb = s.get_playback_info()
                    status = getattr(pb.playback_status, "value", pb.playback_status)
                    playing = int(status) == 4          # 4 = Playing

                    tl = s.get_timeline_properties()
                    pos = tl.position.total_seconds()
                    dur = (tl.end_time - tl.start_time).total_seconds()
                    if playing:
                        try:
                            delta = (datetime.datetime.now(datetime.timezone.utc)
                                     - tl.last_updated_time).total_seconds()
                            if 0 <= delta < 43200:
                                pos += delta
                        except Exception:
                            pass
                    self.changed.emit({"has": True, "key": key, "title": title, "artist": artist,
                                       "playing": playing, "pos": pos, "dur": dur, "thumb": thumb})
            except Exception:
                mgr = None
            await asyncio.sleep(0.6)

    def command(self, name, arg=None):
        if not HAVE_WINRT or self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._cmd(name, arg), self.loop)

    async def _cmd(self, name, arg):
        try:
            mgr = await MediaManager.request_async()
            s = mgr.get_current_session()
            if s is None:
                return
            if name == "toggle":
                await s.try_toggle_play_pause_async()
            elif name == "next":
                await s.try_skip_next_async()
            elif name == "prev":
                await s.try_skip_previous_async()
            elif name == "seek":
                await s.try_change_playback_position_async(int(arg * 10_000_000))
        except Exception:
            pass


# =====================================================================
#                          Погода (Open-Meteo)
# =====================================================================
class Weather(QObject):
    updated = pyqtSignal(object)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.loc = None
        c = settings["city"]
        if c and len(c) == 3:
            self.loc = (c[0], c[1], c[2])
        self._wake = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def set_location(self, lat, lon, city):
        self.loc = (lat, lon, city)
        self.s["city"] = [lat, lon, city]
        self._wake.set()

    def use_auto(self):
        self.loc = None
        self.s["city"] = None
        self._wake.set()

    @staticmethod
    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "WinIsland/2.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _loop(self):
        while True:
            ok = False
            try:
                if self.loc is None:
                    g = self._get("http://ip-api.com/json/?fields=status,city,lat,lon&lang=ru")
                    if g.get("status") != "success":
                        raise RuntimeError("geo")
                    self.loc = (g["lat"], g["lon"], g.get("city", ""))
                lat, lon, city = self.loc
                w = self._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
                              "&current=temperature_2m,weather_code,is_day&timezone=auto" % (lat, lon))
                cur = w["current"]
                emoji, desc = weather_info(int(cur["weather_code"]), int(cur.get("is_day", 1)))
                self.updated.emit({"temp": float(cur["temperature_2m"]), "emoji": emoji,
                                   "desc": desc, "city": city})
                ok = True
            except Exception:
                pass
            self._wake.wait(900 if ok else 60)
            self._wake.clear()


# =====================================================================
#                 Уведомления Windows (Telegram и др.)
# =====================================================================
class NotifWatcher(QObject):
    """Читает базу уведомлений Windows и сообщает о новых."""
    arrived = pyqtSignal(object)

    def start(self):
        if sys.platform != "win32":
            return
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _read(db, tmp, last_id):
        for suf in ("", "-wal", "-shm"):
            src = db + suf
            dst = os.path.join(tmp, "w.db" + suf)
            if os.path.exists(src):
                shutil.copyfile(src, dst)
            elif os.path.exists(dst):
                os.remove(dst)
        con = sqlite3.connect(os.path.join(tmp, "w.db"))
        try:
            cur = con.cursor()
            if last_id is None:
                cur.execute("SELECT MAX(Id) FROM Notification")
                row = cur.fetchone()
                return (row[0] or 0), []
            handlers = {}
            try:
                cur.execute("SELECT RecordId, PrimaryId FROM NotificationHandler")
                handlers = {r[0]: r[1] for r in cur.fetchall()}
            except Exception:
                pass
            cur.execute("SELECT Id, HandlerId, Type, Payload FROM Notification WHERE Id > ? ORDER BY Id",
                        (last_id,))
            out = []
            new_last = last_id
            for nid, hid, typ, payload in cur.fetchall():
                new_last = max(new_last, nid)
                if str(typ).lower() != "toast" or payload is None:
                    continue
                try:
                    parsed = parse_toast(payload)
                except Exception:
                    parsed = None
                if not parsed:
                    continue
                title, body, img = parsed
                if not title and not body:
                    continue
                out.append({"app": app_label(handlers.get(hid, "")), "title": title,
                            "body": body, "icon": img})
            return new_last, out
        finally:
            con.close()

    def _run(self):
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Notifications")
        db = os.path.join(base, "wpndatabase.db")
        print("[WinIsland] база уведомлений Windows:", db,
              "— найдена" if os.path.exists(db) else "— НЕ найдена")
        tmp = tempfile.mkdtemp(prefix="winisland_")
        last_id = None
        last_sig = None
        warned = False
        while True:
            try:
                if os.path.exists(db):
                    sig = tuple(os.path.getmtime(db + s) if os.path.exists(db + s) else 0
                                for s in ("", "-wal"))
                    if sig != last_sig:
                        last_sig = sig
                        first = last_id is None
                        last_id, items = self._read(db, tmp, last_id)
                        if first:
                            print("[WinIsland] слежу за уведомлениями, последний номер:", last_id)
                        for it in items:
                            print("[WinIsland] уведомление:", it["app"], "|", it["title"])
                            self.arrived.emit(it)
            except Exception as ex:
                if not warned:
                    warned = True
                    print("[WinIsland] не удалось прочитать уведомления:", ex)
            time.sleep(0.7)


# =====================================================================
#                              Загрузки
# =====================================================================
class DownloadHub(QObject):
    """Собирает данные о загрузках из расширения браузера и из слежения за папкой."""
    ext_in = pyqtSignal(object)
    fs_in = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.items = {}
        self.last_ext = 0.0
        self.ext_in.connect(self._on_ext)
        self.fs_in.connect(self._on_fs)

    def ext_alive(self):
        return time.monotonic() - self.last_ext < 4.0

    def _upd(self, key, name, received, total, state, src, paused=False):
        now = time.monotonic()
        it = self.items.get(key)
        if it is None:
            it = {"key": key, "name": name, "received": received, "total": total, "state": state,
                  "speed": 0.0, "t": now, "src": src, "done_at": None, "paused": paused}
            self.items[key] = it
        else:
            if state == "active":
                dt = now - it["t"]
                if dt >= 0.25:
                    inst = max(0, received - it["received"]) / dt
                    it["speed"] = inst if it["speed"] == 0 else it["speed"] * 0.6 + inst * 0.4
                    it["t"] = now
                it["received"] = received
            else:
                it["received"] = max(it["received"], received)
            it["name"] = name
            it["total"] = total
            it["paused"] = paused
            it["state"] = state
        if state == "active":
            it["done_at"] = None
        else:
            it["speed"] = 0.0
            if it["done_at"] is None:
                it["done_at"] = now

    def _on_ext(self, items):
        self.last_ext = time.monotonic()
        for d in items:
            key = ("ext", d.get("browser", "b"), d.get("id"))
            st = d.get("state", "in_progress")
            if st == "in_progress":
                state = "active"
            elif st == "complete":
                state = "done"
            else:
                self.items.pop(key, None)
                continue
            self._upd(key, d.get("name") or "Файл", int(d.get("received") or 0),
                      d.get("total") or None, state, "ext", bool(d.get("paused")))
        self.changed.emit()

    def _on_fs(self, lst):
        ext_names = {it["name"] for it in self.items.values()
                     if it["src"] == "ext" and it["state"] == "active"}
        for d in lst:
            if self.ext_alive() and (partial_base(d["path"]) is not None or d["name"] in ext_names):
                continue
            self._upd(("fs", d["path"]), d["name"], int(d["size"]), None,
                      "active" if d["state"] == "active" else "done", "fs")
        self.changed.emit()

    def snapshot(self):
        now = time.monotonic()
        active, fin = [], []
        for k, it in list(self.items.items()):
            if it["state"] == "active":
                if now - it["t"] > 15:
                    del self.items[k]
                else:
                    active.append(it)
            else:
                if now - (it["done_at"] or now) > 30:
                    del self.items[k]
                elif now - (it["done_at"] or now) < 10:
                    fin.append(it)
        fin.sort(key=lambda i: i["done_at"] or 0)
        return active, fin


class FsWatcher:
    """Каждую секунду смотрит папку «Загрузки»: какие файлы растут прямо сейчас."""

    def __init__(self, hub, roots):
        self.hub = hub
        self.roots = roots
        self.known = {}
        self.first = True

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _list(self):
        out = {}
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            try:
                top = list(os.scandir(root))
            except OSError:
                continue
            for e in top:
                try:
                    if e.is_file(follow_symlinks=False):
                        out[e.path] = e.stat().st_size
                    elif e.is_dir(follow_symlinks=False):
                        for e2 in list(os.scandir(e.path)):
                            if e2.is_file(follow_symlinks=False):
                                out[e2.path] = e2.stat().st_size
                except OSError:
                    continue
        return out

    def scan_once(self, now=None):
        now = time.time() if now is None else now
        cur = self._list()
        out = []
        if self.first:
            for p, sz in cur.items():
                self.known[p] = {"size": sz, "grow": 0, "last": 0.0, "active": False,
                                 "partial": partial_base(p) is not None}
            self.first = False
            return out
        for p, sz in cur.items():
            st = self.known.get(p)
            if st is None:
                part = partial_base(p) is not None
                st = {"size": sz, "grow": 1 if sz > 0 else 0, "last": now, "active": part, "partial": part}
                self.known[p] = st
            elif sz > st["size"]:
                st["grow"] += 1
                st["last"] = now
                st["size"] = sz
                if st["partial"] or (st["grow"] >= 2 and sz >= 256 * 1024):
                    st["active"] = True
            elif st["active"] and not st["partial"] and now - st["last"] > 3.0:
                st["active"] = False
                out.append({"path": p, "name": display_name(p), "size": sz, "state": "done"})
                continue
            else:
                st["size"] = sz
            if st["active"]:
                out.append({"path": p, "name": display_name(p), "size": sz, "state": "active"})
        for p in list(self.known):
            if p not in cur:
                st = self.known.pop(p)
                if st["active"] and st["partial"]:
                    out.append({"path": p, "name": display_name(p), "size": st["size"], "state": "done"})
        return out

    def _run(self):
        while True:
            try:
                out = self.scan_once()
                if out:
                    self.hub.fs_in.emit(out)
            except Exception:
                pass
            time.sleep(1.0)


def start_download_server(hub):
    """Локальный сервер: сюда расширение браузера присылает прогресс загрузок."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code=200, body=b"ok"):
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self):
            self._reply(204, b"")

        def do_GET(self):
            self._reply(200, b"WinIsland")

        def do_POST(self):
            origin = self.headers.get("Origin", "")
            if origin and not (origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")):
                self._reply(403, b"no")
                return
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                data = json.loads(self.rfile.read(min(n, 1_000_000)).decode("utf-8"))
                browser = str(data.get("browser", "chromium"))
                items = []
                for it in data.get("items", []):
                    items.append({"browser": browser, "id": it.get("id"), "name": str(it.get("name", "")),
                                  "received": int(it.get("received") or 0), "total": int(it.get("total") or 0),
                                  "state": str(it.get("state", "in_progress")), "paused": bool(it.get("paused"))})
                if items:
                    hub.ext_in.emit(items)
                self._reply(200)
            except Exception:
                self._reply(400, b"bad")

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", DL_PORT), Handler)
    except Exception as ex:
        print("[WinIsland] сервер загрузок не запущен:", ex)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# =====================================================================
#              Системные настройки: Wi-Fi, Bluetooth, звук, яркость
# =====================================================================
class SystemControls(QObject):
    changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.loop = None
        self._saved = []

    def start(self):
        if not HAVE_RADIO:
            print("[WinIsland] Wi-Fi/Bluetooth недоступны:", RADIO_ERROR)
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll())

    @staticmethod
    def _val(x):
        return int(getattr(x, "value", x))

    async def _poll(self):
        allowed = False
        try:
            st = await Radio.request_access_async()
            allowed = self._val(st) == 1
        except Exception:
            pass
        while True:
            try:
                radios = await Radio.get_radios_async()
                wifi = bt = None
                any_on = False
                for r in radios:
                    kind = self._val(r.kind)
                    on = self._val(r.state) == 1
                    if kind == 1:
                        wifi = on
                    elif kind == 3:
                        bt = on
                    if on and kind in (1, 2, 3):
                        any_on = True
                self.changed.emit({"wifi": wifi, "bt": bt, "any_on": any_on, "allowed": allowed})
            except Exception:
                pass
            await asyncio.sleep(2.5)

    def _submit(self, coro):
        if self.loop is None:
            coro.close()
            return
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _set(self, kind, on):
        try:
            for r in await Radio.get_radios_async():
                if self._val(r.kind) == kind:
                    await r.set_state_async(RadioState.ON if on else RadioState.OFF)
        except Exception:
            pass

    def set_radio(self, kind, on):
        if HAVE_RADIO:
            self._submit(self._set(kind, on))

    async def _airplane(self, enable):
        try:
            radios = await Radio.get_radios_async()
            if enable:
                self._saved = [self._val(r.kind) for r in radios
                               if self._val(r.kind) in (1, 2, 3) and self._val(r.state) == 1]
                for r in radios:
                    if self._val(r.kind) in (1, 2, 3):
                        await r.set_state_async(RadioState.OFF)
            else:
                want = self._saved or [1, 3]
                for r in radios:
                    if self._val(r.kind) in want:
                        await r.set_state_async(RadioState.ON)
        except Exception:
            pass

    def set_airplane(self, enable):
        if HAVE_RADIO:
            self._submit(self._airplane(enable))


class VolumeCtl:
    def __init__(self):
        self.ep = None
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER
            dev = AudioUtilities.GetSpeakers()
            try:
                self.ep = dev.EndpointVolume
            except AttributeError:
                iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self.ep = cast(iface, POINTER(IAudioEndpointVolume))
        except Exception as ex:
            print("[WinIsland] громкость недоступна (нужен pycaw):", ex)

    @property
    def available(self):
        return self.ep is not None

    def get(self):
        try:
            return float(self.ep.GetMasterVolumeLevelScalar()), bool(self.ep.GetMute())
        except Exception:
            return None, None

    def set(self, v):
        try:
            self.ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(v))), None)
        except Exception:
            pass

    def set_mute(self, m):
        try:
            self.ep.SetMute(1 if m else 0, None)
        except Exception:
            pass


class BrightnessCtl:
    def __init__(self):
        self.available = False
        self.value = None
        self._pending = None
        self._busy = False
        self._lock = threading.Lock()

    def refresh(self):
        threading.Thread(target=self._get, daemon=True).start()

    def _get(self):
        try:
            import screen_brightness_control as sbc
            v = sbc.get_brightness()
            if isinstance(v, (list, tuple)):
                v = v[0]
            self.value = int(v)
            self.available = True
        except Exception:
            pass

    def set(self, v):
        self.value = int(v)
        with self._lock:
            self._pending = int(v)
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._apply, daemon=True).start()

    def _apply(self):
        try:
            import screen_brightness_control as sbc
            while True:
                with self._lock:
                    v = self._pending
                    self._pending = None
                if v is None:
                    break
                try:
                    sbc.set_brightness(v)
                except Exception:
                    pass
        finally:
            with self._lock:
                self._busy = False


# =====================================================================
#                 Всплывающие окна (город, заметки)
# =====================================================================
POPUP_CSS = """
    QLabel{color:white;font-family:'Segoe UI Variable Display','Segoe UI';font-size:17px;font-weight:600;}
    QLabel#hint{color:#8e8e93;font-size:13px;font-weight:400;}
    QLineEdit, QTextEdit{background:#1c1c1e;color:white;border:1px solid #2c2c2e;border-radius:14px;
              padding:10px 12px;font-size:15px;selection-background-color:#0a84ff;
              font-family:'Segoe UI Variable Display','Segoe UI';}
    QListWidget{background:transparent;border:none;color:white;font-size:14px;outline:0;
                font-family:'Segoe UI Variable Display','Segoe UI';}
    QListWidget::item{padding:9px 12px;border-radius:10px;}
    QListWidget::item:hover{background:#1c1c1e;}
    QListWidget::item:selected{background:#0a84ff;color:white;}
    QPushButton{background:#1c1c1e;color:#0a84ff;border:none;border-radius:14px;
                padding:10px;font-size:14px;font-weight:600;
                font-family:'Segoe UI Variable Display','Segoe UI';}
    QPushButton:hover{background:#2c2c2e;}
    QPushButton#x{color:#8e8e93;padding:2px 8px;border-radius:12px;font-size:13px;}
    QPushButton#danger{color:#ff453a;}
    QPushButton#primary{background:#0a84ff;color:white;}
    QPushButton#primary:hover{background:#3d9bff;}
"""


class PopupCard(QWidget):
    """Тёмная карточка с плавным появлением (для выбора города и заметок)."""

    def __init__(self, w, h):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(w, h)
        self.setStyleSheet(POPUP_CSS)
        self._anim = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 28, 28)
        p.fillPath(path, QColor(0, 0, 0))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(path)
        p.end()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.fade_out()
        else:
            super().keyPressEvent(e)

    def on_open(self):
        pass

    def open_animated(self, cx, top):
        scr = QGuiApplication.primaryScreen().geometry()
        x = max(scr.x() + 8, min(cx - self.width() // 2, scr.x() + scr.width() - self.width() - 8))
        y = top
        self.on_open()
        self.move(x, y - 18)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        a1 = QPropertyAnimation(self, b"pos")
        a1.setDuration(420)
        a1.setStartValue(QPoint(x, y - 18))
        a1.setEndValue(QPoint(x, y))
        a1.setEasingCurve(QEasingCurve.Type.OutCubic)
        a2 = QPropertyAnimation(self, b"windowOpacity")
        a2.setDuration(280)
        a2.setStartValue(0.0)
        a2.setEndValue(1.0)
        g = QParallelAnimationGroup(self)
        g.addAnimation(a1)
        g.addAnimation(a2)
        self._anim = g
        g.start()

    def fade_out(self):
        a = QPropertyAnimation(self, b"windowOpacity")
        a.setDuration(200)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.finished.connect(self.hide)
        self._anim = a
        a.start()


class CityPicker(PopupCard):
    chosen = pyqtSignal(float, float, str)
    auto = pyqtSignal()
    results_ready = pyqtSignal(object, str)

    def __init__(self):
        super().__init__(380, 380)
        self._q = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(QLabel("Город для погоды"))
        head.addStretch(1)
        xb = QPushButton("✕")
        xb.setObjectName("x")
        xb.clicked.connect(self.fade_out)
        head.addWidget(xb)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Начните вводить название города…")
        lay.addWidget(self.search)
        self.hint = QLabel("Например: Казань, Минск, Алматы")
        self.hint.setObjectName("hint")
        lay.addWidget(self.hint)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        auto_btn = QPushButton("Определять автоматически по IP")
        auto_btn.clicked.connect(self._on_auto)
        lay.addWidget(auto_btn)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._start_search)
        self.search.textChanged.connect(lambda _t: self._debounce.start(320))
        self.search.returnPressed.connect(self._pick_first)
        self.list.itemClicked.connect(self._pick)
        self.list.itemActivated.connect(self._pick)
        self.results_ready.connect(self._on_results)

    def on_open(self):
        self.search.clear()
        self.list.clear()
        self.hint.setText("Например: Казань, Минск, Алматы")
        QTimer.singleShot(60, self.search.setFocus)

    def _start_search(self):
        q = self.search.text().strip()
        self._q = q
        if len(q) < 2:
            self.list.clear()
            self.hint.setText("Введите минимум 2 буквы")
            return
        self.hint.setText("Ищу…")
        threading.Thread(target=self._fetch, args=(q,), daemon=True).start()

    def _fetch(self, q):
        try:
            url = ("https://geocoding-api.open-meteo.com/v1/search?name=%s&count=8&language=ru&format=json"
                   % urllib.parse.quote(q))
            req = urllib.request.Request(url, headers={"User-Agent": "WinIsland/2.1"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            self.results_ready.emit(data.get("results") or [], q)
        except Exception:
            self.results_ready.emit(None, q)

    def _on_results(self, res, q):
        if q != self._q:
            return
        self.list.clear()
        if res is None:
            self.hint.setText("Нет соединения с интернетом")
            return
        if not res:
            self.hint.setText("Ничего не найдено")
            return
        self.hint.setText("Выберите город:")
        for r in res:
            sub = ", ".join([x for x in (r.get("admin1"), r.get("country")) if x])
            it = QListWidgetItem(r.get("name", "") + ("\n" + sub if sub else ""))
            it.setData(Qt.ItemDataRole.UserRole, (float(r["latitude"]), float(r["longitude"]), r.get("name", "")))
            self.list.addItem(it)
        self.list.setCurrentRow(0)

    def _pick_first(self):
        if self.list.count():
            self._pick(self.list.item(0))

    def _pick(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.chosen.emit(d[0], d[1], d[2])
            self.fade_out()

    def _on_auto(self):
        self.auto.emit()
        self.fade_out()


class NoteEditor(PopupCard):
    saved = pyqtSignal(int, str)
    deleted = pyqtSignal(int)

    def __init__(self):
        super().__init__(380, 320)
        self._idx = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel("Новая заметка")
        head.addWidget(self.title)
        head.addStretch(1)
        xb = QPushButton("✕")
        xb.setObjectName("x")
        xb.clicked.connect(self.fade_out)
        head.addWidget(xb)
        lay.addLayout(head)
        self.edit = QTextEdit()
        self.edit.setPlaceholderText("Что записать?")
        self.edit.setAcceptRichText(False)
        lay.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.btn_del = QPushButton("Удалить")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        ok = QPushButton("Готово")
        ok.setObjectName("primary")
        ok.clicked.connect(self._save)
        row.addWidget(ok)
        lay.addLayout(row)

    def open_for(self, idx, text, cx, top):
        self._idx = idx
        self.title.setText("Заметка" if idx >= 0 else "Новая заметка")
        self.edit.setPlainText(text)
        self.btn_del.setVisible(idx >= 0)
        self.open_animated(cx, top)

    def on_open(self):
        QTimer.singleShot(60, self.edit.setFocus)

    def _save(self):
        self.saved.emit(self._idx, self.edit.toPlainText())
        self.fade_out()

    def _delete(self):
        self.deleted.emit(self._idx)
        self.fade_out()


# =====================================================================
#                              Остров
# =====================================================================
class Island(QWidget):
    def __init__(self, settings, media, weather, hub, notifier, sysctl, vol, bri):
        super().__init__()
        self.s = settings
        self.media = media
        self.wsrc = weather
        self.hub = hub
        self.sysctl = sysctl
        self.vol = vol
        self.bri = bri
        self.menu = None
        self.picker = None
        self.note_editor = None
        self.clock = ClockTools()
        self.notes = NoteStore()
        self.notes_off = 0
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self.mode = "compact"      # compact | expanded | drop
        self.tab = "media"
        self.tab_dir = 1
        self.tab_alpha = {t: 0.0 for t in TABS}
        self.tab_alpha["media"] = 1.0
        self.hl = Spring(self._tab_rects()[0].left(), 260.0, 24.0)
        self.seg = Spring(0.0, 260.0, 24.0)
        self.tm_disp = 0.0
        self.files = []
        self._icons = {}
        self._ext_icons = {}
        self._icon_provider = QFileIconProvider()
        self.wx = None
        self.art = None
        self.accent = QColor(255, 255, 255)
        self.m = {"has": False, "title": "", "artist": "", "playing": False,
                  "pos": 0.0, "dur": 0.0, "stamp": time.monotonic(), "key": None}

        # уведомления
        self.notif_queue = []
        self.notif_cur = None
        self.notif_pm = None
        self.notif_until = 0.0
        self._test_seen = False
        # загрузки
        self.dl_overlay = False
        self.dl_view = None
        self.dl_hold_until = None
        self.dl_disp = 0.0
        # быстрые настройки
        self.sysst = {"wifi": None, "bt": None, "any_on": True, "allowed": False}
        self.volv = None
        self.muted = False
        self._slider = None
        self._slider_rect = None
        self._vol_tick = 0

        self.sw = Spring(W_IDLE)
        self.sh = Spring(H_IDLE)
        self.alpha = {k: 0.0 for k in LAYERS}
        self.alpha["idle"] = 1.0
        self.layer_targets = {k: False for k in LAYERS}

        self._press = None
        self._dragging = False
        self._in_since = None
        self._out_since = None
        self._fs_tick = 0
        self._fs_hidden = False
        self._fade_anim = None

        scr = QGuiApplication.primaryScreen()
        try:
            rate = float(scr.refreshRate()) or 60.0
        except Exception:
            rate = 60.0
        self._frame_ms = max(7, min(16, int(1000.0 / min(rate, 120.0))))

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._last = time.monotonic()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self.update)
        self._clock_timer.start(1000)

        self._hover = QTimer(self)
        self._hover.timeout.connect(self._hover_poll)
        self._hover.start(80)

        media.changed.connect(self.on_media)
        weather.updated.connect(self.on_weather)
        notifier.arrived.connect(self.push_notification)
        hub.changed.connect(self._kick)
        sysctl.changed.connect(self.on_sys)

        self._retarget()
        self._apply_geometry()

    # ------------------------------------------------------------------
    # вид и размеры
    # ------------------------------------------------------------------
    @property
    def notch(self):
        return self.s["style"] == "notch"

    def apply_style(self):
        self.sw.x = self.sw.target
        self._retarget()
        self._apply_geometry()
        self._kick()
        self.update()

    def _display(self):
        if self.mode == "drop":
            return "drop"
        if self.notif_cur is not None:
            return "notif"
        if self.mode == "expanded":
            return "expanded"
        if self.dl_overlay and self.dl_view:
            return "download"
        return "compact"

    def _dl_h(self):
        return DL_H if (self.dl_overlay and self.dl_view) else 0

    def _tab_h(self, tab):
        if tab == "media":
            return H_EXP + (SHELF_H if self.files else 0)
        if tab == "quick":
            return T0 + 88 + 36 + 18 + (48 if self.bri.available else 0)
        if tab == "timer":
            return T0 + 196
        rows = max(1, min(4, len(self.notes.items)))
        return T0 + rows * 44 + 6 + 38 + 16

    def _target_size(self, disp):
        if disp == "expanded":
            return W_EXP, self._tab_h(self.tab) + self._dl_h()
        if disp == "drop":
            return W_DROP, H_DROP
        if disp == "notif":
            return W_NOTIF, H_NOTIF
        if disp == "download":
            return W_DL, H_DL
        if self.m["has"]:
            return W_MEDIA, H_MEDIA
        if self.clock.active():
            return W_CTIMER, H_CTIMER
        return (W_IDLE_NOTCH if self.notch else W_IDLE), H_IDLE

    def _retarget(self):
        disp = self._display()
        w, h = self._target_size(disp)
        self.sw.target = w
        self.sh.target = h
        self.layer_targets = {k: False for k in LAYERS}
        if disp == "compact":
            if self.m["has"]:
                key = "media"
            elif self.clock.active():
                key = "ctimer"
            else:
                key = "idle"
            self.layer_targets[key] = True
        else:
            self.layer_targets[disp] = True

    def set_mode(self, mode):
        if mode == "compact" and self.mode != "compact":
            self.tab_dir = -1
            self.tab = "media"
            self._slider = None
        self.mode = mode
        self._retarget()
        self._kick()

    def set_tab(self, name):
        if name == self.tab:
            return
        self.tab_dir = 1 if TABS.index(name) > TABS.index(self.tab) else -1
        self.tab = name
        if name == "quick":
            self.bri.refresh()
            self._refresh_volume()
        self._retarget()
        self._kick()

    def _kick(self):
        self._retarget()
        if not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start(self._frame_ms)

    # ------------------------------------------------------------------
    # кадр анимации
    # ------------------------------------------------------------------
    def _update_downloads(self, now):
        if not self.s["downloads"]:
            self.dl_overlay = False
            self.dl_view = None
            return
        active, fin = self.hub.snapshot()
        if active:
            self.dl_overlay = True
            self.dl_hold_until = None
            self.dl_view = self._summarize(active, False)
        elif self.dl_overlay:
            if self.dl_hold_until is None:
                if fin:
                    self.dl_view = self._summarize([fin[-1]], True)
                    self.dl_hold_until = now + 2.6
                else:
                    self.dl_overlay = False
                    self.dl_view = None
            elif now > self.dl_hold_until:
                self.dl_overlay = False
                self.dl_view = None
                self.dl_hold_until = None

    def _summarize(self, items, done):
        if done:
            it = items[0]
            return {"name": it["name"], "sub": "Загрузка завершена · " + fmt_size(it["received"]),
                    "prog": 1.0, "done": True}
        n = len(items)
        recv = sum(i["received"] for i in items)
        speed = sum(i["speed"] for i in items)
        known = all(i["total"] for i in items)
        total = sum(i["total"] or 0 for i in items)
        if n == 1:
            name = items[0]["name"]
        else:
            name = "Загрузки: %d файла" % n
        parts = []
        if items[0].get("paused") and n == 1:
            parts.append("Пауза")
        parts.append((fmt_size(recv) + " из " + fmt_size(total)) if known and total else fmt_size(recv))
        if speed > 2048:
            parts.append(fmt_size(speed) + "/с")
        prog = (recv / float(total)) if (known and total) else None
        if prog is not None:
            prog = min(1.0, max(0.0, prog))
        return {"name": name, "sub": " · ".join(parts), "prog": prog, "done": False}

    def _timer_finished(self):
        beep_alarm()
        self.push_notification({"app": "Таймер", "title": "Таймер завершён", "body": "Время вышло",
                                "icon": None, "kind": "timer"}, force=True)

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.05)
        self._last = now

        if self.clock.t_check(now):
            self._timer_finished()
        if self.notif_cur is not None and now > self.notif_until:
            self._next_notif()
        self._update_downloads(now)
        if self.dl_view:
            target = self.dl_view["prog"] if self.dl_view["prog"] is not None else self.dl_disp
            self.dl_disp += (target - self.dl_disp) * min(1.0, dt * 9.0)
            if self.dl_view["done"]:
                self.dl_disp = min(1.0, self.dl_disp + dt * 3.0)
        else:
            self.dl_disp = 0.0

        # кольцо таймера плавно «доезжает» до нужного значения
        ck = self.clock
        if ck.t_state == "idle":
            tm_target = 0.0
        else:
            tm_target = ck.t_remaining(now) / max(1.0, ck.t_dur)
        self.tm_disp += (tm_target - self.tm_disp) * min(1.0, dt * 10.0)

        self._retarget()
        busy = False
        for sp in (self.sw, self.sh):
            sp.step(dt)
            if sp.moving():
                busy = True
            else:
                sp.settle()
        self.hl.target = self._tab_rects()[TABS.index(self.tab)].left()
        self.seg.target = 0.0 if ck.mode == "timer" else 1.0
        for sp in (self.hl, self.seg):
            sp.step(dt)
            if sp.moving():
                busy = True
            else:
                sp.settle()
        for k in LAYERS:
            t = 1.0 if self.layer_targets[k] else 0.0
            a = self.alpha[k]
            if a != t:
                busy = True
                a = min(t, a + dt * 6.5) if t > a else max(t, a - dt * 8.0)
                self.alpha[k] = a
        for k in TABS:
            t = 1.0 if k == self.tab else 0.0
            a = self.tab_alpha[k]
            if a != t:
                busy = True
                a = min(t, a + dt * 7.5) if t > a else max(t, a - dt * 9.0)
                self.tab_alpha[k] = a
        disp = self._display()
        dynamic = ((self.m["playing"] and disp in ("compact", "expanded"))
                   or (self.dl_view is not None and disp in ("download", "expanded"))
                   or (disp == "expanded" and self.tab == "timer" and ck.active())
                   or disp == "notif")
        slow = ck.active() and not dynamic
        self._apply_geometry()
        self.update()
        if not busy and not dynamic and not slow:
            self._timer.stop()
        else:
            self._timer.setInterval(self._frame_ms if busy else (33 if dynamic else 250))

    def _apply_geometry(self):
        scr = QGuiApplication.primaryScreen().geometry()
        f = FLARE if self.notch else 0
        w = int(round(self.sw.x / 2.0)) * 2
        w = max(20, w) + 2 * f
        h = max(10, int(round(self.sh.x)))
        x = scr.x() + (scr.width() - w) // 2
        y = scr.y() + (0 if self.notch else TOP_MARGIN)
        g = self.geometry()
        if g.x() != x or g.y() != y or g.width() != w or g.height() != h:
            self.setGeometry(x, y, w, h)

    def _fade_window(self, show):
        """Плавно показывает или прячет весь остров."""
        if self._fade_anim is not None:
            self._fade_anim.stop()
        if show:
            self.setWindowOpacity(0.0)
            self.show()
        a = QPropertyAnimation(self, b"windowOpacity")
        a.setDuration(260)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(1.0 if show else 0.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not show:
            a.finished.connect(self.hide)
        self._fade_anim = a
        a.start()

    # ------------------------------------------------------------------
    # данные
    # ------------------------------------------------------------------
    def on_media(self, d):
        m = self.m
        if not d.get("has"):
            m.update(has=False, playing=False, key=None)
            self.art = None
        else:
            if d["key"] != m["key"]:
                self.art = None
                self.accent = QColor(255, 255, 255)
            if d.get("thumb"):
                self._set_art(d["thumb"])
            m.update(has=True, title=d["title"], artist=d["artist"], key=d["key"],
                     playing=d["playing"], pos=d["pos"], dur=d["dur"], stamp=time.monotonic())
        self._kick()

    def on_weather(self, d):
        self.wx = d
        self.update()

    def on_sys(self, d):
        self.sysst = d
        self.update()

    def _set_art(self, data):
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        side = min(pm.width(), pm.height())
        if side <= 0:
            return
        pm = pm.copy((pm.width() - side) // 2, (pm.height() - side) // 2, side, side)
        img = pm.toImage().scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        h, s, v, _a = img.pixelColor(0, 0).getHsvF()
        if h < 0:
            h, s = 0.0, 0.0
        self.accent = QColor.fromHsvF(h, min(1.0, s * 1.15), max(0.8, v))
        self.art = pm.scaled(180, 180, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)

    def cur_pos(self):
        m = self.m
        p = m["pos"]
        if m["playing"]:
            p += time.monotonic() - m["stamp"]
        if m["dur"] > 1:
            p = min(max(p, 0.0), m["dur"])
        return p

    # ------------------------------------------------------------------
    # уведомления
    # ------------------------------------------------------------------
    def push_notification(self, n, force=False):
        if str(n.get("title", "")).startswith("WinIsland тест"):
            self._test_seen = True
        if not force and not self.s["notifications"]:
            return
        self.notif_queue.append(n)
        if self.notif_cur is None:
            self._next_notif()

    def _next_notif(self):
        if self.notif_queue:
            n = self.notif_queue.pop(0)
            self.notif_cur = n
            self.notif_pm = None
            if n.get("icon"):
                pm = QPixmap(n["icon"])
                if not pm.isNull():
                    self.notif_pm = pm
            extra = 4.0 if n.get("kind") == "timer" else 0.0
            self.notif_until = time.monotonic() + 5.5 + extra + min(3.0, len(n.get("body", "")) / 60.0)
        else:
            self.notif_cur = None
            self.notif_pm = None
        self._kick()

    def test_notification(self):
        """Отправляет тестовое уведомление Windows и проверяет, дошло ли оно до острова."""
        self._test_seen = False
        send_test_toast()
        QTimer.singleShot(7000, self._test_check)

    def _test_check(self):
        if not self._test_seen:
            self.push_notification(
                {"app": "WinIsland", "title": "Тест не дошёл до острова",
                 "body": "Telegram: включите системные уведомления. Проверьте «Не беспокоить».",
                 "icon": None, "kind": "info"}, force=True)

    # ------------------------------------------------------------------
    # файлы
    # ------------------------------------------------------------------
    def add_files(self, paths):
        for p in paths:
            p = os.path.normpath(p)
            if p in self.files:
                self.files.remove(p)
            self.files.append(p)

    def clear_files(self):
        self.files = []
        self._kick()

    def _shown(self):
        n = len(self.files)
        off = max(0, n - 5)
        return [(off + i, self.files[off + i]) for i in range(min(5, n))]

    def _icon(self, path):
        pm = self._icons.get(path)
        if pm is None:
            try:
                pm = self._icon_provider.icon(QFileInfo(path)).pixmap(64, 64)
            except Exception:
                pm = QPixmap()
            self._icons[path] = pm
        return pm

    def _icon_for(self, name):
        ext = os.path.splitext(name)[1].lower() or "?"
        pm = self._ext_icons.get(ext)
        if pm is None:
            try:
                fake = "C:/winisland_x/file" + (ext if ext != "?" else "")
                pm = self._icon_provider.icon(QFileInfo(fake)).pixmap(64, 64)
            except Exception:
                pm = QPixmap()
            self._ext_icons[ext] = pm
        return pm

    # ------------------------------------------------------------------
    # громкость
    # ------------------------------------------------------------------
    def _refresh_volume(self):
        if self.vol.available:
            v, mu = self.vol.get()
            if v is not None:
                if self._slider != "vol":
                    self.volv = v
                self.muted = bool(mu)

    # ------------------------------------------------------------------
    # полноэкранный режим, наведение мыши
    # ------------------------------------------------------------------
    def _foreground_fullscreen(self):
        """True, если на ГЛАВНОМ мониторе открыто настоящее полноэкранное окно (не просто развёрнутое)."""
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value in ("WorkerW", "Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                return False
            style = user32.GetWindowLongW(hwnd, -16)
            if (style & 0x00C00000) == 0x00C00000:      # есть заголовок окна — обычное окно
                return False
            mon = user32.MonitorFromWindow(hwnd, 2)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return False
            if not (mi.dwFlags & 1):                     # не главный монитор
                return False
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            m = mi.rcMonitor
            return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
        except Exception:
            return False

    def _popup_open(self):
        for w in (self.picker, self.note_editor):
            if w is not None and w.isVisible():
                return True
        return False

    def _hover_poll(self):
        self._fs_tick += 1
        if self._fs_tick >= 6:
            self._fs_tick = 0
            want_hide = bool(self.s["hide_fullscreen"]) and self._foreground_fullscreen()
            if want_hide and not self._fs_hidden:
                self._fs_hidden = True
                self._fade_window(False)
            elif not want_hide and self._fs_hidden:
                self._fs_hidden = False
                self._fade_window(True)
        if not self.isVisible():
            return
        now = time.monotonic()
        inside = self.frameGeometry().contains(QCursor.pos())

        if self.mode == "expanded" and self.tab == "quick" and self._slider is None:
            self._vol_tick += 1
            if self._vol_tick >= 8:
                self._vol_tick = 0
                self._refresh_volume()
                self.update()

        if self.notif_cur is not None and inside:
            self.notif_until = max(self.notif_until, now + 1.5)

        if self._dragging or self.mode == "drop":
            return
        popup = self._popup_open()
        if inside:
            self._out_since = None
            if self.mode == "compact":
                if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton:
                    self._in_since = None
                elif self._in_since is None:
                    self._in_since = now
                elif now - self._in_since > HOVER_OPEN_DELAY:
                    self._in_since = None
                    if self.notif_cur is None:
                        self.set_mode("expanded")
        else:
            self._in_since = None
            if self.mode == "expanded" and not popup:
                if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton:
                    self._out_since = None
                elif self._out_since is None:
                    self._out_since = now
                elif now - self._out_since > float(self.s["close_delay"]):
                    self._out_since = None
                    self.set_mode("compact")
            else:
                self._out_since = None

    # ------------------------------------------------------------------
    # раскладки (координаты внутри блока шириной W_EXP)
    # ------------------------------------------------------------------
    def _tab_rects(self):
        x0 = (W_EXP - 210) / 2.0 + 4
        return [QRectF(x0 + i * 52, 13, 46, 30) for i in range(4)]

    def _mlayout(self):
        W = W_EXP
        T = T0
        L = {}
        L["art"] = QRectF(22, T, 56, 56)
        L["title"] = QRectF(92, T + 2, W - 92 - 22 - 30, 22)
        L["artist"] = QRectF(92, T + 28, W - 92 - 22, 20)
        L["eq_x"] = W - 22 - 19
        L["eq_cy"] = T + 13
        L["bar"] = QRectF(22, T + 68, W - 44, 5)
        L["t_left"] = QRectF(22, T + 76, 90, 14)
        L["t_right"] = QRectF(W - 22 - 90, T + 76, 90, 14)
        cy = T + 102
        L["prev"] = QRectF(W / 2 - 80 - 18, cy - 18, 36, 36)
        L["play"] = QRectF(W / 2 - 22, cy - 22, 44, 44)
        L["next"] = QRectF(W / 2 + 80 - 18, cy - 18, 36, 36)
        L["div1"] = T + 126
        L["time"] = QRectF(22, T + 132, 190, 30)
        L["date"] = QRectF(22, T + 162, 190, 16)
        L["weather"] = QRectF(W - 22 - 200, T + 128, 200, 56)
        if self.files:
            y = H_EXP
            L["div2"] = y
            L["shelf_title"] = QRectF(22, y + 8, 220, 16)
            L["clear"] = QRectF(W - 22 - 90, y + 8, 90, 16)
            L["chips"] = [QRectF(22 + i * 76, y + 30, 72, 56) for i in range(len(self._shown()))]
        return L

    def _qlayout(self):
        W = W_EXP
        L = {}
        L["tiles"] = [QRectF(18 + i * 104, T0, 92, 72) for i in range(4)]
        L["vol"] = QRectF(22, T0 + 88, W - 44, 36)
        if self.bri.available:
            L["bri"] = QRectF(22, T0 + 136, W - 44, 36)
        return L

    def _tlayout(self):
        W = W_EXP
        T = T0
        L = {}
        sx = (W - 220) / 2.0
        L["seg"] = QRectF(sx, T, 220, 30)
        L["seg_t"] = QRectF(sx, T, 110, 30)
        L["seg_s"] = QRectF(sx + 110, T, 110, 30)
        cy = T + 92
        L["ring_c"] = QPointF(W / 2.0, cy)
        L["ring_r"] = 50.0
        L["btn_l"] = QRectF(36, cy - 30, 60, 60)
        L["btn_r"] = QRectF(W - 36 - 60, cy - 30, 60, 60)
        labels = [("−1 мин", ("adj", -1)), ("5 мин", ("set", 5)), ("10 мин", ("set", 10)),
                  ("15 мин", ("set", 15)), ("30 мин", ("set", 30)), ("+1 мин", ("adj", 1))]
        cw = 58.0
        gap = (W - 44 - 6 * cw) / 5.0
        L["chips"] = [(lab, act, QRectF(22 + i * (cw + gap), T + 152, cw, 30))
                      for i, (lab, act) in enumerate(labels)]
        L["info"] = QRectF(22, T + 152, W - 44, 30)
        return L

    def _nlayout(self):
        W = W_EXP
        vis = self.notes.rev()[self.notes_off:self.notes_off + 4]
        rows = max(1, len(vis))
        L = {"vis": vis}
        L["rows"] = [QRectF(22, T0 + i * 44, W - 44, 38) for i in range(len(vis))]
        L["xs"] = [QRectF(W - 22 - 34, r.top() + 4, 30, 30) for r in L["rows"]]
        L["empty"] = QRectF(22, T0, W - 44, 38)
        L["new"] = QRectF(22, T0 + rows * 44 + 6, W - 44, 38)
        return L

    def _body_pt(self, pos):
        f = FLARE if self.notch else 0
        bw = self.width() - 2 * f
        ox = (bw - W_EXP) / 2.0
        return QPointF(pos.x() - f - ox, pos.y())

    def _hit_media(self, pt):
        L = self._mlayout()
        for k in ("prev", "play", "next"):
            if L[k].adjusted(-8, -8, 8, 8).contains(pt):
                return (k,)
        if L["bar"].adjusted(-6, -12, 6, 12).contains(pt):
            frac = (pt.x() - L["bar"].left()) / L["bar"].width()
            return ("bar", min(1.0, max(0.0, frac)))
        if self.files:
            if L["clear"].contains(pt):
                return ("clear",)
            for i, r in enumerate(L["chips"]):
                if r.contains(pt):
                    return ("chip", self._shown()[i][0])
        if L["weather"].contains(pt):
            return ("weather",)
        return None

    def _hit_quick(self, pt):
        L = self._qlayout()
        for k, r in zip(("wifi", "bt", "air", "mute"), L["tiles"]):
            if r.contains(pt):
                return ("tile", k)
        if L["vol"].adjusted(-4, -6, 4, 6).contains(pt):
            return ("slider", "vol", L["vol"])
        if "bri" in L and L["bri"].adjusted(-4, -6, 4, 6).contains(pt):
            return ("slider", "bri", L["bri"])
        return None

    def _hit_timer(self, pt):
        L = self._tlayout()
        if L["seg_t"].contains(pt):
            return ("seg", "timer")
        if L["seg_s"].contains(pt):
            return ("seg", "sw")
        if L["btn_l"].adjusted(-6, -6, 6, 6).contains(pt):
            return ("left",)
        if L["btn_r"].adjusted(-6, -6, 6, 6).contains(pt):
            return ("right",)
        if self.clock.mode == "timer" and self.clock.t_state == "idle":
            for _lab, act, r in L["chips"]:
                if r.contains(pt):
                    return ("chip", act)
        return None

    def _hit_notes(self, pt):
        L = self._nlayout()
        for (idx, _t), r, xr in zip(L["vis"], L["rows"], L["xs"]):
            if xr.adjusted(-4, -4, 4, 4).contains(pt):
                return ("del", idx)
            if r.contains(pt):
                return ("open", idx)
        if L["new"].contains(pt):
            return ("new",)
        return None

    # ------------------------------------------------------------------
    # рисование
    # ------------------------------------------------------------------
    def _shape(self, w, h):
        path = QPainterPath()
        if not self.notch:
            r = min(h / 2.0, 36.0)
            path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
            return path
        f = float(FLARE)
        W = w + 2 * f
        r = min(h / 2.0, 30.0)
        path.moveTo(0, 0)
        path.lineTo(W, 0)
        path.arcTo(QRectF(W - f, 0, 2 * f, 2 * f), 90, 90)
        path.lineTo(W - f, h - r)
        path.arcTo(QRectF(W - f - 2 * r, h - 2 * r, 2 * r, 2 * r), 0, -90)
        path.lineTo(f + r, h)
        path.arcTo(QRectF(f, h - 2 * r, 2 * r, 2 * r), 270, -90)
        path.lineTo(f, f)
        path.arcTo(QRectF(-f, 0, 2 * f, 2 * f), 0, 90)
        path.closeSubpath()
        return path

    def _settle_xf(self, p, w, a):
        """Лёгкое «оседание» содержимого при появлении: от 94% до 100% с центра сверху."""
        s = 0.94 + 0.06 * a
        p.translate(w / 2.0, 0)
        p.scale(s, s)
        p.translate(-w / 2.0, 0)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        f = FLARE if self.notch else 0
        w, h = self.width() - 2 * f, self.height()
        path = self._shape(w, h)
        p.fillPath(path, QColor(0, 0, 0))
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setClipPath(path)
        p.translate(f, 0)

        a = {k: smooth(self.alpha[k]) for k in LAYERS}
        ox = int(round((w - W_EXP) / 2.0))
        if a["idle"] > 0.01:
            p.setOpacity(a["idle"])
            self._draw_idle(p, w)
        if a["media"] > 0.01:
            p.setOpacity(a["media"])
            self._draw_media_compact(p, w)
        if a["ctimer"] > 0.01:
            p.setOpacity(a["ctimer"])
            self._draw_ctimer(p, w)
        if a["drop"] > 0.01:
            p.setOpacity(a["drop"])
            self._draw_drop(p, w)
        if a["notif"] > 0.01 and self.notif_cur is not None:
            p.save()
            p.setOpacity(a["notif"])
            self._settle_xf(p, w, a["notif"])
            self._draw_notif(p, w)
            p.restore()
        if a["download"] > 0.01 and self.dl_view:
            p.save()
            p.setOpacity(a["download"])
            self._settle_xf(p, w, a["download"])
            self._draw_dl(p, QRectF(0, 0, w, DL_H))
            p.restore()
        if a["expanded"] > 0.01:
            p.save()
            self._settle_xf(p, w, a["expanded"])
            p.translate(ox, 0)
            self._draw_expanded(p, a["expanded"])
            p.restore()
        p.end()

    def _draw_art(self, p, rect, radius):
        p.save()
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path, Qt.ClipOperation.IntersectClip)
        if self.art is not None:
            p.drawPixmap(rect, self.art, QRectF(self.art.rect()))
        else:
            p.fillRect(rect, QColor(44, 44, 48))
            icon_tab_media(p, rect.center().x(), rect.center().y(), QColor(255, 255, 255, 110))
        p.restore()

    def _draw_eq(self, p, x, cy, maxh, color, playing):
        t = time.monotonic()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for i in range(4):
            k = (0.30 + 0.70 * abs(math.sin(t * (2.3 + i * 0.75) + i * 1.3))) if playing else 0.16
            bh = maxh * k
            p.drawRoundedRect(QRectF(x + i * 5.6, cy - bh / 2, 3.4, bh), 1.7, 1.7)

    def _draw_idle(self, p, w):
        if self.notch and self.s["notch_decor"]:
            cx = w / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(38, 38, 42))
            p.drawRoundedRect(QRectF(cx - 40, H_IDLE / 2 - 3.5, 56, 7), 3.5, 3.5)
            p.setBrush(QColor(20, 22, 32))
            p.drawEllipse(QPointF(cx + 38, H_IDLE / 2), 7.5, 7.5)
            p.setBrush(QColor(34, 44, 78))
            p.drawEllipse(QPointF(cx + 38, H_IDLE / 2), 3.8, 3.8)
            p.setBrush(QColor(90, 110, 170, 160))
            p.drawEllipse(QPointF(cx + 36.6, H_IDLE / 2 - 1.4), 1.1, 1.1)
            return
        draw_text(p, QRectF(0, 0, w, H_IDLE), datetime.datetime.now().strftime("%H:%M"),
                  F(14, QFont.Weight.DemiBold), QColor(255, 255, 255, 230), AL.AlignCenter)

    def _draw_media_compact(self, p, w):
        self._draw_art(p, QRectF(8, (H_MEDIA - 26) / 2, 26, 26), 8)
        txt = self.clock.mini_text()
        if txt:
            draw_text(p, QRectF(w - 14 - 70, 0, 70, H_MEDIA), txt, F(14, QFont.Weight.DemiBold),
                      QColor(*ORANGE), AL.AlignRight | AL.AlignVCenter, elide=False)
        else:
            self._draw_eq(p, w - 12 - 19, H_MEDIA / 2, 16, self.accent, self.m["playing"])

    def _draw_ctimer(self, p, w):
        ck = self.clock
        col = QColor(*ORANGE)
        icon_tab_timer(p, 22, H_CTIMER / 2.0, col)
        txt = ck.mini_text() or "00:00"
        c = col if (ck.t_state != "paused") else QColor(ORANGE[0], ORANGE[1], ORANGE[2], 140)
        draw_text(p, QRectF(w - 16 - 90, 0, 90, H_CTIMER), txt, F(15, QFont.Weight.DemiBold), c,
                  AL.AlignRight | AL.AlignVCenter, elide=False)

    def _draw_drop(self, p, w):
        cx = w / 2.0
        p.setPen(QPen(QColor(255, 255, 255, 215), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, 40), 17, 17)
        p.drawLine(QPointF(cx - 7, 40), QPointF(cx + 7, 40))
        p.drawLine(QPointF(cx, 33), QPointF(cx, 47))
        draw_text(p, QRectF(0, 66, w, 24), "Отпустите, чтобы добавить файл",
                  F(15, QFont.Weight.DemiBold), QColor(255, 255, 255), AL.AlignCenter)
        draw_text(p, QRectF(0, 88, w, 18), "Файл будет лежать на полке — потом его можно вытащить обратно",
                  F(12), QColor(255, 255, 255, 120), AL.AlignCenter)

    def _draw_notif(self, p, w):
        n = self.notif_cur
        app = n.get("app", "")
        r = QRectF(16, 14, 50, 50)
        kind = n.get("kind")
        if self.notif_pm is not None:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(r, 14, 14)
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(r, self.notif_pm, QRectF(self.notif_pm.rect()))
            p.restore()
        elif kind == "timer":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*ORANGE))
            p.drawRoundedRect(r, 14, 14)
            icon_tab_timer(p, r.center().x(), r.center().y(), QColor(255, 255, 255))
        else:
            if app == "Telegram":
                col = QColor(0, 136, 204)
            elif app == "WhatsApp":
                col = QColor(37, 211, 102)
            else:
                col = QColor.fromHsv(abs(hash(app)) % 360, 150, 215)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 14, 14)
            draw_text(p, r, (app[:1] or "?").upper(), F(24, QFont.Weight.DemiBold), QColor(255, 255, 255),
                      AL.AlignCenter, elide=False)
        tw = w - 80 - 16
        draw_text(p, QRectF(80, 11, tw - 56, 14), app.upper(), F(10, QFont.Weight.DemiBold),
                  QColor(255, 255, 255, 120))
        draw_text(p, QRectF(w - 16 - 54, 11, 54, 14), "сейчас", F(10), QColor(255, 255, 255, 90),
                  AL.AlignRight | AL.AlignVCenter)
        title = n.get("title") or app
        draw_text(p, QRectF(80, 25, tw, 22), title, F(15, QFont.Weight.DemiBold), QColor(255, 255, 255))
        draw_text(p, QRectF(80, 47, tw, 20), n.get("body", ""), F(13), QColor(255, 255, 255, 170))

    def _draw_dl(self, p, rect):
        v = self.dl_view
        if not v:
            return
        x, y, w = rect.left(), rect.top(), rect.width()
        white = QColor(255, 255, 255)
        gray = QColor(255, 255, 255, 140)
        ic = self._icon_for(v["name"])
        if not ic.isNull():
            p.drawPixmap(QRectF(x + 14, y + 11, 30, 30), ic, QRectF(ic.rect()))
        else:
            draw_text(p, QRectF(x + 14, y + 11, 30, 30), "⬇", EF(20), white, AL.AlignCenter, elide=False)
        cx, cy, r = x + w - 14 - 14, y + 26, 11.0
        done = v["done"]
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 45), 3.0))
        p.drawEllipse(QPointF(cx, cy), r, r)
        ring = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        green = QColor(*GREEN)
        blue = QColor(*BLUE)
        if done:
            p.setPen(QPen(green, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setPen(QPen(green, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            chk = QPainterPath()
            chk.moveTo(cx - 4.5, cy + 0.5)
            chk.lineTo(cx - 1.5, cy + 3.8)
            chk.lineTo(cx + 4.8, cy - 3.6)
            p.drawPath(chk)
            pct_txt, pct_col = "Готово", green
        elif v["prog"] is None:
            p.setPen(QPen(blue, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            start = int((90 - (time.monotonic() * 300.0) % 360.0) * 16)
            p.drawArc(ring, start, -100 * 16)
            pct_txt, pct_col = "", white
        else:
            p.setPen(QPen(blue, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, int(-360.0 * 16 * max(0.01, self.dl_disp)))
            pct_txt, pct_col = "%d%%" % int(round(self.dl_disp * 100)), white
        if pct_txt:
            draw_text(p, QRectF(cx - r - 8 - 52, y + 14, 52, 24), pct_txt, F(13, QFont.Weight.DemiBold),
                      pct_col, AL.AlignRight | AL.AlignVCenter, elide=False)
        tw = w - 54 - 14 - 28 - 8 - 52 - 6
        draw_text(p, QRectF(x + 54, y + 8, tw, 18), v["name"], F(13, QFont.Weight.DemiBold), white)
        draw_text(p, QRectF(x + 54, y + 27, tw, 16), v["sub"], F(11), gray)

    # ---------- раскрытый остров: панель вкладок + содержимое ----------
    def _draw_tabbar(self, p, base):
        W = W_EXP
        p.setOpacity(base)
        cont = QRectF((W - 210) / 2.0, 9, 210, 38)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QColor(255, 255, 255, 24))
        p.drawRoundedRect(cont, 19, 19)
        rects = self._tab_rects()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 238))
        p.drawRoundedRect(QRectF(self.hl.x, 13, 46, 30), 15, 15)
        fns = (icon_tab_media, icon_tab_quick, icon_tab_timer, icon_tab_notes)
        dark = QColor(12, 12, 14)
        light = QColor(255, 255, 255)
        for r, fn in zip(rects, fns):
            t = 1.0 - abs(self.hl.x - r.left()) / 52.0
            fn(p, r.center().x(), r.center().y(), lerp_color(light, dark, t))
        if self.clock.active():
            r = rects[2]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*ORANGE))
            p.drawEllipse(QPointF(r.right() - 8, r.top() + 7), 2.8, 2.8)

    def _draw_expanded(self, p, base):
        self._draw_tabbar(p, base)
        drawers = {"media": self._draw_tab_media, "quick": self._draw_tab_quick,
                   "timer": self._draw_tab_timer, "notes": self._draw_tab_notes}
        for t in TABS:
            ta = smooth(self.tab_alpha[t])
            if ta <= 0.01:
                continue
            slide = (1.0 - ta) * 18.0 * self.tab_dir
            off = slide if t == self.tab else -slide
            p.save()
            p.translate(off, 0)
            p.setOpacity(base * ta)
            drawers[t](p)
            p.restore()
        if self._dl_h() and self.dl_view:
            p.save()
            p.setOpacity(base)
            y = self.height() - DL_H
            p.setPen(QPen(QColor(255, 255, 255, 28), 1))
            p.drawLine(QPointF(22, y), QPointF(W_EXP - 22, y))
            self._draw_dl(p, QRectF(0, y, W_EXP, DL_H))
            p.restore()

    def _draw_tab_media(self, p):
        L = self._mlayout()
        m = self.m
        has = m["has"]
        white = QColor(255, 255, 255)
        gray = QColor(255, 255, 255, 140)
        W = W_EXP

        self._draw_art(p, L["art"], 12)
        title = m["title"] if has and m["title"] else ("Ничего не играет" if not has else "Без названия")
        artist = m["artist"] if has else "Включите музыку или видео"
        draw_text(p, L["title"], title, F(17, QFont.Weight.DemiBold), white if has else gray)
        draw_text(p, L["artist"], artist, F(14), gray)
        if has:
            self._draw_eq(p, L["eq_x"], L["eq_cy"], 18, self.accent, m["playing"])

        dur = m["dur"]
        bar = L["bar"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawRoundedRect(bar, 2.5, 2.5)
        if has and dur > 1:
            pos = self.cur_pos()
            fw = max(5.0, bar.width() * pos / dur)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), fw, bar.height()), 2.5, 2.5)
            draw_text(p, L["t_left"], fmt_time(pos), F(11), gray)
            draw_text(p, L["t_right"], "-" + fmt_time(dur - pos), F(11), gray,
                      AL.AlignRight | AL.AlignVCenter)

        ctrl = white if has else QColor(255, 255, 255, 70)
        pc, pl, nx = L["prev"].center(), L["play"].center(), L["next"].center()
        icon_prev(p, pc.x(), pc.y(), 22, ctrl)
        if m["playing"]:
            icon_pause(p, pl.x(), pl.y(), 26, ctrl)
        else:
            icon_play(p, pl.x(), pl.y(), 26, ctrl)
        icon_next(p, nx.x(), nx.y(), 22, ctrl)

        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawLine(QPointF(22, L["div1"]), QPointF(W - 22, L["div1"]))
        now = datetime.datetime.now()
        draw_text(p, L["time"], now.strftime("%H:%M"), F(24, QFont.Weight.DemiBold), white)
        draw_text(p, L["date"], ru_date(now), F(12), gray)

        right = W - 22
        ty = T0 + 132
        wd = self.wx
        if wd:
            t = int(round(wd["temp"]))
            ts = "0°" if t == 0 else "%+d°" % t
            tf = F(24, QFont.Weight.DemiBold)
            p.setFont(tf)
            tw = p.fontMetrics().horizontalAdvance(ts)
            draw_text(p, QRectF(right - tw - 2, ty, tw + 4, 30), ts, tf, white,
                      AL.AlignRight | AL.AlignVCenter, elide=False)
            draw_text(p, QRectF(right - tw - 2 - 40, ty, 36, 30), wd["emoji"], EF(22), white,
                      AL.AlignCenter, elide=False)
            info = wd["city"] + (" · " if wd["city"] and wd["desc"] else "") + wd["desc"]
            draw_text(p, QRectF(right - 230, ty + 30, 230, 16), info, F(12), gray,
                      AL.AlignRight | AL.AlignVCenter)
        else:
            draw_text(p, QRectF(right - 200, ty, 200, 30), "Погода загружается…", F(12), gray,
                      AL.AlignRight | AL.AlignVCenter)

        if self.files:
            p.setPen(QPen(QColor(255, 255, 255, 28), 1))
            p.drawLine(QPointF(22, L["div2"]), QPointF(W - 22, L["div2"]))
            draw_text(p, L["shelf_title"], "Файлы на полке: %d" % len(self.files),
                      F(12, QFont.Weight.DemiBold), gray)
            draw_text(p, L["clear"], "Очистить", F(12, QFont.Weight.DemiBold), QColor(*BLUE),
                      AL.AlignRight | AL.AlignVCenter)
            for (idx, path), r in zip(self._shown(), L["chips"]):
                pm = self._icon(path)
                if not pm.isNull():
                    p.drawPixmap(QRectF(r.center().x() - 17, r.top(), 34, 34), pm, QRectF(pm.rect()))
                draw_text(p, QRectF(r.left(), r.top() + 38, r.width(), 15), os.path.basename(path),
                          F(11), QColor(255, 255, 255, 200), AL.AlignCenter)

    def _draw_slider(self, p, rect, frac, kind, enabled=True, muted=False):
        rr = rect.height() / 2.0
        track = QPainterPath()
        track.addRoundedRect(rect, rr, rr)
        p.fillPath(track, QColor(255, 255, 255, 40 if enabled else 22))
        fw = 0.0
        if frac is not None and enabled:
            fw = rect.width() * max(0.0, min(1.0, frac))
            p.save()
            p.setClipPath(track, Qt.ClipOperation.IntersectClip)
            p.fillRect(QRectF(rect.left(), rect.top(), fw, rect.height()), QColor(255, 255, 255, 238))
            p.restore()
        covered = fw > 34
        col = QColor(12, 12, 14) if covered else QColor(255, 255, 255, 255 if enabled else 90)
        cx, cy = rect.left() + 22, rect.center().y()
        if kind == "vol":
            icon_speaker(p, cx, cy, col, muted)
        else:
            icon_sun(p, cx, cy, col)
        if not enabled:
            draw_text(p, QRectF(rect.left() + 44, rect.top(), rect.width() - 54, rect.height()),
                      "недоступно", F(12), QColor(255, 255, 255, 90))

    def _draw_tab_quick(self, p):
        L = self._qlayout()
        st = self.sysst
        air = not st.get("any_on", True)
        tiles = [("wifi", "Wi-Fi", bool(st.get("wifi")), st.get("wifi") is not None),
                 ("bt", "Bluetooth", bool(st.get("bt")), st.get("bt") is not None),
                 ("air", "Полёт", air, HAVE_RADIO),
                 ("mute", "Без звука", self.muted, self.vol.available)]
        for (key, label, active, enabled), r in zip(tiles, L["tiles"]):
            p.setPen(Qt.PenStyle.NoPen)
            if active:
                p.setBrush(QColor(255, 255, 255, 245))
            else:
                p.setBrush(QColor(255, 255, 255, 34 if enabled else 16))
            p.drawRoundedRect(r, 22, 22)
            ic = QColor(12, 12, 14) if active else QColor(255, 255, 255, 255 if enabled else 90)
            cx, ty = r.center().x(), r.top()
            if key == "wifi":
                icon_wifi(p, cx, ty + 36, ic)
            elif key == "bt":
                icon_bt(p, cx, ty + 27, ic)
            elif key == "air":
                icon_airplane(p, cx, ty + 26, ic)
            else:
                icon_speaker(p, cx, ty + 26, ic, self.muted)
            draw_text(p, QRectF(r.left(), ty + 48, r.width(), 18), label, F(11, QFont.Weight.DemiBold),
                      QColor(12, 12, 14) if active else QColor(255, 255, 255, 255 if enabled else 90),
                      AL.AlignCenter)
        self._draw_slider(p, L["vol"], self.volv, "vol", self.vol.available, self.muted)
        if "bri" in L:
            bv = (self.bri.value / 100.0) if self.bri.value is not None else None
            self._draw_slider(p, L["bri"], bv, "bri", True)

    def _draw_round_btn(self, p, rect, label, kind, enabled=True):
        cols = {"gray": (255, 255, 255), "green": GREEN, "orange": ORANGE, "red": RED}
        c = cols[kind]
        k = 1.0 if enabled else 0.4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c[0], c[1], c[2], int((40 if kind == "gray" else 62) * k)))
        p.drawEllipse(rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(c[0], c[1], c[2], int((46 if kind == "gray" else 90) * k)), 1.2))
        p.drawEllipse(rect.adjusted(-3.5, -3.5, 3.5, 3.5))
        tc = QColor(255, 255, 255, int(255 * k)) if kind == "gray" else QColor(c[0], c[1], c[2], int(255 * k))
        draw_text(p, rect, label, F(14, QFont.Weight.DemiBold), tc, AL.AlignCenter, elide=False)

    def _draw_ring(self, p, c, r, frac, color):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 34), 5.0))
        p.drawEllipse(c, r, r)
        if frac > 0.003:
            p.setPen(QPen(color, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16, int(-360.0 * 16 * min(1.0, frac)))

    def _draw_tab_timer(self, p):
        L = self._tlayout()
        ck = self.clock
        now = time.monotonic()
        white = QColor(255, 255, 255)
        gray = QColor(255, 255, 255, 140)
        orange = QColor(*ORANGE)

        # переключатель «Таймер | Секундомер» с плавной «таблеткой»
        seg = L["seg"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 24))
        p.drawRoundedRect(seg, 15, 15)
        sx = seg.left() + 2 + self.seg.x * 108
        p.setBrush(QColor(255, 255, 255, 64))
        p.drawRoundedRect(QRectF(sx, seg.top() + 2, 106, 26), 13, 13)
        draw_text(p, L["seg_t"], "Таймер", F(13, QFont.Weight.DemiBold),
                  white if ck.mode == "timer" else gray, AL.AlignCenter)
        draw_text(p, L["seg_s"], "Секундомер", F(13, QFont.Weight.DemiBold),
                  white if ck.mode == "sw" else gray, AL.AlignCenter)

        c, r = L["ring_c"], L["ring_r"]
        rect_t = QRectF(c.x() - r + 6, c.y() - 22, 2 * r - 12, 44)
        if ck.mode == "timer":
            rem = ck.t_remaining(now)
            self._draw_ring(p, c, r, self.tm_disp, orange)
            txt = fmt_clock(math.ceil(rem - 1e-6))
            draw_text(p, rect_t, txt, F(30 if len(txt) <= 5 else 22, QFont.Weight.DemiBold),
                      white if ck.t_state != "paused" else gray, AL.AlignCenter, elide=False)
            self._draw_round_btn(p, L["btn_l"], "Отмена", "gray", ck.t_state != "idle")
            if ck.t_state == "running":
                self._draw_round_btn(p, L["btn_r"], "Пауза", "orange")
            elif ck.t_state == "paused":
                self._draw_round_btn(p, L["btn_r"], "Далее", "green")
            else:
                self._draw_round_btn(p, L["btn_r"], "Старт", "green")
            if ck.t_state == "idle":
                for lab, act, rr in L["chips"]:
                    sel = act[0] == "set" and int(ck.t_dur) == act[1] * 60
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 70 if sel else 26))
                    p.drawRoundedRect(rr, 15, 15)
                    draw_text(p, rr, lab, F(12, QFont.Weight.DemiBold), white if sel else QColor(255, 255, 255, 200),
                              AL.AlignCenter, elide=False)
            else:
                end = datetime.datetime.now() + datetime.timedelta(seconds=rem)
                info = ("Закончится в " + end.strftime("%H:%M")) if ck.t_state == "running" else "Пауза"
                draw_text(p, L["info"], info, F(13), gray, AL.AlignCenter)
        else:
            el = ck.sw_elapsed(now)
            self._draw_ring(p, c, r, (el % 60.0) / 60.0 if el > 0 else 0.0, orange)
            draw_text(p, rect_t, fmt_sw(el), F(20, QFont.Weight.DemiBold), white, AL.AlignCenter, elide=False)
            running = ck.sw_state == "running"
            if running:
                self._draw_round_btn(p, L["btn_l"], "Круг", "gray")
                self._draw_round_btn(p, L["btn_r"], "Стоп", "red")
            else:
                self._draw_round_btn(p, L["btn_l"], "Сброс", "gray", el > 0)
                self._draw_round_btn(p, L["btn_r"], "Старт", "green")
            if ck.laps:
                prev = ck.laps[-2] if len(ck.laps) > 1 else 0.0
                info = "Круг %d · %s" % (len(ck.laps), fmt_sw(ck.laps[-1] - prev))
            else:
                info = "Нажмите «Старт»" if el <= 0 else "Нажмите «Круг», чтобы отметить круг"
            draw_text(p, L["info"], info, F(13), gray, AL.AlignCenter)

    def _draw_tab_notes(self, p):
        L = self._nlayout()
        white = QColor(255, 255, 255)
        gray = QColor(255, 255, 255, 140)
        if not L["vis"]:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(L["empty"], 16, 16)
            draw_text(p, L["empty"], "Заметок пока нет", F(14), gray, AL.AlignCenter)
        for (idx, text), r, xr in zip(L["vis"], L["rows"], L["xs"]):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 16, 16)
            first = text.strip().split("\n")[0].strip() or "Пустая заметка"
            draw_text(p, QRectF(r.left() + 16, r.top(), r.width() - 16 - 44, r.height()), first,
                      F(14), white)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawEllipse(xr.adjusted(4, 4, -4, -4))
            _stroke(p, QColor(255, 255, 255, 200), 1.6)
            c = xr.center()
            p.drawLine(QPointF(c.x() - 3.2, c.y() - 3.2), QPointF(c.x() + 3.2, c.y() + 3.2))
            p.drawLine(QPointF(c.x() - 3.2, c.y() + 3.2), QPointF(c.x() + 3.2, c.y() - 3.2))
        nb = L["new"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BLUE[0], BLUE[1], BLUE[2], 50))
        p.drawRoundedRect(nb, 19, 19)
        blue = QColor(*BLUE)
        _stroke(p, blue, 2.0)
        cx = nb.center().x() - 62
        cy = nb.center().y()
        p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
        p.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 5))
        draw_text(p, QRectF(cx + 12, nb.top(), 140, nb.height()), "Новая заметка",
                  F(14, QFont.Weight.DemiBold), blue)

    # ------------------------------------------------------------------
    # мышь
    # ------------------------------------------------------------------
    def _open_uri(self, uri):
        try:
            os.startfile(uri)
        except Exception:
            pass

    def _slider_set(self, kind, rect, x):
        pt = self._body_pt(QPointF(x, 0))
        frac = (pt.x() - rect.left()) / rect.width()
        frac = min(1.0, max(0.0, frac))
        if kind == "vol":
            self.volv = frac
            if self.muted and frac > 0:
                self.vol.set_mute(False)
                self.muted = False
            self.vol.set(frac)
        else:
            self.bri.set(int(round(frac * 100)))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            if self.menu:
                self.menu.exec(e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        disp = self._display()
        pos = e.position()
        if disp == "notif":
            self._next_notif()
            return
        if disp in ("compact", "download"):
            self.set_mode("expanded")
            return
        if disp != "expanded":
            return
        pt = self._body_pt(pos)
        for i, r in enumerate(self._tab_rects()):
            if r.adjusted(-3, -3, 3, 3).contains(pt):
                self.set_tab(TABS[i])
                return
        if self.tab == "media":
            self._click_media(self._hit_media(pt), e)
        elif self.tab == "quick":
            self._click_quick(self._hit_quick(pt), pos)
        elif self.tab == "timer":
            self._click_timer(self._hit_timer(pt))
        else:
            self._click_notes(self._hit_notes(pt))
        self.update()

    def _click_media(self, hit, e):
        if not hit:
            return
        k = hit[0]
        if k == "play":
            self.m["playing"] = not self.m["playing"]
            self.m["pos"] = self.cur_pos()
            self.m["stamp"] = time.monotonic()
            self.media.command("toggle")
            self._kick()
        elif k == "next":
            self.media.command("next")
        elif k == "prev":
            self.media.command("prev")
        elif k == "bar":
            if self.m["has"] and self.m["dur"] > 1:
                sec = hit[1] * self.m["dur"]
                self.m["pos"] = sec
                self.m["stamp"] = time.monotonic()
                self.media.command("seek", sec)
        elif k == "clear":
            self.clear_files()
        elif k == "chip":
            self._press = (hit[1], e.globalPosition().toPoint())
        elif k == "weather":
            self.open_city_picker()

    def _click_quick(self, hit, pos):
        if not hit:
            return
        if hit[0] == "slider":
            self._slider = hit[1]
            self._slider_rect = hit[2]
            self._slider_set(hit[1], hit[2], pos.x())
        elif hit[0] == "tile":
            self._tile_action(hit[1])

    def _click_timer(self, hit):
        if not hit:
            return
        ck = self.clock
        now = time.monotonic()
        k = hit[0]
        if k == "seg":
            ck.mode = hit[1]
        elif ck.mode == "timer":
            if k == "right":
                if ck.t_state == "idle":
                    self.tm_disp = 0.0           # кольцо красиво «заполняется» при старте
                    ck.t_start(now)
                elif ck.t_state == "running":
                    ck.t_pause(now)
                else:
                    ck.t_start(now)
            elif k == "left":
                if ck.t_state != "idle":
                    ck.t_cancel()
            elif k == "chip":
                if hit[1][0] == "adj":
                    ck.t_adjust(hit[1][1])
                else:
                    ck.t_set(hit[1][1])
        else:
            if k == "right":
                ck.sw_toggle(now)
            elif k == "left":
                ck.sw_secondary(now)
        self._kick()

    def _click_notes(self, hit):
        if not hit:
            return
        k = hit[0]
        if k == "new":
            self.open_note(-1)
        elif k == "open":
            self.open_note(hit[1])
        elif k == "del":
            self.notes.remove(hit[1])
            self.notes_off = max(0, min(self.notes_off, len(self.notes.items) - 4))
            self._kick()

    def _tile_action(self, key):
        st = self.sysst
        if key == "wifi" and st.get("wifi") is not None:
            new = not st["wifi"]
            self.sysctl.set_radio(1, new)
            st["wifi"] = new
        elif key == "bt" and st.get("bt") is not None:
            new = not st["bt"]
            self.sysctl.set_radio(3, new)
            st["bt"] = new
        elif key == "air" and HAVE_RADIO:
            enable = bool(st.get("any_on", True))
            self.sysctl.set_airplane(enable)
            st["any_on"] = not enable
            if enable:
                st["wifi"] = False if st.get("wifi") is not None else None
                st["bt"] = False if st.get("bt") is not None else None
        elif key == "mute" and self.vol.available:
            self.muted = not self.muted
            self.vol.set_mute(self.muted)
        self.update()

    def wheelEvent(self, e):
        if self._display() == "expanded" and self.tab == "notes":
            step = -1 if e.angleDelta().y() > 0 else 1
            mx = max(0, len(self.notes.items) - 4)
            self.notes_off = max(0, min(mx, self.notes_off + step))
            self.update()

    def mouseMoveEvent(self, e):
        if self._slider is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self._slider_set(self._slider, self._slider_rect, e.position().x())
            return
        if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            idx, start = self._press
            if (e.globalPosition().toPoint() - start).manhattanLength() > 8:
                self._press = None
                self._start_drag(idx)

    def mouseReleaseEvent(self, e):
        self._slider = None
        if self._press is not None:
            idx, _ = self._press
            self._press = None
            if e.button() == Qt.MouseButton.LeftButton and 0 <= idx < len(self.files):
                self._open_uri(self.files[idx])

    def _start_drag(self, idx):
        if not (0 <= idx < len(self.files)):
            return
        path = self.files[idx]
        self._dragging = True
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        d = QDrag(self)
        d.setMimeData(md)
        pm = self._icon(path)
        if not pm.isNull():
            d.setPixmap(pm.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
            d.setHotSpot(QPoint(24, 24))
        d.exec(Qt.DropAction.CopyAction)
        self._dragging = False

    # ------------------------------------------------------------------
    # выбор города и заметки
    # ------------------------------------------------------------------
    def open_city_picker(self):
        if self.picker is None:
            self.picker = CityPicker()
            self.picker.chosen.connect(self._city_chosen)
            self.picker.auto.connect(self._city_auto)
        g = self.geometry()
        self.picker.open_animated(g.center().x(), g.bottom() + 12)

    def _city_chosen(self, lat, lon, name):
        self.wx = None
        self.wsrc.set_location(lat, lon, name)
        self.update()

    def _city_auto(self):
        self.wx = None
        self.wsrc.use_auto()
        self.update()

    def open_note(self, idx):
        if self.note_editor is None:
            self.note_editor = NoteEditor()
            self.note_editor.saved.connect(self._note_saved)
            self.note_editor.deleted.connect(self._note_deleted)
        text = self.notes.items[idx] if 0 <= idx < len(self.notes.items) else ""
        g = self.geometry()
        self.note_editor.open_for(idx, text, g.center().x(), g.bottom() + 12)

    def _note_saved(self, idx, text):
        text = text.strip()
        if idx >= 0:
            if text:
                self.notes.update(idx, text)
            else:
                self.notes.remove(idx)
        elif text:
            self.notes.add(text)
        self.notes_off = 0
        self._kick()

    def _note_deleted(self, idx):
        self.notes.remove(idx)
        self.notes_off = 0
        self._kick()

    # ------------------------------------------------------------------
    # перетаскивание файлов НА остров
    # ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if self._dragging or not e.mimeData().hasUrls():
            e.ignore()
            return
        e.acceptProposedAction()
        self.set_mode("drop")

    def dragMoveEvent(self, e):
        if not self._dragging and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        if self.mode == "drop":
            self.set_mode("compact")

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_files(paths)
            e.acceptProposedAction()
        self._in_since = None
        self._out_since = None
        self.tab = "media"
        self.tab_alpha = {t: (1.0 if t == "media" else 0.0) for t in TABS}
        self.set_mode("expanded")


# =====================================================================
#                     Автозапуск, трей, запуск
# =====================================================================
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except Exception:
        return False


def set_autostart(enable):
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = '"%s"' % sys.executable
                else:
                    exe = sys.executable
                    if exe.lower().endswith("python.exe"):
                        exe = exe[:-10] + "pythonw.exe"
                    cmd = '"%s" "%s"' % (exe, os.path.abspath(__file__))
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception as ex:
        print("[WinIsland] автозапуск:", ex)


def make_tray_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(0, 0, 0))
    p.setPen(QPen(QColor(255, 255, 255, 90), 2))
    p.drawRoundedRect(QRectF(4, 20, 56, 24), 12, 12)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QPointF(18, 32), 4, 4)
    p.end()
    return QIcon(pm)


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def build_menu(app, island, settings, weather):
    menu = QMenu()

    # --- вид ---
    sub_look = menu.addMenu("Вид острова")
    grp = QActionGroup(sub_look)
    grp.setExclusive(True)
    for key, label in (("island", "Остров (капсула, как iPhone 14 Pro)"),
                       ("notch", "Чёлка (как iPhone 13, с динамиком и камерой)")):
        a = QAction(label, sub_look)
        a.setCheckable(True)
        a.setChecked(settings["style"] == key)
        a.triggered.connect(lambda _c=False, k=key: (settings.__setitem__("style", k), island.apply_style()))
        grp.addAction(a)
        sub_look.addAction(a)
    sub_look.addSeparator()
    a_decor = QAction("Рисовать динамик и камеру на чёлке", sub_look)
    a_decor.setCheckable(True)
    a_decor.setChecked(bool(settings["notch_decor"]))
    a_decor.toggled.connect(lambda v: (settings.__setitem__("notch_decor", bool(v)), island.update()))
    sub_look.addAction(a_decor)

    # --- полноэкранный режим ---
    a_fs = QAction("Скрывать в полноэкранных приложениях", menu)
    a_fs.setCheckable(True)
    a_fs.setChecked(bool(settings["hide_fullscreen"]))
    a_fs.toggled.connect(lambda v: settings.__setitem__("hide_fullscreen", bool(v)))
    menu.addAction(a_fs)

    # --- задержка закрытия ---
    sub_delay = menu.addMenu("Закрывать после ухода мыши")
    dgrp = QActionGroup(sub_delay)
    dgrp.setExclusive(True)
    for sec, label in ((1, "через 1 секунду"), (3, "через 3 секунды"), (5, "через 5 секунд"),
                       (10, "через 10 секунд"), (20, "через 20 секунд"), (60, "через минуту")):
        a = QAction(label, sub_delay)
        a.setCheckable(True)
        a.setChecked(int(settings["close_delay"]) == sec)
        a.triggered.connect(lambda _c=False, s=sec: settings.__setitem__("close_delay", s))
        dgrp.addAction(a)
        sub_delay.addAction(a)
    menu.addSeparator()

    # --- уведомления и загрузки ---
    a_notif = QAction("Показывать уведомления Windows (Telegram и др.)", menu)
    a_notif.setCheckable(True)
    a_notif.setChecked(bool(settings["notifications"]))
    a_notif.toggled.connect(lambda v: settings.__setitem__("notifications", bool(v)))
    menu.addAction(a_notif)

    a_test = QAction("Проверить уведомления (отправить тест)", menu)
    a_test.triggered.connect(island.test_notification)
    menu.addAction(a_test)

    a_nset = QAction("Параметры уведомлений Windows…", menu)
    a_nset.triggered.connect(lambda: island._open_uri("ms-settings:notifications"))
    menu.addAction(a_nset)

    a_dl = QAction("Показывать загрузки файлов", menu)
    a_dl.setCheckable(True)
    a_dl.setChecked(bool(settings["downloads"]))
    a_dl.toggled.connect(lambda v: settings.__setitem__("downloads", bool(v)))
    menu.addAction(a_dl)

    ext_dir = os.path.join(app_dir(), "browser_extension")
    if os.path.isdir(ext_dir):
        a_ext = QAction("Папка расширения для браузера (точные загрузки)", menu)
        a_ext.triggered.connect(lambda: os.startfile(ext_dir))
        menu.addAction(a_ext)
    menu.addSeparator()

    a_city = QAction("Выбрать город для погоды…", menu)
    a_city.triggered.connect(island.open_city_picker)
    menu.addAction(a_city)

    act_auto = QAction("Запускать вместе с Windows", menu)
    act_auto.setCheckable(True)
    act_auto.setChecked(autostart_enabled())
    act_auto.toggled.connect(set_autostart)
    menu.addAction(act_auto)
    act_clear = QAction("Очистить полку с файлами", menu)
    act_clear.triggered.connect(island.clear_files)
    menu.addAction(act_clear)
    menu.addSeparator()
    act_quit = QAction("Выход", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    return menu


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)

    lock = QLockFile(QDir.temp().filePath("winisland.lock"))
    if not lock.tryLock(100):
        return 0

    settings = Settings()
    media = MediaBackend()
    weather = Weather(settings)
    hub = DownloadHub()
    notifier = NotifWatcher()
    sysctl = SystemControls()
    vol = VolumeCtl()
    bri = BrightnessCtl()

    island = Island(settings, media, weather, hub, notifier, sysctl, vol, bri)
    menu = build_menu(app, island, settings, weather)
    island.menu = menu

    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip(APP_NAME)
    tray.setContextMenu(menu)
    tray.show()

    roots = [os.path.join(os.path.expanduser("~"), "Downloads")] + list(EXTRA_WATCH_DIRS)
    start_download_server(hub)
    FsWatcher(hub, roots).start()
    media.start()
    weather.start()
    notifier.start()
    sysctl.start()
    island.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
