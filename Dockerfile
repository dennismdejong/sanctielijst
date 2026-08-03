FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY data/risk_countries.json /app/risk_countries.json

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

ENV PEP_DATA_DIR=/data/pep
ENV PYTHONUNBUFFERED=1
ENV PEP_INDEX_SUBPROCESS=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
