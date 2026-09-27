FROM python:3.11-slim

WORKDIR /app

# Отключаем буферизацию вывода
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Порт по умолчанию для Railway
EXPOSE 8000

# Запуск приложения
CMD ["python", "main.py"]
