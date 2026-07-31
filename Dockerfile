FROM python:3.12-slim

WORKDIR /app

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
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
