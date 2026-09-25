# -*- coding: utf-8 -*-
"""Wave 4 live verification: Arabic create -> bind -> ask -> delete -> detach."""
import json
import time
import urllib.request

KEY = open("D:/hwk-data/aali_master_key.txt", encoding="utf-8").read().strip()
BASE = "http://127.0.0.1:5055"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "X-API-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=90) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# 1. create with REAL Arabic
code, out = req("/api/assistants", "POST", {
    "name": "مدرّس الرياضيات", "icon": "🧑‍🏫",
    "tagline": "يشرح خطوة بخطوة",
    "instruction": "اشرح ببطء وبالعربية وبتأني"})
assert code == 201 and out["ok"], out
aid = out["assistant"]["id"]
print("1. create 201:", out["assistant"]["name"], "|", out["assistant"]["icon"])

# 2. create a session via a first ask (bind needs an existing session)
sid = f"live-w4-utf8-{int(time.time())}"
code, out = req("/api/ask", "POST", {"message": "hi", "sid": sid,
                                     "policy": "always_ask"})
assert out.get("ok"), out
print("2. session created via ask:", sid)

# 3. bind
code, out = req("/api/assistants/active", "POST",
                {"sid": sid, "assistant_id": aid})
assert code == 200 and out["ok"] and out["assistant"]["id"] == aid, out
print("3. bind 200:", out["assistant"]["name"])

# 4. ask with persona bound -> session record must lead with persona
code, out = req("/api/ask", "POST", {"message": "مرحبا", "sid": sid,
                                     "policy": "always_ask"})
assert out.get("ok"), out
key = f"u{KEY}:{sid}"
rec = next(json.loads(l) for l in open("file-agent/sessions.jsonl",
                                       encoding="utf-8")
           if json.loads(l).get("key") == key)
last_user = [t["content"] for t in rec["turns"]
             if t["role"] == "user"][-1]
assert last_user.startswith("[أنت الآن تتصرف كمساعد مخصص باسم «مدرّس الرياضيات»"), \
    last_user[:120]
assert "تعليمات الشخصية: اشرح ببطء وبالعربية وبتأني" in last_user
print("4. persona leads the stored message (proper Arabic) ✓")
print("   brain replied:", out["reply"][:80])

# 5. unbind -> persona gone
code, out = req("/api/assistants/active", "POST", {"sid": sid, "assistant_id": None})
assert code == 200 and out["assistant"] is None
print("5. unbind ok ✓")

# 6. re-bind, then DELETE -> all sessions detach
req("/api/assistants/active", "POST", {"sid": sid, "assistant_id": aid})
code, out = req(f"/api/assistants/{aid}", "DELETE")
assert code == 200 and out["ok"], out
rec = json.loads(open("file-agent/sessions.jsonl", encoding="utf-8").readline())  # noqa
with open("file-agent/sessions.jsonl", encoding="utf-8") as fh:
    rec = next(json.loads(l) for l in fh if json.loads(l).get("key") == key)
assert "assistant_id" not in rec, rec.get("assistant_id")
print("6. delete detached the session bind ✓")

# 7. store is now empty
code, out = req("/api/assistants")
assert out["assistants"] == [], out
print("7. store empty after delete ✓")
print("ALL LIVE CHECKS PASSED")
