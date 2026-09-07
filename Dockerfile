# Aali server — the brain + API that web / desktop / CLI / terminal clients share.
FROM python:3.12-slim

WORKDIR /app
COPY file-agent/requirements.txt /app/file-agent/requirements.txt
RUN pip install --no-cache-dir -r file-agent/requirements.txt waitress

COPY file-agent /app/file-agent
COPY web/dist /app/web/dist
COPY aali_cli.py /app/aali_cli.py

ENV PORT=5055
EXPOSE 5055

# Production WSGI server (waitress) — app:app is the Flask object in file-agent/app.py
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5055", "app:app"]
