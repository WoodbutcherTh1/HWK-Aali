"""Ask the Aali brain on the PC, over the LAN.

Why HTTP and not SSH
--------------------
Jarvis runs on a Pi that shares the LAN with the PC, and the brain already
speaks HTTP on :5055. POSTing the owner's question straight to
``/api/ask`` means three things an SSH pipe could not promise:

* **No quoting.** A question is arbitrary text -- Arabic, quotes, ``$``,
  semicolons, newlines. Putting it in a remote shell string is how a question
  turns into a command. A JSON body has no such failure mode.
* **No SSH dependency.** The PC's SSH story is still being set up, and the
  brain is reachable over plain HTTP from anywhere on the house.
* **The right tool for the job.** The brain owns the files, the sandbox and the
  tools; "ask her to do X" and "do X" are then the same request, instead of
  Jarvis pretending to be a second, weaker version of it.

The key is optional. A brain running in key mode answers a uniform 404 to an
unauthenticated caller, and the honest response to that is to say so and name
the file the owner has to drop in -- never to report a fake answer.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .machines import Machine

# The brain runs a full agent loop with tools, so a real question legitimately
# takes a while. Short enough to keep the Telegram answer timely, long enough
# that a two-tool answer is not cut off mid-way.
TIMEOUT = 90
MAX_QUESTION = 2000
MAX_REPLY = 3000
# One conversation per Jarvis session id, so "and now the second file" still
# lands in the same thread on the brain's side.
SID = "jarvis"


def api_base(machine: Machine) -> str:
    """The brain's base URL for a machine (its api_port, else 5055)."""
    return f"http://{machine.ip}:{machine.api_port or 5055}"


def ask(
    api_key: str,
    machine: Machine,
    question: str,
    sid: str = SID,
    timeout: int = TIMEOUT,
) -> tuple[bool, str]:
    """Send one question to the brain. Returns ``(ok, text_or_reason)``.

    ``text_or_reason`` is the reply on success and a short English reason on
    failure; the caller frames both in Arabic. Never raises.
    """
    body = (question or "").strip()
    if not body:
        return False, "empty question"
    if len(body) > MAX_QUESTION:
        body = body[:MAX_QUESTION]

    payload = json.dumps({"message": body, "sid": sid}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(
        f"{api_base(machine)}/api/ask", data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        # The PC may be asleep, rebooting, or off the network entirely.
        return False, type(exc).__name__
    try:
        data: dict[str, Any] = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, "reply was not JSON"
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return False, "brain returned no reply"
    reply = reply.strip()
    if len(reply) > MAX_REPLY:
        reply = reply[:MAX_REPLY] + " …(مقطوع)"
    return True, reply