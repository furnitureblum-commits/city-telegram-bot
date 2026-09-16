import asyncio
import os
import logging
import requests
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

from flask import Flask
import threading

load_dotenv()

# ============================================================
# КЭШ В ПАМЯТИ
# ============================================================
_cache = {}

def cache_get_city(query):
    return _cache.get(f"city:{query.lower().strip()}")

def cache_set_city(query, name, lat, lon):
    _cache[f"city:{query.lower().strip()}"] = {"name": name, "lat": lat, "lon": lon}

def cache_get_area(lat, lon):
    return _cache.get(f"area:{round(lat, 2)},{round(lon, 2)}")

def cache_set_area(lat, lon, area):
    _cache[f"area:{round(lat, 2)},{round(lon, 2)}"] = area


# ============================================================
# НОРМАЛИЗАЦИЯ НАЗВАНИЙ ОБЛАСТЕЙ
# ============================================================
# Убираем слова "область", "край", "республика" и т.д., чтобы
# "Саратовская область" и "Саратовская" считались одним и тем же.
def normalize_area(s):
    if not s:
        return ""
    s = s.lower().strip()
    for word in ["область", "край", "республика", "респ.", "г.", "город"]:
        s = s.replace(word, "")
    return s.strip()


# ============================================================
# СПИСОК ГОРОДОВ С ОБЛАСТЯМИ
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
# ПОИСК ГОРОДА (С КЭШЕМ)
# ============================================================
def search_city(query: str):
    cached = cache_get_city(query)
    if cached:
        logging.info(f"Кэш: город '{query}'")
        return cached

    api_key = os.getenv("MAPTILER_KEY")
    encoded_query = requests.utils.quote(query)
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
# ОПРЕДЕЛЕНИЕ ОБЛАСТИ (С КЭШЕМ)
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
# РАСЧЁТ МАРШРУТА (OSRM)
# ============================================================
def get_route(from_coords, to_coords):
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{from_coords['lon']},{from_coords['lat']};"
        f"{to_coords['lon']},{to_coords['lat']}"
    )
    params = {"overview": "false"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "distance": round(route["distance"] / 1000),
                "duration": round(route["duration"] / 60)
            }
    except Exception as e:
        logging.error(f"OSRM error: {e}")
    return {"distance": float("inf"), "duration": 0}


def format_duration(minutes):
    hours = minutes // 60
    mins = minutes % 60
    if hours == 0:
        return f"{mins} мин"
    if mins == 0:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


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
        await message.answer("😔 Не удалось найти такой населённый пункт. Попробуй написать по-другому.")
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
            "status": status
        })

    results.sort(key=lambda x: x["distance"])

    text_lines = [f"📍 Ваш город: {client_city['name'].split(',')[0]}\n"]
    for res in results:
        name = res["city"]["name"]
        dist = res["distance"]
        if res["status"] == "green":
            time_str = format_duration(res["duration"])
            text_lines.append(f"🟢 {name} — {dist} км ({time_str})")
        elif res["status"] == "red":
            text_lines.append(f"🔴 {name} — {dist} км")
        else:
            text_lines.append(f"⚪ {name} — {dist} км")

    await message.answer("\n".join(text_lines))


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
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен! Отправь ему сообщение в Telegram.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
