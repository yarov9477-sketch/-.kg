import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from database import get_db

logger = logging.getLogger("posylka_kg.crud")

# ---------------------------------------------------------
# 1. ПОЛЬЗОВАТЕЛИ И ГЕО-ФИЛЬТР ТЕЛЕФОННЫХ НОМЕРОВ
# ---------------------------------------------------------

ALLOWED_PHONE_PREFIXES = ("+996", "+992", "+998", "+7", "996", "992", "998", "7", "8")

def normalize_and_validate_phone(phone: str) -> Tuple[str, str]:
    """
    Нормализует номер телефона и проверяет гео-фильтр:
    Разрешены префиксы: +996 (КР), +992 (РТ), +998 (РУз), +7 (РФ/РК).
    Если код другой — статус 'PUMP_TO_MODERATION'.
    """
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not cleaned.startswith("+"):
        if cleaned.startswith("8") and len(cleaned) == 11:
            cleaned = "+7" + cleaned[1:]
        elif cleaned.startswith("7") and len(cleaned) == 11:
            cleaned = "+7" + cleaned[1:]
        elif cleaned.startswith("996"):
            cleaned = "+" + cleaned
        elif cleaned.startswith("992"):
            cleaned = "+" + cleaned
        elif cleaned.startswith("998"):
            cleaned = "+" + cleaned
        else:
            cleaned = "+" + cleaned

    # Проверка гео-префикса
    if cleaned.startswith(("+996", "+992", "+998", "+7")):
        status = "ACTIVE"
    else:
        status = "PUMP_TO_MODERATION"

    return cleaned, status


async def get_or_create_user(
    telegram_id: int,
    full_name: str,
    phone_number: Optional[str] = None,
    role: str = "SENDER"
) -> Dict[str, Any]:
    """
    Получает пользователя или создает нового с учетом гео-валидации номера.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cursor.fetchone()

        if row:
            user = dict(row)
            # Обновление номера телефона при необходимости
            if phone_number and (not user.get("phone_number") or user.get("phone_number") != phone_number):
                clean_phone, status = normalize_and_validate_phone(phone_number)
                await db.execute(
                    "UPDATE users SET phone_number = ?, status = ? WHERE telegram_id = ?",
                    (clean_phone, status, telegram_id)
                )
                await db.commit()
                user["phone_number"] = clean_phone
                user["status"] = status
            return user

        # Новый пользователь
        clean_phone = None
        status = "ACTIVE"
        if phone_number:
            clean_phone, status = normalize_and_validate_phone(phone_number)

        await db.execute("""
            INSERT INTO users (telegram_id, role, full_name, phone_number, status)
            VALUES (?, ?, ?, ?, ?)
        """, (telegram_id, role, full_name, clean_phone, status))
        await db.commit()

        cursor = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        new_row = await cursor.fetchone()
        return dict(new_row)


async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_user_role(telegram_id: int, new_role: str):
    async with get_db() as db:
        await db.execute("UPDATE users SET role = ? WHERE telegram_id = ?", (new_role, telegram_id))
        await db.commit()


# ---------------------------------------------------------
# 2. ЗАЩИТА ОТ СПАМА (ANTI-CLICK SPAM / LOCKOUT 30 MIN)
# ---------------------------------------------------------

async def check_and_record_anti_spam(user_id: int, action_type: str = "click") -> Dict[str, Any]:
    """
    Алгоритм анти-спама:
    1. Проверяет, заблокирован ли уже пользователь.
    2. Логирует действие в `spam_action_logs`.
    3. Считает действия за последние 60 секунд.
    4. Если действий >= 5: блокировка на 30 минут (`spam_blocked_until`).
    """
    now = datetime.utcnow()
    async with get_db() as db:
        # Проверка текущей блокировки
        cursor = await db.execute("SELECT spam_blocked_until FROM users WHERE telegram_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row["spam_blocked_until"]:
            try:
                blocked_until = datetime.fromisoformat(row["spam_blocked_until"].replace("Z", ""))
                if blocked_until > now:
                    remaining_seconds = int((blocked_until - now).total_seconds())
                    return {
                        "blocked": True,
                        "remaining_seconds": remaining_seconds,
                        "just_blocked": False,
                        "message": "Вы совершили слишком много действий. Доступ ограничен на 30 минут."
                    }
            except Exception as e:
                logger.error(f"Error parsing spam_blocked_until: {e}")

        # Логирование текущего действия
        await db.execute(
            "INSERT INTO spam_action_logs (user_id, action_type, created_at) VALUES (?, ?, ?)",
            (user_id, action_type, now.isoformat())
        )

        # Подсчет действий за последние 60 секунд
        time_threshold = (now - timedelta(seconds=60)).isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM spam_action_logs WHERE user_id = ? AND created_at >= ?",
            (user_id, time_threshold)
        )
        count_row = await cursor.fetchone()
        actions_count = count_row["count"] if count_row else 1

        if actions_count >= 5:
            lockout_time = now + timedelta(minutes=30)
            await db.execute(
                "UPDATE users SET spam_blocked_until = ? WHERE telegram_id = ?",
                (lockout_time.isoformat(), user_id)
            )
            await db.commit()
            return {
                "blocked": True,
                "remaining_seconds": 1800,
                "just_blocked": True,
                "message": "Вы совершили слишком много действий. Доступ ограничен на 30 минут."
            }

        await db.commit()
        return {"blocked": False, "remaining_seconds": 0}


# ---------------------------------------------------------
# 3. ВЕРИФИКАЦИЯ ВОДИТЕЛЕЙ (✓ ВЕРИФИЦИРОВАН)
# ---------------------------------------------------------

async def submit_driver_verification(
    driver_id: int,
    passport_photo: Optional[str] = None,
    license_photo: Optional[str] = None,
    selfie_photo: Optional[str] = None
) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO driver_verifications (driver_id, passport_photo, license_photo, selfie_photo, status)
            VALUES (?, ?, ?, ?, 'PENDING')
        """, (driver_id, passport_photo, license_photo, selfie_photo))
        await db.commit()
        return cursor.lastrowid


async def set_driver_verification_status(verification_id: int, status: str, admin_notes: Optional[str] = None):
    async with get_db() as db:
        await db.execute("""
            UPDATE driver_verifications
            SET status = ?, admin_notes = ?
            WHERE id = ?
        """, (status, admin_notes, verification_id))
        await db.commit()


async def is_driver_verified(driver_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT id FROM driver_verifications
            WHERE driver_id = ? AND status = 'APPROVED'
            LIMIT 1
        """, (driver_id,))
        return await cursor.fetchone() is not None


# ---------------------------------------------------------
# 4. CRUD ДЛЯ ВОДИТЕЛЕЙ (РЕЙСЫ / ОБЪЯВЛЕНИЯ)
# ---------------------------------------------------------

async def create_trip(
    driver_id: int,
    from_city: str,
    to_city: str,
    cargo_type: str,
    departure_time_type: str,
    price: int,
    extra_metadata: Optional[Dict[str, Any]] = None
) -> int:
    metadata_json = json.dumps(extra_metadata or {}, ensure_ascii=False)
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO trips (driver_id, from_city, to_city, cargo_type, departure_time_type, price, status, extra_metadata)
            VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
        """, (driver_id, from_city, to_city, cargo_type, departure_time_type, price, metadata_json))
        await db.commit()
        return cursor.lastrowid


async def update_trip(
    trip_id: int,
    driver_id: int,
    price: Optional[int] = None,
    departure_time_type: Optional[str] = None,
    cargo_type: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None
) -> bool:
    fields = []
    values = []

    if price is not None:
        fields.append("price = ?")
        values.append(price)
    if departure_time_type is not None:
        fields.append("departure_time_type = ?")
        values.append(departure_time_type)
    if cargo_type is not None:
        fields.append("cargo_type = ?")
        values.append(cargo_type)
    if extra_metadata is not None:
        fields.append("extra_metadata = ?")
        values.append(json.dumps(extra_metadata, ensure_ascii=False))

    if not fields:
        return False

    values.extend([trip_id, driver_id])
    query = f"UPDATE trips SET {', '.join(fields)} WHERE id = ? AND driver_id = ?"

    async with get_db() as db:
        cursor = await db.execute(query, values)
        await db.commit()
        return cursor.rowcount > 0


async def close_trip(trip_id: int, driver_id: int, status: str = "COMPLETED") -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE trips SET status = ? WHERE id = ? AND driver_id = ?",
            (status, trip_id, driver_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_trip_by_id(trip_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT t.*, u.full_name as driver_name, u.phone_number as driver_phone,
                   CASE WHEN dv.status = 'APPROVED' THEN 1 ELSE 0 END as is_verified
            FROM trips t
            JOIN users u ON t.driver_id = u.telegram_id
            LEFT JOIN driver_verifications dv ON t.driver_id = dv.driver_id AND dv.status = 'APPROVED'
            WHERE t.id = ?
        """, (trip_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        trip = dict(row)
        try:
            trip["extra_metadata"] = json.loads(trip["extra_metadata"] or "{}")
        except Exception:
            trip["extra_metadata"] = {}
        return trip


async def get_driver_trips(driver_id: int) -> List[Dict[str, Any]]:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT t.*,
                   (SELECT COUNT(*) FROM orders o WHERE o.trip_id = t.id) as orders_count
            FROM trips t
            WHERE t.driver_id = ?
            ORDER BY t.created_at DESC
        """, (driver_id,))
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            trip = dict(r)
            try:
                trip["extra_metadata"] = json.loads(trip["extra_metadata"] or "{}")
            except Exception:
                trip["extra_metadata"] = {}
            result.append(trip)
        return result


# ---------------------------------------------------------
# 5. АЛГОРИТМ ВЫДАЧИ ЛЕНТЫ ДЛЯ ОТПРАВИТЕЛЕЙ
# ---------------------------------------------------------

async def get_trips_feed(
    from_city: Optional[str] = None,
    to_city: Optional[str] = None,
    cargo_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Алгоритм сортировки выдачи:
    1) Верифицированные водители (is_verified = 1);
    2) Минимальная цена за доставку (price ASC);
    3) Актуальность (created_at DESC).
    """
    query = """
        SELECT 
            t.id, t.driver_id, t.from_city, t.to_city, t.cargo_type, 
            t.departure_time_type, t.price, t.status, t.created_at, t.extra_metadata,
            u.full_name as driver_name, u.phone_number as driver_phone,
            CASE WHEN dv.status = 'APPROVED' THEN 1 ELSE 0 END as is_verified,
            COALESCE(AVG(r.rating), 5.0) as average_rating,
            COUNT(r.id) as reviews_count
        FROM trips t
        JOIN users u ON t.driver_id = u.telegram_id
        LEFT JOIN driver_verifications dv ON t.driver_id = dv.driver_id AND dv.status = 'APPROVED'
        LEFT JOIN reviews r ON t.driver_id = r.driver_id
        WHERE t.status = 'ACTIVE' AND u.status = 'ACTIVE'
    """
    params: List[Any] = []

    if from_city:
        query += " AND t.from_city LIKE ?"
        params.append(f"%{from_city}%")
    if to_city:
        query += " AND t.to_city LIKE ?"
        params.append(f"%{to_city}%")
    if cargo_type and cargo_type != "ЛЮБОЙ":
        query += " AND (t.cargo_type = ? OR t.cargo_type = 'ЛЮБОЙ')"
        params.append(cargo_type)

    query += """
        GROUP BY t.id
        ORDER BY 
            is_verified DESC,
            t.price ASC,
            t.created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            trip = dict(r)
            try:
                trip["extra_metadata"] = json.loads(trip["extra_metadata"] or "{}")
            except Exception:
                trip["extra_metadata"] = {}
            trip["average_rating"] = round(float(trip["average_rating"]), 1)
            result.append(trip)
        return result


# ---------------------------------------------------------
# 6. ЗАКАЗЫ И ТРЕКИНГ
# ---------------------------------------------------------

async def create_order(
    trip_id: int,
    sender_id: int,
    extra_metadata: Optional[Dict[str, Any]] = None
) -> int:
    metadata_json = json.dumps(extra_metadata or {}, ensure_ascii=False)
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO orders (trip_id, sender_id, status, extra_metadata)
            VALUES (?, ?, 'CREATED', ?)
        """, (trip_id, sender_id, metadata_json))
        await db.commit()
        return cursor.lastrowid


async def update_order_status(
    order_id: int,
    status: str,
    last_location: Optional[str] = None
) -> bool:
    now = datetime.utcnow().isoformat()
    fields = ["status = ?"]
    values: List[Any] = [status]

    if last_location:
        fields.append("last_location = ?")
        values.append(last_location)

    if status == "ACCEPTED":
        fields.append("accepted_at = ?")
        values.append(now)
    elif status == "DELIVERED":
        fields.append("completed_at = ?")
        values.append(now)

    values.append(order_id)
    query = f"UPDATE orders SET {', '.join(fields)} WHERE id = ?"

    async with get_db() as db:
        cursor = await db.execute(query, values)
        await db.commit()
        return cursor.rowcount > 0


async def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT o.*, t.driver_id, t.from_city, t.to_city, t.price,
                   u_driver.full_name as driver_name, u_driver.phone_number as driver_phone,
                   u_sender.full_name as sender_name, u_sender.phone_number as sender_phone
            FROM orders o
            JOIN trips t ON o.trip_id = t.id
            JOIN users u_driver ON t.driver_id = u_driver.telegram_id
            JOIN users u_sender ON o.sender_id = u_sender.telegram_id
            WHERE o.id = ?
        """, (order_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        order = dict(row)
        try:
            order["extra_metadata"] = json.loads(order["extra_metadata"] or "{}")
        except Exception:
            order["extra_metadata"] = {}
        return order


# ---------------------------------------------------------
# 7. СИСТЕМА ОТЗЫВОВ С ЗАЩИТОЙ ОТ НАКРУТКИ И JSON-ПОЛЕМ
# ---------------------------------------------------------

async def can_leave_review(order_id: int, sender_id: int) -> Tuple[bool, str]:
    """
    1. Оставить отзыв можно ТОЛЬКО после статуса DELIVERED.
    2. Кнопка отзыва активна строго через 3-4 часа (минимально 3 часа) после принятия заказа (accepted_at).
    3. Отзыв еще не был оставлен ранее.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order_row = await cursor.fetchone()
        if not order_row:
            return False, "Заказ не найден."

        order = dict(order_row)
        if order["sender_id"] != sender_id:
            return False, "Вы не являетесь отправителем этого заказа."

        if order["status"] != "DELIVERED":
            return False, "Отзыв можно оставить только после подтверждения доставки груза (DELIVERED)."

        # Проверка существующего отзыва
        cursor = await db.execute("SELECT id FROM reviews WHERE order_id = ?", (order_id,))
        if await cursor.fetchone():
            return False, "Отзыв к этому заказу уже оставлен."

        if not order.get("accepted_at"):
            return False, "Время принятия заказа не зафиксировано."

        try:
            accepted_time = datetime.fromisoformat(order["accepted_at"].replace("Z", ""))
            min_review_time = accepted_time + timedelta(hours=3)
            now = datetime.utcnow()

            if now < min_review_time:
                remaining_mins = int((min_review_time - now).total_seconds() // 60)
                return False, f"Оставить отзыв можно будет через {remaining_mins} мин. (защита от накрутки: 3 часа после принятия)."
        except Exception as e:
            logger.error(f"Error checking review time: {e}")

        return True, "OK"


async def create_review(
    order_id: int,
    sender_id: int,
    rating: int,
    comment: Optional[str] = None,
    review_data: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, Optional[int]]:
    can_post, message = await can_leave_review(order_id, sender_id)
    if not can_post:
        return False, message, None

    review_data_json = json.dumps(review_data or {}, ensure_ascii=False)

    async with get_db() as db:
        cursor = await db.execute("""
            SELECT t.driver_id FROM orders o
            JOIN trips t ON o.trip_id = t.id
            WHERE o.id = ?
        """, (order_id,))
        row = await cursor.fetchone()
        if not row:
            return False, "Связанный рейс не найден.", None

        driver_id = row["driver_id"]

        cursor = await db.execute("""
            INSERT INTO reviews (order_id, driver_id, sender_id, rating, comment, review_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, driver_id, sender_id, rating, comment, review_data_json))
        await db.commit()
        return True, "Отзыв успешно добавлен.", cursor.lastrowid


async def get_driver_reviews(driver_id: int) -> List[Dict[str, Any]]:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT r.*, u.full_name as sender_name
            FROM reviews r
            JOIN users u ON r.sender_id = u.telegram_id
            WHERE r.driver_id = ?
            ORDER BY r.created_at DESC
        """, (driver_id,))
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            rev = dict(r)
            try:
                rev["review_data"] = json.loads(rev["review_data"] or "{}")
            except Exception:
                rev["review_data"] = {}
            result.append(rev)
        return result


# ---------------------------------------------------------
# 8. МОДУЛЬ ЖАЛОБ (REPORTS)
# ---------------------------------------------------------

async def create_report(
    reporter_id: int,
    reported_driver_id: int,
    reason: str,
    description: Optional[str] = None,
    proof_files: Optional[List[str]] = None
) -> int:
    proof_json = json.dumps(proof_files or [], ensure_ascii=False)
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO reports (reporter_id, reported_driver_id, reason, description, proof_files)
            VALUES (?, ?, ?, ?, ?)
        """, (reporter_id, reported_driver_id, reason, description, proof_json))
        await db.commit()
        return cursor.lastrowid


# ---------------------------------------------------------
# 9. СПИСОК ГОРОДОВ
# ---------------------------------------------------------

async def get_all_cities() -> List[str]:
    async with get_db() as db:
        cursor = await db.execute("SELECT name FROM cities ORDER BY popularity_score DESC, name ASC")
        rows = await cursor.fetchall()
        return [r["name"] for r in rows]
