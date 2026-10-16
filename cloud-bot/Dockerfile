FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN useradd --create-home bot
USER bot
ENV MOCK_SEND=true
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 60 app:app"]
