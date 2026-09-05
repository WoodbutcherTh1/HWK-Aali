"""Mobile-friendly Flask interface for the local file agent."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, render_template_string, request

from agent_loop import AgentLoopError, agent_loop


app = Flask(__name__)
WORKSPACE_ROOT = Path(
    os.getenv("AGENT_WORKSPACE", Path(__file__).resolve().parent / "agent_workspace")
).expanduser().resolve()

PAGE = """\
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>وكيل الملفات المحلي</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #0f172a; color: #e2e8f0; }
    main { width: min(760px, 100%); margin: auto; padding: 28px 16px 48px; }
    h1 { margin: 0 0 8px; font-size: clamp(1.7rem, 6vw, 2.4rem); }
    .subtitle { color: #94a3b8; line-height: 1.7; margin: 0 0 24px; }
    form, .response, .error { padding: 16px; border: 1px solid #334155; border-radius: 16px; background: #111c33; }
    label { display: block; margin-bottom: 8px; font-weight: 700; }
    textarea { width: 100%; min-height: 140px; resize: vertical; padding: 13px; border: 1px solid #475569; border-radius: 12px; background: #0b1220; color: #f8fafc; font: inherit; line-height: 1.6; }
    textarea:focus { outline: 2px solid #38bdf8; outline-offset: 2px; }
    button { width: 100%; margin-top: 14px; padding: 13px 18px; border: 0; border-radius: 11px; background: #38bdf8; color: #082f49; font: inherit; font-weight: 800; cursor: pointer; }
    button:hover { background: #7dd3fc; }
    .response, .error { margin-top: 18px; white-space: pre-wrap; line-height: 1.75; }
    .response h2, .error h2 { margin: 0 0 8px; font-size: 1rem; }
    .error { border-color: #f87171; color: #fecaca; }
    .meta { color: #64748b; font-size: .86rem; }
    select { width: 100%; margin-bottom: 14px; padding: 12px; border: 1px solid #475569; border-radius: 12px; background: #0b1220; color: #f8fafc; font: inherit; }
  </style>
</head>
<body>
  <main>
    <h1>وكيل الملفات المحلي</h1>
    <p class="subtitle">اكتب طلبك بدون API key، وسيقرأ الوكيل الملفات أو يعدّلها داخل مجلد العمل المسموح فقط.</p>
    <form method="post">
      <label for="mode">طريقة التشغيل</label>
      <select id="mode" name="mode">
        <option value="local" {% if mode == "local" %}selected{% endif %}>نموذج محلي / وضع بلا مفتاح</option>
        <option value="cloud" {% if mode == "cloud" %}selected{% endif %}>نموذج سحابي اختياري</option>
      </select>
      <label for="message">طلبك</label>
      <textarea id="message" name="message" required placeholder="مثال: اقرأ notes/today.txt وأضف في نهايته ملخصاً قصيراً">{{ message }}</textarea>
      <button type="submit">إرسال الطلب</button>
    </form>
    {% if response %}<section class="response"><h2>رد الوكيل</h2>{{ response }}</section>{% endif %}
    {% if error %}<section class="error"><h2>حدث خطأ</h2>{{ error }}</section>{% endif %}
    <p class="meta">مجلد العمل: {{ workspace }}</p>
  </main>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home() -> str:
    message = ""
    response = None
    error = None
    mode = "local"
    if request.method == "POST":
        message = request.form.get("message", "")
        mode = request.form.get("mode", "local")
        try:
            response = agent_loop(
                message,
                WORKSPACE_ROOT,
                mode=mode,
                print_final=False,
            )
        except AgentLoopError as exc:
            error = str(exc)
    return render_template_string(
        PAGE,
        message=message,
        response=response,
        error=error,
        mode=mode,
        workspace=WORKSPACE_ROOT,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)