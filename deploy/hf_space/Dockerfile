# Aali server — Hugging Face Space variant.
# HF Spaces expose one port (default 7860) and run containers as a non-root
# user, so this differs from the repo-root Dockerfile: PORT=7860, no root steps.
FROM python:3.12-slim

WORKDIR /app
COPY file-agent/requirements.txt /app/file-agent/requirements.txt
RUN pip install --no-cache-dir -r file-agent/requirements.txt waitress

COPY file-agent /app/file-agent
COPY web/dist /app/web/dist

ENV PORT=7860
EXPOSE 7860

# Spaces run as user 1000 — sessions.jsonl and the workspace must be writable.
RUN useradd -m -u 1000 aali && mkdir -p /app/file-agent/agent_workspace && chown -R aali:aali /app
USER aali

CMD ["waitress-serve", "--host=0.0.0.0", "--port=7860", "app:app"]
