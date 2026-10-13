FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8766 HOST=0.0.0.0 DB_PATH=/data/packages.db \
    COOKIE_FILE=/data/taobao.cookie
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app \
    && mkdir -p /data && chown app:app /data
COPY server /app/server
USER app
EXPOSE 8766
VOLUME ["/data"]
CMD ["python", "-m", "server.app"]
