"""n8n workflow generator for Aali.

Turns a short Arabic/English description into a valid n8n workflow JSON the
user can import into their n8n editor (Workflows → Import from file/clipboard).
Pattern: trigger node → action nodes → (optional) respond node.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------- templates --
# Each builder returns a node dict. IDs are filled in by the assembler.

def _webhook_trigger() -> dict[str, Any]:
    return {
        "parameters": {"httpMethod": "POST", "path": "aali", "responseMode": "responseNode", "options": {}},
        "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
        "position": [-380, 0],
    }


def _schedule_trigger(expression: str = "0 */1 * * *") -> dict[str, Any]:
    return {
        "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": expression}]}},
        "name": "Schedule", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
        "position": [-380, 0],
    }


def _telegram_send(text_expr: str) -> dict[str, Any]:
    return {
        "parameters": {"chatId": "={{ $json.chatId || 'YOUR_CHAT_ID' }}",
                       "text": text_expr, "additionalFields": {}},
        "name": "Telegram", "type": "n8n-nodes-base.telegram", "typeVersion": 1.2,
        "position": [180, 0],
    }


def _gmail_trigger() -> dict[str, Any]:
    return {
        "parameters": {"pollTimes": [{"item": {"mode": "everyHour"}}], "simple": True,
                       "filters": {}},
        "name": "Gmail Trigger", "type": "n8n-nodes-base.gmailTrigger", "typeVersion": 1.2,
        "position": [-380, 0],
    }


def _http_get(url: str) -> dict[str, Any]:
    return {
        "parameters": {"url": url, "options": {}},
        "name": "HTTP GET", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "position": [-120, 0],
    }


def _aali_ask() -> dict[str, Any]:
    """Call Aali's own brain API inside the workflow."""
    return {
        "parameters": {
            "method": "POST", "url": "http://127.0.0.1:5055/api/ask",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ message: $json.message || $json.text || 'مرحبا', sid: 'n8n-flow', mode: 'local' }) }}",
            "options": {},
        },
        "name": "Aali Brain", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "position": [-120, 0],
    }


def _set_fields(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "parameters": {"assignments": {"assignments": [
            {"id": f"f{i}", "name": k, "value": v, "type": "string"}
            for i, (k, v) in enumerate(fields.items())
        ]}, "options": {}},
        "name": "Set", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
        "position": [60, 0],
    }


def _respond() -> dict[str, Any]:
    return {
        "parameters": {"respondWith": "allIncomingItems", "options": {}},
        "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1,
        "position": [420, 0],
    }


# ------------------------------------------------------------------ parser --

_TRIGGERS = [
    (r"كل يوم|يومياً|يوميا|كل ساعة|كل ساع|جدول|schedule|cron|دوري", "schedule"),
    (r"بريد|إيميل|ايميل|gmail|email", "gmail"),
    (r"webhook|ويبهوك|ويب هوك|endpoint", "webhook"),
]

_ACTIONS = [
    (r"تيليجرام|telegram", "telegram"),
    (r"آلي|aali|الدماغ|النموذج|ملخص ذكي|summar", "aali"),
    (r"موقع|رابط|http|api|url|جلب", "http"),
]

def _classify(text: str, table: list[tuple[str, str]], default: str | None = None) -> str | None:
    for pattern, kind in table:
        if re.search(pattern, text, re.IGNORECASE):
            return kind
    return default


def _uuid() -> str:
    import uuid

    return uuid.uuid4().hex[:24]


def _connect(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    connections: dict[str, Any] = {}
    for a, b in zip(nodes, nodes[1:]):
        connections.setdefault(a["name"], {"main": [[{"node": b["name"], "type": "main", "index": 0}]]})
    return connections


def generate_workflow(description: str) -> dict[str, Any]:
    """Build an importable n8n workflow dict from a natural-language description."""
    text = description.strip()
    # Priority: explicit timing > email > webhook (default). A URL alone must
    # not flip the trigger to webhook — that's an action, not a trigger.
    trigger_kind: str = "webhook"
    for pattern, kind in _TRIGGERS:
        if re.search(pattern, text, re.IGNORECASE):
            trigger_kind = kind
            break
    trigger = {
        "webhook": _webhook_trigger,
        "schedule": _schedule_trigger,
        "gmail": _gmail_trigger,
    }[trigger_kind]()

    chain: list[dict[str, Any]] = [trigger]
    action_kinds: list[str] = []

    aali_wanted = bool(re.search(r"آلي|aali|الدماغ|النموذج|لخص|ملخص|summar", text, re.IGNORECASE))
    telegram_wanted = bool(re.search(r"تيليجرام|telegram", text, re.IGNORECASE))

    if trigger_kind == "gmail" and not telegram_wanted and not aali_wanted:
        # Summarize new emails with Aali by default
        aali_wanted = True

    if aali_wanted:
        chain.append(_aali_ask())
        action_kinds.append("aali")
    else:
        url_match = re.search(r"https?://\S+", text)
        if url_match:
            chain.append(_http_get(url_match.group(0).rstrip('.,،"'))
                         )
            action_kinds.append("http")

    if telegram_wanted:
        chain.append(_set_fields({"text": "={{ $json.reply || $json.text || $json.body }}"}))
        chain.append(_telegram_send("={{ $json.text }}"))
        action_kinds.append("telegram")

    if not action_kinds:
        # Fallback: store/echo through Aali's brain so the flow still does something real
        chain.append(_aali_ask())
        action_kinds.append("aali")

    if trigger_kind == "webhook":
        chain.append(_respond())

    for node in chain:
        node.setdefault("id", _uuid())
    # re-space positions evenly
    for i, node in enumerate(chain):
        node["position"] = [-380 + i * 220, 0]

    return {
        "name": ("Aali · " + text[:48]) or "Aali workflow",
        "nodes": chain,
        "connections": _connect(chain),
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }


def safe_filename(text: str) -> str:
    stem = re.sub(r"[^\w\u0600-\u06FF-]+", "_", text[:40]).strip("_") or "aali_workflow"
    return stem + ".json"


if __name__ == "__main__":
    import sys

    desc = " ".join(sys.argv[1:]) or "عند وصول بريد جديد لخصه وأرسله إلى تيليجرام"
    workflow = generate_workflow(desc)
    print(json.dumps(workflow, ensure_ascii=False, indent=1))
