"""Mock Aali API for UI development and testing — NO GPU, NO real brain.

Serves canned /api/* responses (same shapes as file-agent/app.py) plus the
built web app from web/dist at /ui/. Used to verify the desktop client
end-to-end while Phase A trains. NOT part of the product.

    .venv/Scripts/python.exe scripts/mock_aali_api.py --port 5099
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from flask import Flask, Response, make_response, request, send_from_directory

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

app = Flask(__name__)

SESSIONS: dict[str, list[dict]] = {}


@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-Id, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


CANNED_REPLY = (
    "تم! أنشأت التطبيق داخل مساحة العمل وشغّلته للتأكد أنه يعمل.\n\n"
    "```python\n# app_demo.py\nprint(\"مرحباً من آلي ✦\")\n```\n\n"
    "**ما فعلته:**\n1. كتبت الملف\n2. شغّلته وتحققت من الخرج\n"
    "- لا أخطاء\n- الخرج مطابق\n\nالخطوة التالية مقترحة بالأسفل 👇"
)


@app.route("/api/health")
def health():
    return {"ok": True, "service": "aali", "workspace": "MOCK"}


@app.route("/api/ask", methods=["POST"])
def ask():
    p = request.get_json(silent=True) or {}
    sid = str(p.get("sid") or uuid.uuid4().hex)
    msg = str(p.get("message", ""))
    SESSIONS.setdefault(sid, []).append({"role": "user", "content": msg, "ts": time.time()})
    SESSIONS[sid].append({"role": "assistant", "content": CANNED_REPLY, "ts": time.time()})
    return {
        "ok": True,
        "reply": CANNED_REPLY,
        "sid": sid,
        "suggestions": ["أضف اختباراً للتطبيق", "اشرح لي الكود سطراً بسطر", "حوّله إلى واجهة رسومية"],
    }


@app.route("/api/ask/stream", methods=["POST"])
def ask_stream():
    p = request.get_json(silent=True) or {}
    sid = str(p.get("sid") or uuid.uuid4().hex)
    msg = str(p.get("message", ""))
    SESSIONS.setdefault(sid, []).append({"role": "user", "content": msg, "ts": time.time()})
    SESSIONS[sid].append({"role": "assistant", "content": CANNED_REPLY, "ts": time.time()})

    def gen():
        steps = [
            ("provider_selected", {"provider": "ollama", "model": "aali-brain"}),
            ("tool_requested", {"tool": "write_file", "arguments": {"path": "app_demo.py", "input": "print('hi')"}}),
            ("tool_result", {"tool": "write_file", "result": "written 2 lines"}),
            ("tool_requested", {"tool": "run_command", "arguments": {"command": "python app_demo.py"}}),
            ("tool_result", {"tool": "run_command", "result": "مرحباً من آلي ✦"}),
        ]
        for ev, data in steps:
            yield f"event: activity\ndata: {json.dumps({'event': ev, **data}, ensure_ascii=False)}\n\n"
            time.sleep(0.7)
        body = {
            "ok": True,
            "reply": CANNED_REPLY,
            "sid": sid,
            "suggestions": ["أضف اختباراً للتطبيق", "اشرح لي الكود سطراً بسطر", "حوّله إلى واجهة رسومية"],
        }
        yield f"event: done\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/sessions")
def sessions():
    rows = []
    for sid, turns in SESSIONS.items():
        title = next((t["content"][:60] for t in turns if t["role"] == "user"), "محادثة")
        rows.append({"sid": sid, "title": title, "turns": len(turns),
                     "updated_at": time.time()})
    return {"ok": True, "sessions": rows}


@app.route("/api/session/<sid>")
def session_get(sid):
    return {"ok": True, "sid": sid, "turns": SESSIONS.get(sid, [])}


@app.route("/api/session/<sid>", methods=["DELETE"])
def session_del(sid):
    SESSIONS.pop(sid, None)
    return {"ok": True}


@app.route("/api/compact", methods=["POST"])
def compact():
    return {"ok": True, "compacted": True, "summary": "ملخص تجريبي للمحادثة.", "kept_turns": 4}


@app.route("/ui/")
@app.route("/ui/<path:filename>")
def ui(filename: str = "index.html"):
    return send_from_directory(WEB_DIST, filename)


@app.route("/")
def root():
    return make_response("<a href='/ui/'>افتح تطبيق آلي</a>")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5099)
    args = parser.parse_args()
    print(f"Mock Aali API on http://127.0.0.1:{args.port}/ui/")
    app.run(host="127.0.0.1", port=args.port, debug=False)
