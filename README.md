# 📦 Посылка.kg — Telegram Web App & Сервис межгородских передач

Сервис для отправки и поиска посылок по Кыргызстану, СНГ и РФ с брутальным мужским интерфейсом, защитой от спама и единой базой данных **`kg.db`**.

---

## 🛠 Технологический стек
- **Backend:** Python 3.11+, FastAPI, Aiogram 3 (Telegram Bot), Uvicorn
- **Database:** `aiosqlite` (асинхронный SQLite, единый файл `kg.db`)
- **Frontend / TWA:** HTML5, Tailwind CSS, Alpine.js, Lucide Custom Vector Icons (четкие векторные SVG-иконки вместо стандартных эмодзи Telegram)

---

## 🚀 Переменные окружения (Railway Variables)

Для развертывания на Railway добавьте следующие переменные во вкладке **Variables**:

| Переменная | Описание | Пример значения |
|---|---|---|
| `BOT_TOKEN` | Токен Telegram-бота из [@BotFather](https://t.me/BotFather) | `7123456789:AAH...` |
| `WEBAPP_URL` | Публичный URL вашего проекта на Railway | `https://posylka-kg.up.railway.app` |
| `DB_PATH` | Путь к единой базе данных SQLite | `kg.db` (или `/data/kg.db` при подключении Railway Volume) |
| `ADMIN_ID` | Ваш числовой Telegram ID | `123456789` |
| `PORT` | Порт (Railway устанавливает автоматически) | `8000` |
| `HOST` | Хост для запуска | `0.0.0.0` |

---

## 📂 Структура проекта
- `database.py` — Схема базы данных `kg.db` на `aiosqlite` с JSON-полями и индексами.
- `crud.py` — Функции выборки, гео-валидация номеров (`+996`, `+992`, `+998`, `+7`), анти-спам таймер (30 мин), алгоритм выдачи ленты и система отзывов.
- `main.py` — FastAPI сервер + интеграция с Aiogram 3 в едином процессе.
- `bot.py` — Обработчики Telegram-бота (`/start`, кнопка отправки контакта, запуск WebApp).
- `templates/index.html` — Брутальный Dark Mode интерфейс Telegram Web App.
- `requirements.txt` — Список Python-библиотек.
- `Dockerfile` — Контейнер для Railway / VPS.
