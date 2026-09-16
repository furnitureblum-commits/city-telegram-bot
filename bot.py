import asyncio
import os
import logging
import sqlite3
import requests
from dotenv import load_dotenv
from urllib.parse import quote

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

from flask import Flask
import threading

load_dotenv()

DB_FILE = "cache.db"


# ============================================================
# SQLITE КЭШ
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS city_cache (
        query TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS area_cache (
        lat REAL, lon REAL, area TEXT, PRIMARY KEY (lat, lon)
    )""")
    conn.commit()
    conn.close()


def cache_get_city(query):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT name, lat, lon FROM city_cache WHERE query = ?",
        (query.lower().strip(),)
    ).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "lat": row[1], "lon": row[2]}
    return None


def cache_set_city(query, name, lat, lon):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO city_cache VALUES (?, ?, ?, ?)",
        (query.lower().strip(), name, lat, lon)
    )
    conn.commit()
    conn.close()


def cache_get_area(lat, lon):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT area FROM area_cache WHERE lat = ? AND lon = ?",
        (round(lat, 2), round(lon, 2))
    ).fetchone()
    conn.close()
    return row[0] if row else None


def cache_set_area(lat, lon, area):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO area_cache VALUES (?, ?, ?)",
        (round(lat, 2), round(lon, 2), area)
    )
    conn.commit()
    conn.close()


# ============================================================
# НОРМАЛИЗАЦИЯ ОБЛАСТЕЙ
# ============================================================
def normalize_area(s):
    if not s:
        return ""
    s = s.lower().strip()
    for word in ["область", "край", "республика", "респ.", "г.", "город"]:
        s = s.replace(word, "")
    return s.strip()


# ============================================================
# СПИСОК ГОРОДОВ
# ============================================================
CITIES = [
    {"name": "Москва", "lat": 55.7558, "lon": 37.6173, "area": "Москва"},
    {"name": "Санкт-Петербург", "lat": 59.9343, "lon": 30.3351, "area": "Санкт-Петербург"},
    {"name": "Петрозаводск", "lat": 61.7849, "lon": 34.3469, "area": "Карелия"},
    {"name": "Саратов", "lat": 51.5336, "lon": 46.0343, "area": "Саратовская"},
    {"name": "Нижний Новгород", "lat": 56.3269, "lon": 44.0059, "area": "Нижегородская"},
    {"name": "Новосибирск", "lat": 55.0084, "lon": 82.9357, "area": "Новосибирская"},
    {"name": "Краснодар", "lat": 45.0355, "lon": 38.9753, "area": "Краснодарский"},
    {"name": "Мурманск", "lat": 68.9585, "lon": 33.0827, "area": "Мурманская"}
]

MAX_DISTANCE = 500

logging.basicConfig(level=logging.INFO)

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()


# ============================================================
# ПОИСК ГОРОДА
# ============================================================
def search_city(query: str):
    cached = cache_get_city(query)
    if cached:
        logging.info(f"Кэш: город '{query}'")
        return cached

    api_key = os.getenv("MAPTILER_KEY")
    encoded_query = quote(query)
    url = f"https://api.maptiler.com/geocoding/{encoded_query}.json"
    params = {"key": api_key, "language": "ru", "limit": 1}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("features"):
            feat = data["features"][0]
            lon, lat = feat["geometry"]["coordinates"]
            result = {"name": feat["place_name"], "lat": lat, "lon": lon}
            cache_set_city(query, result["name"], lat, lon)
            return result
    except Exception as e:
        logging.error(f"MapTiler search error: {e}")
    return None


# ============================================================
# ОПРЕДЕЛЕНИЕ ОБЛАСТИ
# ============================================================
def get_area(lat: float, lon: float):
    cached = cache_get_area(lat, lon)
    if cached:
        logging.info(f"Кэш: область для {lat},{lon}")
        return cached

    api_key = os.getenv("MAPTILER_KEY")
    url = f"https://api.maptiler.com/geocoding/{lon},{lat}.json"
    params = {"key": api_key, "language": "ru", "limit": 1, "types": "region"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("features"):
            feat = data["features"][0]
            context = feat.get("context", [])
            area = None
            for item in context:
                if item.get("id", "").startswith("region"):
                    area = item.get("text")
                    break
            if not area and context:
                area = context[0].get("text")
            if area:
                cache_set_area(lat, lon, area)
                return area
    except Exception as e:
        logging.error(f"MapTiler reverse error: {e}")
    return None


# ============================================================
# РАСЧЁТ МАРШРУТА (С ГЕОМЕТРИЕЙ)
# ============================================================
def get_route(from_coords, to_coords):
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{from_coords['lon']},{from_coords['lat']};"
        f"{to_coords['lon']},{to_coords['lat']}"
    )
    params = {"overview": "simplified", "geometries": "geojson"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "distance": round(route["distance"] / 1000),
                "duration": round(route["duration"] / 60),
                "geometry": route.get("geometry")
            }
    except Exception as e:
        logging.error(f"OSRM error: {e}")
    return {"distance": float("inf"), "duration": 0, "geometry": None}


def format_duration(minutes):
    hours = minutes // 60
    mins = minutes % 60
    if hours == 0:
        return f"{mins} мин"
    if mins == 0:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


# ============================================================
# КАРТА (MapTiler Static Maps)
# ============================================================
def encode_polyline(coords, precision=5):
    """Кодирует [[lat, lon], ...] в формат Google Polyline."""
    factor = 10 ** precision
    result = []
    prev_lat = 0
    prev_lon = 0
    for lat, lon in coords:
        lat_i = int(round(lat * factor))
        lon_i = int(round(lon * factor))
        d_lat = lat_i - prev_lat
        d_lon = lon_i - prev_lon
        prev_lat = lat_i
        prev_lon = lon_i
        for v in (d_lat, d_lon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                result.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            result.append(chr(v + 63))
    return ''.join(result)


def build_map_url(client_city, nearest_city, geometry):
    api_key = os.getenv("MAPTILER_KEY")
    if not api_key or not geometry:
        return None
    try:
        coords = geometry["coordinates"]  # [[lon, lat], ...]
        max_points = 60
        if len(coords) > max_points:
            step = max(1, len(coords) // max_points)
            coords = coords[::step]
            if coords[-1] != geometry["coordinates"][-1]:
                coords.append(geometry["coordinates"][-1])

        latlon = [[c[1], c[0]] for c in coords]
        encoded = encode_polyline(latlon)
        encoded_safe = quote(encoded, safe='')

        url = (
            f"https://api.maptiler.com/maps/streets-v2/static/auto/800x500.png"
            f"?key={api_key}"
            f"&markers={client_city['lon']},{client_city['lat']},red"
            f"&markers={nearest_city['lon']},{nearest_city['lat']},blue"
            f"&path=weight:5|color:0x4a90e2|enc:{encoded_safe}"
        )
        if len(url) > 2000:
            logging.warning(f"URL карты слишком длинный ({len(url)}), упрощаем")
            url = (
                f"https://api.maptiler.com/maps/streets-v2/static/auto/800x500.png"
                f"?key={api_key}"
                f"&markers={client_city['lon']},{client_city['lat']},red"
                f"&markers={nearest_city['lon']},{nearest_city['lat']},blue"
            )
        return url
    except Exception as e:
        logging.error(f"Map URL error: {e}")
        return None


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу найти ближайший город из списка к твоему населённому пункту.\n\n"
        "Просто напиши мне название города, например: Тверь или Колпино."
    )


@dp.message()
async def handle_city(message: Message):
    city_name = message.text.strip()
    if len(city_name) < 3:
        await message.answer("Пожалуйста, введи более полное название (минимум 3 буквы).")
        return

    await message.answer(f"🔍 Ищу «{city_name}»...")

    client_city = search_city(city_name)
    if not client_city:
        await message.answer("😔 Не удалось найти такой населённый пункт.")
        return

    client_area = normalize_area(get_area(client_city["lat"], client_city["lon"]))

    results = []
    for city in CITIES:
        route = get_route(client_city, city)
        city_area = normalize_area(city["area"])

        if route["distance"] <= MAX_DISTANCE:
            status = "green"
        elif client_area and city_area and client_area == city_area:
            status = "red"
        else:
            status = "grey"

        results.append({
            "city": city,
            "distance": route["distance"],
            "duration": route["duration"],
            "status": status,
            "geometry": route.get("geometry")
        })

    results.sort(key=lambda x: x["distance"])

    text_lines = [f"📍 Ваш город: {client_city['name'].split(',')[0]}\n"]
    for res in results:
        name = res["city"]["name"]
        dist = res["distance"]
        if res["status"] == "green":
            text_lines.append(f"🟢 {name} — {dist} км ({format_duration(res['duration'])})")
        elif res["status"] == "red":
            text_lines.append(f"🔴 {name} — {dist} км")
        else:
            text_lines.append(f"⚪ {name} — {dist} км")
    text = "\n".join(text_lines)

    # Карта для ближайшего города
    nearest = results[0]
    map_url = build_map_url(client_city, nearest["city"], nearest.get("geometry"))

    if map_url:
        try:
            await message.answer_photo(photo=map_url, caption=text)
            return
        except Exception as e:
            logging.error(f"Не удалось отправить карту: {e}")

    await message.answer(text)


# ============================================================
# FLASK ДЛЯ RENDER
# ============================================================
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

@app.route("/health")
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
