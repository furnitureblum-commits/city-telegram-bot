# ============================================================
# ИМПОРТЫ — подключаем нужные библиотеки
# ============================================================
# asyncio — стандартная библиотека Python для асинхронности.
# Асинхронность нужна, чтобы бот мог обслуживать много пользователей
# одновременно и не "зависал", пока ждёт ответа от внешнего сервиса.
import asyncio

# os — стандартная библиотека. Нужна, чтобы прочитать переменные
# окружения (в нашем случае — токен из .env).
import os

# logging — стандартная библиотека для вывода сообщений в консоль.
# Используем, чтобы видеть ошибки и события бота.
import logging

# requests — сторонняя библиотека. Умеет делать HTTP-запросы
# (как браузер, только без окна). Через неё обращаемся к картам.
import requests

# load_dotenv — функция из библиотеки python-dotenv.
# Читает файл .env и подставляет значения как переменные окружения.
from dotenv import load_dotenv
from flask import Flask
import threading

# Импортируем классы и функции из aiogram — библиотеки для Telegram.
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

# ============================================================
# ЗАГРУЗКА ТОКЕНА
# ============================================================
# Читаем файл .env, который лежит рядом с этим файлом.
load_dotenv()

# ============================================================
# КОНСТАНТЫ ПРОЕКТА
# ============================================================
# Список городов, с которыми сравниваем город клиента.
# Каждый город — это словарь: имя + координаты (широта, долгота).
CITIES = [
    {"name": "Москва", "lat": 55.7558, "lon": 37.6173},
    {"name": "Санкт-Петербург", "lat": 59.9343, "lon": 30.3351},
    {"name": "Петрозаводск", "lat": 61.7849, "lon": 34.3469},
    {"name": "Саратов", "lat": 51.5336, "lon": 46.0343},
    {"name": "Нижний Новгород", "lat": 56.3269, "lon": 44.0059},
    {"name": "Новосибирск", "lat": 55.0084, "lon": 82.9357},
    {"name": "Краснодар", "lat": 45.0355, "lon": 38.9753},
    {"name": "Мурманск", "lat": 68.9585, "lon": 33.0827}
]

# Максимальное расстояние в км, при котором город считается "зелёным".
MAX_DISTANCE = 500

# Настройка логирования: будем видеть в терминале все INFO-сообщения.
# Полезно при отладке — сразу видно, что бот делает.
logging.basicConfig(level=logging.INFO)

# ============================================================
# СОЗДАЁМ ОБЪЕКТЫ БОТА И ДИСПЕТЧЕРА
# ============================================================
# Bot — "телефонная трубка", через неё отправляем запросы к Telegram.
# Токен берём из переменной окружения BOT_TOKEN (из .env).
bot = Bot(token=os.getenv("BOT_TOKEN"))

# Dispatcher — "мозг", который принимает входящие сообщения
# и решает, какой обработчик вызвать. Представь, что это телефонная
# станция, которая переключает звонки на нужный отдел.
dp = Dispatcher()


# ============================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ВНЕШНИМИ СЕРВИСАМИ
# ============================================================

def search_city(query: str):
    """Ищет координаты через MapTiler Geocoding API."""
    api_key = os.getenv("MAPTILER_KEY")
    # URL-кодируем запрос (пробелы заменяются на %20 и т.д.)
    encoded_query = requests.utils.quote(query)
    url = f"https://api.maptiler.com/geocoding/{encoded_query}.json"
    params = {
        "key": api_key,
        "language": "ru",
        "limit": 1
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("features"):
            feat = data["features"][0]
            # В GeoJSON координаты идут в порядке [долгота, широта]
            lon, lat = feat["geometry"]["coordinates"]
            return {
                "name": feat["place_name"],
                "lat": lat,
                "lon": lon
            }
    except Exception as e:
        logging.error(f"MapTiler search error: {e}")
    return None
    
    try:
        # Отправляем запрос. timeout=10 — если сервер не ответит за 10 секунд,
        # выкинем ошибку (чтобы бот не висел вечно).
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        # Если сервер вернул ошибку (4xx, 5xx) — бросит исключение.
        response.raise_for_status()
        
        # Преобразуем ответ из JSON-строки в Python-объект (список словарей).
        data = response.json()
        
        # Если что-то нашли:
        if data:
            return {
                "name": data[0]["display_name"],  # полное имя, напр. "Тверь, Тверская область, Россия"
                "lat": float(data[0]["lat"]),     # широта
                "lon": float(data[0]["lon"])      # долгота
            }
    except Exception as e:
        # Если что-то пошло не так — запишем ошибку в лог, но не упадём.
        logging.error(f"Ошибка поиска города: {e}")
    
    # Если ничего не нашли или была ошибка — вернём None.
    return None


def get_area(lat: float, lon: float):
    """Определяет область по координатам через MapTiler Reverse Geocoding."""
    api_key = os.getenv("MAPTILER_KEY")
    # MapTiler принимает координаты в формате "долгота,широта"
    url = f"https://api.maptiler.com/geocoding/{lon},{lat}.json"
    params = {
        "key": api_key,
        "language": "ru",
        "limit": 1,
        "types": "region"  # просим только регионы (области, края)
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("features"):
            feat = data["features"][0]
            # Пытаемся достать название региона из контекста
            context = feat.get("context", [])
            for item in context:
                if item.get("id", "").startswith("region"):
                    return item.get("text")
            # Если регион не найден, возвращаем первый контекст
            if context:
                return context[0].get("text")
    except Exception as e:
        logging.error(f"MapTiler reverse error: {e}")
    return None
def get_route(from_coords: dict, to_coords: dict):
    """
    Считает расстояние и время в пути по дорогам через OSRM.
    
    Вход:  from_coords, to_coords — словари с ключами lat, lon.
    Выход: {"distance": км, "duration": минуты} или {"distance": inf, ...}
           если маршрут не найден.
    
    Почему OSRM, а не "по прямой"? Потому что по прямой из Твери до
    Москвы ~160 км, а по дорогам — около 180. Разница существенная,
    и клиенту важна реальная дистанция.
    """
    # OSRM требует координаты в формате lon,lat — ВНИМАНИЕ, lon идёт первым!
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{from_coords['lon']},{from_coords['lat']};"
        f"{to_coords['lon']},{to_coords['lat']}"
    )
    # overview=false — не нужна форма маршрута, только цифры.
    # Для бота это не важно, а ответ приходит быстрее.
    params = {"overview": "false"}
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Проверяем, что сервис вернул именно маршрут.
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "distance": round(route["distance"] / 1000),  # метры → км
                "duration": round(route["duration"] / 60)     # секунды → минуты
            }
    except Exception as e:
        logging.error(f"Ошибка расчёта маршрута: {e}")
    
    # Если маршрут не нашёлся — бесконечность (этот город уйдёт вниз списка).
    return {"distance": float("inf"), "duration": 0}


def format_duration(minutes: int) -> str:
    """
    Превращает минуты в красивую строку: "2 ч 15 мин", "45 мин", "3 ч".
    Просто для читаемости ответа бота.
    """
    hours = minutes // 60    # целочисленное деление — сколько полных часов
    mins = minutes % 60      # остаток — сколько минут сверху
    
    if hours == 0:
        return f"{mins} мин"
    if mins == 0:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


# ============================================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# ============================================================
# Обработчик — это функция, которую aiogram вызывает, когда
# приходит подходящее сообщение. Мы "приклеиваем" обработчик к событию
# с помощью декоратора @dp.message(...).

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """
    Срабатывает на команду /start.
    async def — асинхронная функция. Так надо, потому что aiogram
    сам работает асинхронно. Не переживай, синтаксис такой же,
    просто перед def пишется async, а перед вызовом тяжёлых
    операций — await.
    """
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу найти ближайший город из списка к твоему населённому пункту.\n\n"
        "Просто напиши мне название города, например: Тверь или Колпино."
    )


@dp.message()
async def handle_city(message: Message):
    """
    Срабатывает на ЛЮБОЕ текстовое сообщение (после /start).
    Здесь вся логика: ищем город клиента, считаем расстояния
    до всех 8 городов, формируем ответ.
    """
    # Берём текст сообщения и убираем пробелы в начале и конце.
    city_name = message.text.strip()
    
    # Если ввели меньше 3 букв — вежливо просим уточнить.
    if len(city_name) < 3:
        await message.answer("Пожалуйста, введи более полное название (минимум 3 буквы).")
        return  # return прерывает функцию — идём спать, ничего не делаем
    
    # Сообщаем пользователю, что начали работу (иначе он будет думать, что бот завис).
    await message.answer(f"🔍 Ищу «{city_name}»...")
    
    # 1. Ищем координаты города клиента через Nominatim.
    client_city = search_city(city_name)
    if not client_city:
        await message.answer(
            "😔 Не удалось найти такой населённый пункт. Попробуй написать по-другому."
        )
        return
    
    # 2. Определяем область клиента (пригодится для проверки "красного" условия).
    client_area = get_area(client_city["lat"], client_city["lon"])
    
    # Здесь будем накапливать результаты по каждому городу из списка.
    results = []
    
    # 3. Проходимся по всем 8 городам.
    for city in CITIES:
        # Считаем расстояние и время от клиента до текущего города.
        route = get_route(client_city, city)
        
        # Узнаём область текущего города из списка.
        city_area = get_area(city["lat"], city["lon"])
        
        # Определяем цвет города по правилам.
        if route["distance"] <= MAX_DISTANCE:
            status = "green"                       # 🟢 ближе 500 км
        elif client_area and city_area and client_area == city_area:
            status = "red"                         # 🔴 далеко, но та же область
        else:
            status = "grey"                        # ⚪ далеко и другая область
        
        # Сохраняем результат по этому городу в общий список.
        results.append({
            "city": city,
            "distance": route["distance"],
            "duration": route["duration"],
            "status": status
        })
    
    # 4. Сортируем список по расстоянию — от ближайшего к дальнему.
    # key=lambda x: x["distance"] говорит: "сортируй по полю distance".
    results.sort(key=lambda x: x["distance"])
    
    # 5. Собираем красивый текст ответа.
    # \n — символ переноса строки.
    text_lines = [f"📍 Ваш город: {client_city['name'].split(',')[0]}\n"]
    
    for res in results:
        name = res["city"]["name"]
        dist = res["distance"]
        
        if res["status"] == "green":
            # Для зелёных показываем ещё и время в пути.
            time_str = format_duration(res["duration"])
            text_lines.append(f"🟢 {name} — {dist} км ({time_str})")
        elif res["status"] == "red":
            text_lines.append(f"🔴 {name} — {dist} км (в той же области)")
        else:
            text_lines.append(f"⚪ {name} — {dist} км")
    
    # Объединяем все строки в одну через перенос строки и отправляем.
    await message.answer("\n".join(text_lines))


# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def main():
    """
    Главная функция. Запускает бесконечный цикл опроса Telegram.
    polling — это режим, когда наш бот сам постоянно спрашивает
    Telegram: "есть новые сообщения?" — и, если есть, забирает их.
    Альтернатива — webhook, когда Telegram сам стучится к нам.
    Но polling проще для локального запуска и Render.
    """
    # Удаляем вебхук (если он был от предыдущего запуска) и
    # сбрасываем накопившиеся старые сообщения, чтобы бот не отвечал
    # на то, что пришло, пока он был выключен.
    # ============================================================
# FLASK-СЕРВЕР ДЛЯ RENDER
# ============================================================
# Render ожидает, что сервис слушает порт. Flask-сервер просто
# отвечает "OK" на любой запрос. Это нужно, чтобы Render не убивал
# наш сервис, думая, что он сломан.
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

@app.route("/health")
def health():
    return "OK"

def run_flask():
    """Запускает Flask-сервер в отдельном потоке."""
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def main():
    # Удаляем вебхук и сбрасываем старые сообщения
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен! Отправь ему сообщение в Telegram.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке (он занимает порт)
    threading.Thread(target=run_flask, daemon=True).start()
    # Запускаем бота в основном потоке
    asyncio.run(main())
