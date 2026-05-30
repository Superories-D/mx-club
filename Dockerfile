FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system --gid 10001 app && adduser --system --uid 10001 --ingroup app app
COPY --chown=app:app . .
RUN mkdir -p uploads/avatars uploads/posts uploads/activities uploads/submissions uploads/site_assets \
    && chown -R app:app /app

USER app

EXPOSE 5000

CMD ["sh", "-c", "gunicorn -b 0.0.0.0:5000 -w ${GUNICORN_WORKERS:-2} --access-logfile - --error-logfile - wsgi:app"]
