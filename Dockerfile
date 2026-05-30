FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p uploads/avatars uploads/posts uploads/activities uploads/submissions uploads/site_assets

EXPOSE 5000

CMD ["sh", "-c", "gunicorn -b 0.0.0.0:5000 -w ${GUNICORN_WORKERS:-2} --access-logfile - --error-logfile - wsgi:app"]
