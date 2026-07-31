FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

ENV PEP_DATA_DIR=/data/pep

ENTRYPOINT ["python", "scripts/update_pep.py"]
CMD ["--interval", "168"]
