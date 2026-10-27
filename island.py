# -*- coding: utf-8 -*-
"""
WinIsland 2.0 — «Dynamic Island» для Windows 10/11 в стиле Apple.

Возможности:
  * остров (капсула) или чёлка как на iPhone 13 (с динамиком и камерой) — выбирается в меню в трее;
  * плеер: обложка, название, перемотка, пауза, следующий/предыдущий трек;
  * время, дата, погода (клик по погоде — выбор города с поиском);
  * уведомления Windows (Telegram, WhatsApp, почта и т.д.) показываются в острове;
  * загрузки файлов: процент, скорость, красивое исчезновение после 100%;
  * быстрые настройки: Wi-Fi, Bluetooth, режим полёта, громкость, яркость;
  * полка для файлов: перетащи файл на остров — потом вытащи обратно;
  * остров закрывается через N секунд после ухода мыши (по умолчанию 10).
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
                             QListWidgetItem, QPushButton)

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
HIDE_IN_FULLSCREEN = True # прятать остров, если открыто полноэкранное окно
DL_PORT = 47831           # порт для расширения браузера (загрузки)
EXTRA_WATCH_DIRS = []     # дополнительные папки для слежения за загрузками, например [r"D:\Загрузки"]

# размеры состояний (px)
W_IDLE, H_IDLE = 150, 36
W_IDLE_NOTCH = 214
W_MEDIA, H_MEDIA = 236, 38
W_EXP, H_EXP = 440, 226
SHELF_H = 96
DL_H = 52
W_DROP, H_DROP = 440, 116
W_NOTIF, H_NOTIF = 400, 78
W_DL, H_DL = 340, 52
FLARE = 14                # «плечи» чёлки по бокам

APP_NAME = "WinIsland"
LAYERS = ("idle", "media", "expanded", "drop", "quick", "notif", "download")

AL = Qt.AlignmentFlag
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
PARTIAL_EXT = (".crdownload", ".part", ".opdownload", ".download", ".partial", ".aria2", ".tdownload")

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")
DEFAULT_SETTINGS = {"style": "island", "notch_decor": True, "close_delay": 10,
                    "notifications": True, "downloads": True, "city": None}


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


def fmt_size(n):
    n = float(max(0, n))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            s = ("%.0f" % n) if unit == "Б" else ("%.1f" % n)
            return s.replace(".", ",") + " " + unit
        n /= 1024.0
    return "0 Б"


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
             ("vk", "VK"), ("steam", "Steam"), ("teams", "Teams"), ("zoom", "Zoom")]
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


# =====================================================================
#                     Шрифты и значки (рисуем сами)
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


def SF(px):
    f = QFont()
    f.setFamilies(["Segoe UI Symbol", "Segoe UI", "Arial"])
    f.setPixelSize(px)
    return f


def draw_text(p, rect, s, font, color, align=AL.AlignLeft | AL.AlignVCenter, elide=True):
    p.setFont(font)
    p.setPen(color)
    if elide:
        s = p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(rect.width()))
    p.drawText(rect, align, s)


def _poly(p, pts, color):
    path = QPainterPath()
    path.moveTo(pts[0][0], pts[0][1])
    for q in pts[1:]:
        path.lineTo(q[0], q[1])
    path.closeSubpath()
    p.setPen(QPen(color, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
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
    p.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def icon_gear(p, cx, cy, color):
    """Значок «ползунки» для кнопки быстрых настроек."""
    p.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(cx - 7, cy - 4), QPointF(cx + 7, cy - 4))
    p.drawLine(QPointF(cx - 7, cy + 4), QPointF(cx + 7, cy + 4))
    p.setBrush(color)
    p.drawEllipse(QPointF(cx - 2.5, cy - 4), 2.6, 2.6)
    p.drawEllipse(QPointF(cx + 3, cy + 4), 2.6, 2.6)


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
        req = urllib.request.Request(url, headers={"User-Agent": "WinIsland/2.0"})
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
        tmp = tempfile.mkdtemp(prefix="winisland_")
        last_id = None
        last_sig = None
        while True:
            try:
                if os.path.exists(db):
                    sig = tuple(os.path.getmtime(db + s) if os.path.exists(db + s) else 0
                                for s in ("", "-wal"))
                    if sig != last_sig:
                        last_sig = sig
                        last_id, items = self._read(db, tmp, last_id)
                        for it in items:
                            self.arrived.emit(it)
            except Exception:
                pass
            time.sleep(1.0)


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
#                        Окно выбора города
# =====================================================================
class CityPicker(QWidget):
    chosen = pyqtSignal(float, float, str)
    auto = pyqtSignal()
    results_ready = pyqtSignal(object, str)

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(380, 380)
        self._q = ""
        self._anim = None
        self.setStyleSheet("""
            QLabel{color:white;font-family:'Segoe UI Variable Display','Segoe UI';font-size:17px;font-weight:600;}
            QLabel#hint{color:#8e8e93;font-size:13px;font-weight:400;}
            QLineEdit{background:#1c1c1e;color:white;border:1px solid #2c2c2e;border-radius:12px;
                      padding:10px 12px;font-size:15px;selection-background-color:#0a84ff;
                      font-family:'Segoe UI Variable Display','Segoe UI';}
            QListWidget{background:transparent;border:none;color:white;font-size:14px;outline:0;
                        font-family:'Segoe UI Variable Display','Segoe UI';}
            QListWidget::item{padding:9px 12px;border-radius:10px;}
            QListWidget::item:hover{background:#1c1c1e;}
            QListWidget::item:selected{background:#0a84ff;color:white;}
            QPushButton{background:#1c1c1e;color:#0a84ff;border:none;border-radius:12px;
                        padding:10px;font-size:14px;font-weight:600;
                        font-family:'Segoe UI Variable Display','Segoe UI';}
            QPushButton:hover{background:#2c2c2e;}
            QPushButton#x{color:#8e8e93;padding:2px 8px;border-radius:12px;font-size:13px;}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Город для погоды")
        head.addWidget(title)
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

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 26, 26)
        p.fillPath(path, QColor(0, 0, 0))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(path)
        p.end()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.fade_out()
        else:
            super().keyPressEvent(e)

    def open_animated(self, cx, top):
        scr = QGuiApplication.primaryScreen().geometry()
        x = max(scr.x() + 8, min(cx - self.width() // 2, scr.x() + scr.width() - self.width() - 8))
        y = top
        self.search.clear()
        self.list.clear()
        self.hint.setText("Например: Казань, Минск, Алматы")
        self.move(x, y - 18)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        a1 = QPropertyAnimation(self, b"pos")
        a1.setDuration(380)
        a1.setStartValue(QPoint(x, y - 18))
        a1.setEndValue(QPoint(x, y))
        a1.setEasingCurve(QEasingCurve.Type.OutCubic)
        a2 = QPropertyAnimation(self, b"windowOpacity")
        a2.setDuration(260)
        a2.setStartValue(0.0)
        a2.setEndValue(1.0)
        g = QParallelAnimationGroup(self)
        g.addAnimation(a1)
        g.addAnimation(a2)
        self._anim = g
        g.start()

    def fade_out(self):
        a = QPropertyAnimation(self, b"windowOpacity")
        a.setDuration(180)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.finished.connect(self.hide)
        self._anim = a
        a.start()

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
            req = urllib.request.Request(url, headers={"User-Agent": "WinIsland/2.0"})
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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self.mode = "compact"      # compact | expanded | quick | drop
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

        self._clock = QTimer(self)
        self._clock.timeout.connect(self.update)
        self._clock.start(1000)

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
        if self.mode in ("drop", "quick"):
            return self.mode
        if self.notif_cur is not None:
            return "notif"
        if self.mode == "expanded":
            return "expanded"
        if self.dl_overlay and self.dl_view:
            return "download"
        return "compact"

    def _dl_h(self):
        return DL_H if (self.dl_overlay and self.dl_view) else 0

    def _quick_h(self):
        return 240 + (48 if self.bri.available else 0)

    def _target_size(self, disp):
        if disp == "expanded":
            return W_EXP, H_EXP + self._dl_h() + (SHELF_H if self.files else 0)
        if disp == "drop":
            return W_DROP, H_DROP
        if disp == "quick":
            return W_EXP, self._quick_h()
        if disp == "notif":
            return W_NOTIF, H_NOTIF
        if disp == "download":
            return W_DL, H_DL
        if self.m["has"]:
            return W_MEDIA, H_MEDIA
        return (W_IDLE_NOTCH if self.notch else W_IDLE), H_IDLE

    def _retarget(self):
        disp = self._display()
        w, h = self._target_size(disp)
        self.sw.target = w
        self.sh.target = h
        self.layer_targets = {k: False for k in LAYERS}
        if disp == "compact":
            self.layer_targets["media" if self.m["has"] else "idle"] = True
        else:
            self.layer_targets[disp] = True

    def set_mode(self, mode):
        self.mode = mode
        if mode == "quick":
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

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.05)
        self._last = now

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

        self._retarget()
        moving = False
        for sp in (self.sw, self.sh):
            sp.step(dt)
            if sp.moving():
                moving = True
            else:
                sp.settle()
        busy = moving
        for k in LAYERS:
            t = 1.0 if self.layer_targets[k] else 0.0
            a = self.alpha[k]
            if a != t:
                busy = True
                a = min(t, a + dt * 6.5) if t > a else max(t, a - dt * 8.0)
                self.alpha[k] = a
        disp = self._display()
        dynamic = ((self.m["playing"] and disp in ("compact", "expanded"))
                   or (self.dl_view is not None and disp in ("download", "expanded"))
                   or disp in ("notif", "quick"))
        self._apply_geometry()
        self.update()
        if not busy and not dynamic:
            self._timer.stop()
        else:
            self._timer.setInterval(self._frame_ms if busy else 33)

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
    def push_notification(self, n):
        if not self.s["notifications"]:
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
            self.notif_until = time.monotonic() + 5.5 + min(3.0, len(n.get("body", "")) / 60.0)
        else:
            self.notif_cur = None
            self.notif_pm = None
        self._kick()

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
    # наведение мыши, полноэкранный режим
    # ------------------------------------------------------------------
    def _foreground_fullscreen(self):
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value in ("WorkerW", "Progman", "Shell_TrayWnd"):
                return False
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            return r.left <= 0 and r.top <= 0 and r.right >= sw and r.bottom >= sh
        except Exception:
            return False

    def _hover_poll(self):
        if HIDE_IN_FULLSCREEN:
            self._fs_tick += 1
            if self._fs_tick >= 12:
                self._fs_tick = 0
                fs = self._foreground_fullscreen()
                if fs and not self._fs_hidden:
                    self._fs_hidden = True
                    self.hide()
                elif not fs and self._fs_hidden:
                    self._fs_hidden = False
                    self.show()
        if not self.isVisible():
            return
        now = time.monotonic()
        inside = self.frameGeometry().contains(QCursor.pos())

        if self.mode == "quick" and self._slider is None:
            self._vol_tick += 1
            if self._vol_tick >= 8:
                self._vol_tick = 0
                self._refresh_volume()
                self.update()

        if self.notif_cur is not None and inside:
            self.notif_until = max(self.notif_until, now + 1.5)

        if self._dragging or self.mode == "drop":
            return
        picker_open = self.picker is not None and self.picker.isVisible()
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
            if self.mode in ("expanded", "quick") and not picker_open:
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
    # раскладка (координаты внутри блока шириной W_EXP)
    # ------------------------------------------------------------------
    def _layout(self):
        W = W_EXP
        L = {}
        L["art"] = QRectF(22, 18, 60, 60)
        L["title"] = QRectF(98, 22, W - 98 - 22 - 30 - 10 - 28, 24)
        L["artist"] = QRectF(98, 48, W - 98 - 22 - 40, 20)
        L["gear"] = QRectF(W - 22 - 30, 18, 30, 30)
        L["eq_x"] = W - 22 - 30 - 10 - 19
        L["eq_cy"] = 33
        L["bar"] = QRectF(22, 92, W - 44, 5)
        L["t_left"] = QRectF(22, 101, 90, 14)
        L["t_right"] = QRectF(W - 22 - 90, 101, 90, 14)
        cy = 138
        L["prev"] = QRectF(W / 2 - 80 - 18, cy - 18, 36, 36)
        L["play"] = QRectF(W / 2 - 22, cy - 22, 44, 44)
        L["next"] = QRectF(W / 2 + 80 - 18, cy - 18, 36, 36)
        L["div1"] = 162
        L["time"] = QRectF(22, 170, 190, 32)
        L["date"] = QRectF(22, 202, 190, 16)
        L["close"] = QRectF(22, 166, 190, 56)
        L["weather"] = QRectF(W - 22 - 200, 166, 200, 56)
        y = H_EXP
        dlh = self._dl_h()
        if dlh:
            L["dl"] = QRectF(0, y, W, DL_H)
            y += dlh
        if self.files:
            L["div2"] = y
            L["shelf_title"] = QRectF(22, y + 8, 220, 16)
            L["clear"] = QRectF(W - 22 - 90, y + 8, 90, 16)
            L["chips"] = [QRectF(22 + i * 76, y + 30, 72, 56) for i in range(len(self._shown()))]
        return L

    def _qlayout(self):
        W = W_EXP
        L = {}
        L["back"] = QRectF(10, 10, 110, 32)
        L["tiles"] = [QRectF(18 + i * 104, 52, 92, 72) for i in range(4)]
        L["vol"] = QRectF(22, 142, W - 44, 36)
        y = 190
        if self.bri.available:
            L["bri"] = QRectF(22, y, W - 44, 36)
            y += 48
        cw = (W - 44 - 24) / 4.0
        L["chips"] = [QRectF(22 + i * (cw + 8), y + 4, cw, 30) for i in range(4)]
        return L

    def _body_pt(self, pos):
        f = FLARE if self.notch else 0
        bw = self.width() - 2 * f
        ox = (bw - W_EXP) / 2.0
        return QPointF(pos.x() - f - ox, pos.y())

    def _hit(self, pos):
        pt = self._body_pt(pos)
        L = self._layout()
        for k in ("prev", "play", "next"):
            if L[k].adjusted(-8, -8, 8, 8).contains(pt):
                return (k,)
        if L["gear"].adjusted(-6, -6, 6, 6).contains(pt):
            return ("gear",)
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
        if L["close"].contains(pt):
            return ("close",)
        return None

    def _qhit(self, pos):
        pt = self._body_pt(pos)
        L = self._qlayout()
        if L["back"].contains(pt):
            return ("back",)
        keys = ("wifi", "bt", "air", "mute")
        for k, r in zip(keys, L["tiles"]):
            if r.contains(pt):
                return ("tile", k)
        if L["vol"].adjusted(-4, -6, 4, 6).contains(pt):
            return ("slider", "vol", L["vol"])
        if "bri" in L and L["bri"].adjusted(-4, -6, 4, 6).contains(pt):
            return ("slider", "bri", L["bri"])
        urls = ("ms-settings:network-wifi", "ms-settings:bluetooth", "ms-settings:sound", "ms-settings:display")
        for u, r in zip(urls, L["chips"]):
            if r.contains(pt):
                return ("open", u)
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
        if a["drop"] > 0.01:
            p.setOpacity(a["drop"])
            self._draw_drop(p, w)
        if a["notif"] > 0.01 and self.notif_cur is not None:
            p.setOpacity(a["notif"])
            self._draw_notif(p, w)
        if a["download"] > 0.01 and self.dl_view:
            p.setOpacity(a["download"])
            self._draw_dl(p, QRectF(0, 0, w, DL_H))
        if a["expanded"] > 0.01:
            p.setOpacity(a["expanded"])
            p.save()
            p.translate(ox, 0)
            self._draw_expanded(p)
            p.restore()
        if a["quick"] > 0.01:
            p.setOpacity(a["quick"])
            p.save()
            p.translate(ox, 0)
            self._draw_quick(p)
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
            draw_text(p, rect, "♪", F(int(rect.height() * 0.5)), QColor(255, 255, 255, 110),
                      AL.AlignCenter, elide=False)
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
        self._draw_eq(p, w - 12 - 19, H_MEDIA / 2, 16, self.accent, self.m["playing"])

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
        if self.notif_pm is not None:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(r, 14, 14)
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(r, self.notif_pm, QRectF(self.notif_pm.rect()))
            p.restore()
        else:
            col = QColor(0, 136, 204) if app == "Telegram" else QColor.fromHsv(abs(hash(app)) % 360, 150, 215)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 14, 14)
            draw_text(p, r, (app[:1] or "?").upper(), F(24, QFont.Weight.DemiBold), QColor(255, 255, 255),
                      AL.AlignCenter, elide=False)
        tw = w - 80 - 16
        draw_text(p, QRectF(80, 11, tw, 14), app.upper(), F(10, QFont.Weight.DemiBold),
                  QColor(255, 255, 255, 120))
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
        green = QColor(48, 209, 88)
        blue = QColor(10, 132, 255)
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

    def _draw_expanded(self, p):
        L = self._layout()
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

        g = L["gear"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawEllipse(g)
        icon_gear(p, g.center().x(), g.center().y(), white)

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
        draw_text(p, L["time"], now.strftime("%H:%M"), F(26, QFont.Weight.DemiBold), white)
        draw_text(p, L["date"], ru_date(now), F(12), gray)

        right = W - 22
        wd = self.wx
        if wd:
            t = int(round(wd["temp"]))
            ts = "0°" if t == 0 else "%+d°" % t
            tf = F(26, QFont.Weight.DemiBold)
            p.setFont(tf)
            tw = p.fontMetrics().horizontalAdvance(ts)
            draw_text(p, QRectF(right - tw - 2, 170, tw + 4, 32), ts, tf, white,
                      AL.AlignRight | AL.AlignVCenter, elide=False)
            draw_text(p, QRectF(right - tw - 2 - 40, 170, 36, 32), wd["emoji"], EF(24), white,
                      AL.AlignCenter, elide=False)
            info = wd["city"] + (" · " if wd["city"] and wd["desc"] else "") + wd["desc"]
            draw_text(p, QRectF(right - 230, 202, 230, 16), info, F(12), gray,
                      AL.AlignRight | AL.AlignVCenter)
        else:
            draw_text(p, QRectF(right - 200, 170, 200, 32), "Погода загружается…", F(12), gray,
                      AL.AlignRight | AL.AlignVCenter)

        if "dl" in L:
            p.setPen(QPen(QColor(255, 255, 255, 28), 1))
            p.drawLine(QPointF(22, L["dl"].top()), QPointF(W - 22, L["dl"].top()))
            self._draw_dl(p, L["dl"])

        if self.files:
            p.setPen(QPen(QColor(255, 255, 255, 28), 1))
            p.drawLine(QPointF(22, L["div2"]), QPointF(W - 22, L["div2"]))
            draw_text(p, L["shelf_title"], "Файлы на полке: %d" % len(self.files),
                      F(12, QFont.Weight.DemiBold), gray)
            draw_text(p, L["clear"], "Очистить", F(12, QFont.Weight.DemiBold), QColor(10, 132, 255),
                      AL.AlignRight | AL.AlignVCenter)
            for (idx, path), r in zip(self._shown(), L["chips"]):
                pm = self._icon(path)
                if not pm.isNull():
                    p.drawPixmap(QRectF(r.center().x() - 17, r.top(), 34, 34), pm, QRectF(pm.rect()))
                draw_text(p, QRectF(r.left(), r.top() + 38, r.width(), 15), os.path.basename(path),
                          F(11), QColor(255, 255, 255, 200), AL.AlignCenter)

    def _draw_slider(self, p, rect, frac, emoji, enabled=True):
        rr = rect.height() / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40 if enabled else 22))
        p.drawRoundedRect(rect, rr, rr)
        if frac is not None and enabled:
            fw = max(rect.height(), rect.width() * max(0.0, min(1.0, frac)))
            p.setBrush(QColor(255, 255, 255, 238))
            p.drawRoundedRect(QRectF(rect.left(), rect.top(), fw, rect.height()), rr, rr)
        draw_text(p, QRectF(rect.left() + 10, rect.top(), 30, rect.height()), emoji, EF(17),
                  QColor(255, 255, 255), AL.AlignCenter, elide=False)
        if not enabled:
            draw_text(p, QRectF(rect.left() + 44, rect.top(), rect.width() - 54, rect.height()),
                      "недоступно", F(12), QColor(255, 255, 255, 90))

    def _draw_quick(self, p):
        L = self._qlayout()
        white = QColor(255, 255, 255)
        W = W_EXP
        draw_text(p, L["back"], "‹  Назад", F(14, QFont.Weight.DemiBold), QColor(10, 132, 255))
        draw_text(p, QRectF(0, 10, W, 32), "Быстрые настройки", F(15, QFont.Weight.DemiBold), white,
                  AL.AlignCenter)

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
            p.drawRoundedRect(r, 20, 20)
            ic = QColor(0, 0, 0) if active else QColor(255, 255, 255, 255 if enabled else 90)
            cx, ty = r.center().x(), r.top()
            if key == "wifi":
                icon_wifi(p, cx, ty + 36, ic)
            elif key == "bt":
                icon_bt(p, cx, ty + 27, ic)
            elif key == "air":
                draw_text(p, QRectF(r.left(), ty + 8, r.width(), 34), "✈", SF(26), ic, AL.AlignCenter,
                          elide=False)
            else:
                draw_text(p, QRectF(r.left(), ty + 8, r.width(), 34), "🔇" if self.muted else "🔊", EF(24),
                          ic, AL.AlignCenter, elide=False)
            draw_text(p, QRectF(r.left(), ty + 48, r.width(), 18), label, F(11, QFont.Weight.DemiBold),
                      QColor(0, 0, 0) if active else QColor(255, 255, 255, 255 if enabled else 90),
                      AL.AlignCenter)

        self._draw_slider(p, L["vol"], self.volv, "🔇" if self.muted else "🔊", self.vol.available)
        if "bri" in L:
            bv = (self.bri.value / 100.0) if self.bri.value is not None else None
            self._draw_slider(p, L["bri"], bv, "☀️", True)
        labels = ("Wi-Fi", "Bluetooth", "Звук", "Экран")
        for lab, r in zip(labels, L["chips"]):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 15, 15)
            draw_text(p, r, lab, F(12, QFont.Weight.DemiBold), QColor(10, 132, 255), AL.AlignCenter)

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
        if disp == "quick":
            hit = self._qhit(pos)
            if not hit:
                return
            k = hit[0]
            if k == "back":
                self.set_mode("expanded")
            elif k == "open":
                self._open_uri(hit[1])
            elif k == "slider":
                self._slider = hit[1]
                self._slider_rect = hit[2]
                self._slider_set(hit[1], hit[2], pos.x())
            elif k == "tile":
                self._tile_action(hit[1])
            return
        if disp != "expanded":
            return
        hit = self._hit(pos)
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
        elif k == "gear":
            self.set_mode("quick")
        elif k == "weather":
            self.open_city_picker()
        elif k == "close":
            self.set_mode("compact")
        self.update()

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
    # выбор города
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

    # --- функции ---
    a_notif = QAction("Показывать уведомления Windows (Telegram и др.)", menu)
    a_notif.setCheckable(True)
    a_notif.setChecked(bool(settings["notifications"]))
    a_notif.toggled.connect(lambda v: settings.__setitem__("notifications", bool(v)))
    menu.addAction(a_notif)

    a_dl = QAction("Показывать загрузки файлов", menu)
    a_dl.setCheckable(True)
    a_dl.setChecked(bool(settings["downloads"]))
    a_dl.toggled.connect(lambda v: settings.__setitem__("downloads", bool(v)))
    menu.addAction(a_dl)

    a_city = QAction("Выбрать город для погоды…", menu)
    a_city.triggered.connect(island.open_city_picker)
    menu.addAction(a_city)
    menu.addSeparator()

    ext_dir = os.path.join(app_dir(), "browser_extension")
    if os.path.isdir(ext_dir):
        a_ext = QAction("Папка расширения для браузера (точные загрузки)", menu)
        a_ext.triggered.connect(lambda: os.startfile(ext_dir))
        menu.addAction(a_ext)
    a_nset = QAction("Параметры уведомлений Windows…", menu)
    a_nset.triggered.connect(lambda: island._open_uri("ms-settings:notifications"))
    menu.addAction(a_nset)
    menu.addSeparator()

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
