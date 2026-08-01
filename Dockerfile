FROM python:3.12-slim

WORKDIR /app

# ffmpeg: audio normalization for web voice transcription (wayfinder #6).
# Browser MediaRecorder outputs webm/mp4; Gemini needs OGG/Opus, so we convert.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Belt-and-suspenders: also create /data at build time in case no Volume
# is mounted yet. If you attach a Railway Volume at /data, its content
# persists across deploys; without one, this dir still exists so the
# container doesn't crash on first boot, but checkpoints won't survive
# a redeploy.
RUN mkdir -p /data

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]