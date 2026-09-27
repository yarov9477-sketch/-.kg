import aiosqlite
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any, Dict, List
from config import settings

logger = logging.getLogger("posylka_kg.database")

@asynccontextmanager
async def get_db():
    """
    Асинхронный контекстный менеджер подключения к единой SQLite базе (kg.db).
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA journal_mode = WAL;")
        yield db

async def init_db():
    """
    Инициализация таблиц и индексов базы данных kg.db.
    """
    async with get_db() as db:
        # 1. Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'SENDER',
                full_name TEXT NOT NULL,
                phone_number TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                spam_blocked_until TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                extra_data TEXT DEFAULT '{}'
            );
        """)

        # 2. Таблица верификации водителей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS driver_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                passport_photo TEXT,
                license_photo TEXT,
                selfie_photo TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                admin_notes TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (driver_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );
        """)

        # 3. Таблица городов и направлений
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                popularity_score INTEGER NOT NULL DEFAULT 0
            );
        """)

        # 4. Таблица рейсов / объявлений водителей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                from_city TEXT NOT NULL,
                to_city TEXT NOT NULL,
                cargo_type TEXT NOT NULL,
                departure_time_type TEXT NOT NULL,
                price INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                extra_metadata TEXT DEFAULT '{}',
                FOREIGN KEY (driver_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );
        """)

        # 5. Таблица заказов / бронирований посылок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                last_location TEXT,
                accepted_at TIMESTAMP,
                completed_at TIMESTAMP,
                extra_metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );
        """)

        # 6. Таблица отзывов (с защитой от накрутки)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER UNIQUE NOT NULL,
                driver_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
                comment TEXT,
                review_data TEXT DEFAULT '{}',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE,
                FOREIGN KEY (driver_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );
        """)

        # 7. Таблица жалоб
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                reported_driver_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                description TEXT,
                proof_files TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (reporter_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
                FOREIGN KEY (reported_driver_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );
        """)

        # 8. Таблица логов кликов / действий для Anti-Spam Lockout (5 действий / 60 сек -> 30 мин бан)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS spam_action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Создание оптимизированных индексов
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trips_cities ON trips (from_city, to_city, status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trips_driver ON trips (driver_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_trip ON orders (trip_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_sender ON orders (sender_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_driver ON reviews (driver_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_spam_logs ON spam_action_logs (user_id, created_at);")

        # Начальный сидинг популярных городов
        initial_cities = [
            ("Бишкек", 100),
            ("Ош", 95),
            ("Жалал-Абад", 85),
            ("Каракол", 80),
            ("Чолпон-Ата", 75),
            ("Нарын", 70),
            ("Талас", 70),
            ("Баткен", 65),
            ("Кызыл-Кия", 60),
            ("Токмок", 60),
            ("Балыкчы", 55),
            ("Кадамжай", 50),
            ("Москва", 90),
            ("Екатеринбург", 70),
            ("Новосибирск", 65),
            ("Санкт-Петербург", 60),
            ("Алматы", 80),
            ("Ташкент", 65),
            ("Душанбе", 55)
        ]
        await db.executemany("""
            INSERT OR IGNORE INTO cities (name, popularity_score) VALUES (?, ?)
        """, initial_cities)

        await db.commit()
        logger.info("Database initialized successfully at: %s", settings.DB_PATH)
