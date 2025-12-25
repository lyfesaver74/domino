FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py personas.py schemas.py memory_store.py console.html tts_fish.py ./
COPY static ./static

EXPOSE 2424

CMD ["python", "main.py"]
