import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import settings
import database
import crud
from bot import bot, dp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("posylka_kg.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Инициализация единой SQLite базы данных kg.db
    logger.info("Initializing database: %s", settings.DB_PATH)
    await database.init_db()

    # 2. Запуск фонового поллинга Telegram-бота (если передан токен)
    bot_task = None
    if bot and settings.BOT_TOKEN:
        logger.info("Starting Telegram Bot Polling...")
        bot_task = asyncio.create_task(dp.start_polling(bot))

    yield

    # Остановка бота
    if bot_task:
        bot_task.cancel()
        if bot:
            await bot.session.close()
        logger.info("Telegram Bot stopped.")


app = FastAPI(
    title="Посылка.kg API & WebApp",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключение статических файлов и шаблонов
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------
# PYDANTIC МОДЕЛИ ЗАПРОСОВ
# ---------------------------------------------------------

class CreateTripRequest(BaseModel):
    driver_id: int
    from_city: str
    to_city: str
    cargo_type: str = "ЛЮБОЙ"
    departure_time_type: str = "СЕГОДНЯ"
    price: int = Field(ge=0, description="Стоимость в сомах")
    extra_metadata: Optional[Dict[str, Any]] = None

class UpdateTripRequest(BaseModel):
    driver_id: int
    price: Optional[int] = None
    departure_time_type: Optional[str] = None
    cargo_type: Optional[str] = None
    extra_metadata: Optional[Dict[str, Any]] = None

class CreateOrderRequest(BaseModel):
    trip_id: int
    sender_id: int
    extra_metadata: Optional[Dict[str, Any]] = None

class CreateReviewRequest(BaseModel):
    order_id: int
    sender_id: int
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = None
    review_data: Optional[Dict[str, Any]] = None

class CreateReportRequest(BaseModel):
    reporter_id: int
    reported_driver_id: int
    reason: str
    description: Optional[str] = None
    proof_files: Optional[List[str]] = None


# ---------------------------------------------------------
# ЭНДПОИНТЫ ДЛЯ FRONTEND (TWA)
# ---------------------------------------------------------

@app.get("/")
async def get_webapp_index(request: Request, user_id: Optional[int] = None):
    """Главная страница Telegram Web App"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user_id": user_id or 0,
        "app_title": "Посылка.kg"
    })


@app.get("/api/cities")
async def get_cities():
    cities = await crud.get_all_cities()
    return {"cities": cities}


@app.get("/api/feed")
async def get_feed(
    from_city: Optional[str] = None,
    to_city: Optional[str] = None,
    cargo_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Лента объявлений с приоритетом верифицированных водителей и минимальной цены
    """
    trips = await crud.get_trips_feed(
        from_city=from_city,
        to_city=to_city,
        cargo_type=cargo_type,
        limit=limit,
        offset=offset
    )
    return {"trips": trips}


@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int):
    user = await crud.get_user(user_id)
    if not user:
        # Автоматическая базовая регистрация
        user = await crud.get_or_create_user(telegram_id=user_id, full_name="Пользователь")
    return {"user": user}


@app.post("/api/anti-spam/check")
async def check_anti_spam(user_id: int = Query(...), action: str = "button_click"):
    """
    Проверка спам-замка и фиксация действия
    """
    spam_status = await crud.check_and_record_anti_spam(user_id, action)
    return spam_status


@app.post("/api/trips")
async def create_new_trip(data: CreateTripRequest):
    """
    Создание рейса водителем с проверкой анти-спама
    """
    spam_check = await crud.check_and_record_anti_spam(data.driver_id, "create_trip")
    if spam_check.get("blocked"):
        raise HTTPException(
            status_code=429,
            detail=spam_check.get("message")
        )

    trip_id = await crud.create_trip(
        driver_id=data.driver_id,
        from_city=data.from_city,
        to_city=data.to_city,
        cargo_type=data.cargo_type,
        departure_time_type=data.departure_time_type,
        price=data.price,
        extra_metadata=data.extra_metadata
    )
    return {"success": True, "trip_id": trip_id}


@app.get("/api/trips/my")
async def get_my_trips(driver_id: int = Query(...)):
    trips = await crud.get_driver_trips(driver_id)
    return {"trips": trips}


@app.patch("/api/trips/{trip_id}")
async def update_my_trip(trip_id: int, data: UpdateTripRequest):
    updated = await crud.update_trip(
        trip_id=trip_id,
        driver_id=data.driver_id,
        price=data.price,
        departure_time_type=data.departure_time_type,
        cargo_type=data.cargo_type,
        extra_metadata=data.extra_metadata
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Не удалось обновить рейс.")
    return {"success": True}


@app.post("/api/trips/{trip_id}/close")
async def close_my_trip(trip_id: int, driver_id: int = Query(...)):
    closed = await crud.close_trip(trip_id, driver_id)
    if not closed:
        raise HTTPException(status_code=400, detail="Не удалось завершить рейс.")
    return {"success": True}


@app.post("/api/orders")
async def create_new_order(data: CreateOrderRequest):
    spam_check = await crud.check_and_record_anti_spam(data.sender_id, "create_order")
    if spam_check.get("blocked"):
        raise HTTPException(status_code=429, detail=spam_check.get("message"))

    order_id = await crud.create_order(
        trip_id=data.trip_id,
        sender_id=data.sender_id,
        extra_metadata=data.extra_metadata
    )
    return {"success": True, "order_id": order_id}


@app.post("/api/reviews")
async def add_review(data: CreateReviewRequest):
    """
    Создание отзыва (активно строго после DELIVERED и через 3 часа после принятия)
    """
    success, message, review_id = await crud.create_review(
        order_id=data.order_id,
        sender_id=data.sender_id,
        rating=data.rating,
        comment=data.comment,
        review_data=data.review_data
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "review_id": review_id, "message": message}


@app.post("/api/reports")
async def add_report(data: CreateReportRequest):
    report_id = await crud.create_report(
        reporter_id=data.reporter_id,
        reported_driver_id=data.reported_driver_id,
        reason=data.reason,
        description=data.description,
        proof_files=data.proof_files
    )
    return {"success": True, "report_id": report_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
