FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY stock_sum ./stock_sum

RUN pip install --no-cache-dir . && python -m playwright install --with-deps chromium

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["stock-sum", "daemon", "--host", "0.0.0.0", "--port", "8000"]
