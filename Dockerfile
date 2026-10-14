FROM python:3.12-slim

WORKDIR /app

# Timezone embutido no container (ajuste via env TIMEZONE também).
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

CMD ["python", "main.py"]
