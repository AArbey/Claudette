FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S aihelper && \
    adduser -S -G aihelper -h /app aihelper && \
    mkdir -p /app /client /config /knowledge /web /data && \
    chown -R aihelper:aihelper /app /data

COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY --chown=aihelper:aihelper server/brain.py server/web_tools.py /app/
COPY --chown=aihelper:aihelper client/ /client/
COPY --chown=aihelper:aihelper server/config/ /config/
COPY --chown=aihelper:aihelper server/knowledge/ /knowledge/
COPY --chown=aihelper:aihelper server/web/ /web/

USER aihelper
WORKDIR /app

EXPOSE 8080 8081
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os; from urllib.request import urlopen; urlopen('http://127.0.0.1:' + os.getenv('BRAIN_PORT', '8080') + '/readyz', timeout=2).read()"

CMD ["python", "/app/brain.py"]
