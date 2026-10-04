"""Natural-language intent routing for Jarvis.

The owner's ask
---------------
Talk to Jarvis in plain language and have it *obey*: "send Buffy this prompt",
"is the PC awake?", "how much RAM is left", "shut it down when you're done".
No command list, no syntax, and no typing -- half of it arrives as a voice note
that Whisper spelled however it liked.

How that is made safe
---------------------
The router never executes anything. It reads the owner's sentence and returns
the NAME of one capability from the closed table below plus a few arguments,
and the normal handlers do the work. That keeps the guarantee the power layer
already had: a Telegram message can only ever *select* an action, never supply
a command. There is no code path from a model reply to a shell.

So the trust boundary is deliberately narrow:

* ``validate`` drops any action name that is not in the table, any argument the
  capability never declared, and any value outside that argument's allowed
  choices. A hallucinated ``"run_shell"`` or a ``machine`` of ``"router"``
  cannot reach a handler.
* A required argument that is missing makes the whole decision ``None``, which
  the dispatcher treats as "no decision" and hands to ordinary chat.
* Destructive capabilities are flagged in the table and the dispatcher routes
  them through the existing two-step token, so speaking "shut down the PC"
  mints a token instead of pulling the plug.
* When the model fails, says something unusable, or the router is switched off,
  the result is ``None`` -- never a fallback guess.

Why a separate model
--------------------
The router runs on *every* free-text message, so it gets its own smaller,
faster model at ``temperature=0``. The owner's chat model stays the answerer;
this one only picks a name from a list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import groq
from .config import GroqConfig

# A classifier that daydreams is a classifier that eventually routes a shutdown
# to the wrong machine, so it runs cold with a tiny token ceiling.
INTENT_MODEL = "openai/gpt-oss-20b"
MAX_INTENT_TOKENS = 300
MAX_ARG_CHARS = 2000

# Vocabulary the handlers already know. Kept here so validation and the
# router prompt cannot drift apart.
MACHINES = ("pc", "mac", "pi", "all")
POWER_ACTIONS = ("shutdown", "reboot", "sleep")
RESOURCES = ("disk", "ram", "gpu")

# The Arabic half of the table, written as REAL TEXT and kept in one place.
#
# It was \u escapes here first, on the theory that a hand-edited block in this
# repo can silently lose literal Arabic. The escapes lost something worse: a
# mistyped sequence decodes to valid-but-nonsense Arabic, and nobody notices a
# garbled classifier prompt until an order silently routes to "chat". One table
# of real text is reviewable by eye; twenty hand-typed escapes are not.
SUMMARY_AR: dict[str, str] = {
    "status": "تقرير حالة الأجهزة: أي جهاز متصل الآن وأيها مغلق",
    "resources": "قراءة مساحة القرص أو الذاكرة أو الكارت على جهاز",
    "wake": "تشغيل جهاز نائم بإشعار الاستيقاظ",
    "power": "إطفاء أو إعادة تشغيل أو نوم جهاز (يحتاج رمز تأكيد)",
    "aali_status": "هل عقل آلي يستجيب على الحاسوب",
    "aali_ask": "سؤال أو مهمة لعقل آلي على الحاسوب، وتبّع جوابه",
    "logs": "قراءة آخر سطور من سجل على جهاز",
    "freebuff_send": "إرسال رسالة أو سؤال أو مهمة إلى فريفب لينفذها",
    "freebuff_read": "قراءة ما ردّ به فريفب وتوصيله هنا",
    "freebuff_status": "كم رسالة تنتظر فريفب في صندوق الوارد",
    "task": "تسجيل مهمة أو تذكير لجهاز حتى لا تُنسى",
    "chat": "حديث عادي أو سؤال عن جارفيس نفسه، وهو الافتراضي عند الشك",
    "unsupported": "طلب مستحيل أو خارج كل الإمكانيات، فيجب نفيه بصدق",
}


@dataclass(frozen=True)
class Capability:
    """One thing Jarvis can be asked to do in plain language."""

    name: str
    summary: str
    args: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    destructive: bool = False
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)


_RAW: tuple[Capability, ...] = (
    Capability(
        "status",
        "report whether the machines are online",
        args=("machine",),
        choices={"machine": MACHINES},
    ),
    Capability(
        "resources",
        "disk space, RAM or GPU numbers for a machine",
        args=("machine", "what"),
        choices={"machine": MACHINES, "what": RESOURCES},
    ),
    Capability(
        "wake",
        "power on a sleeping machine (magic packet)",
        args=("machine",),
        choices={"machine": MACHINES},
    ),
    Capability(
        "power",
        "shut down, reboot or sleep a machine (ALWAYS needs a confirmation token)",
        args=("machine", "action"),
        required=("action",),
        destructive=True,
        choices={"machine": MACHINES, "action": POWER_ACTIONS},
    ),
    Capability(
        "aali_status",
        "is the Aali brain answering on the PC",
    ),
    Capability(
        "aali_ask",
        "ask the Aali brain (the assistant on the PC, which owns the files, "
        "the shell and the tools) a question or a task, and get its answer back",
        args=("question",),
        required=("question",),
    ),
    Capability(
        "logs",
        "tail a log file on a machine",
        args=("machine", "file"),
        choices={"machine": MACHINES},
    ),
    Capability(
        "freebuff_send",
        "hand a message, prompt, question or task to Freebuff (the programming "
        "agent) so it works on it",
        args=("message",),
        required=("message",),
    ),
    Capability(
        "freebuff_read",
        "read what Freebuff wrote back and deliver it here",
    ),
    Capability(
        "freebuff_status",
        "how many messages are sitting in the Freebuff inbox",
    ),
    Capability(
        "task",
        "note a task or reminder for a machine so it is not forgotten",
        args=("machine", "task"),
        choices={"machine": MACHINES},
    ),
    Capability(
        "chat",
        "ordinary conversation, a question about Jarvis itself, or anything "
        "that is not an order about the house -- DEFAULT when unsure",
    ),
    Capability(
        "unsupported",
        "the owner asks for something physically impossible or outside every "
        "action above -- say so honestly instead of pretending",
        args=("why",),
    ),
)

# The table cannot ship an action with no Arabic, and the tests pin that: a
# capability nobody could describe in the owner's language is a capability the
# router will keep mis-handling.
CAPABILITIES: tuple[Capability, ...] = _RAW
BY_NAME: dict[str, Capability] = {c.name: c for c in CAPABILITIES}

ROUTER_SYSTEM = """\
You are the INTENT ROUTER for JAVIS, a Telegram remote-control bot for one
owner. You never answer the owner and you never act. Your only job is to read
his message and choose exactly one action from the list below.

Reply with ONE JSON object and nothing else:
{"action": "<action name>", "args": {"only the declared fields"}}

@CAPABILITIES@

Hard rules:
- "action" MUST be one of the names above. Never invent one.
- "args" may only contain the declared fields for that action. Never invent a
  field, and never put a shell command, a path or an argument list in a field.
- machine is only pc, mac, pi or all. Default to "pc" when the owner does not
  say, and to "all" when he clearly means every machine.
- "power" is DESTRUCTIVE. Still choose it -- Jarvis asks for a confirmation
  token itself. Never choose it for a question ("will shutting down lose my
  work?" is "chat").
- Freebuff is also spelled Buffy, Barney, Byrne, فريفب, فريف اف or بوفي.
  Anything addressed to that agent is "freebuff_send" with the payload in
  "message" -- including "ask her X", "tell her to do Y", "what did she say".
- If the message contains BOTH talk and an order, the order wins.
- If you are not at all sure it is an order, choose "chat". Chat is the safe
  default and Jarvis can always ask him to rephrase.
- Choose "unsupported" only for something no capability covers, and put a short
  reason in "why". Never choose "unsupported" for something you merely did not
  recognise as an order -- that is "chat".
- Copy the owner's payload verbatim into its field (in his own language). Do
  not translate it, summarise it, or answer it yourself.
- chat, aali_status, freebuff_read and freebuff_status take no arguments
  (unsupported may carry "why").
"""


def build_prompt() -> str:
    """The router prompt, generated from the table so the two cannot drift."""
    lines: list[str] = []
    for cap in CAPABILITIES:
        line = f'- "{cap.name}": {cap.summary}'
        if cap.args:
            parts: list[str] = []
            for arg in cap.args:
                piece = f'"{arg}"'
                allowed = cap.choices.get(arg)
                if allowed:
                    piece += " (" + "/".join(allowed) + ")"
                parts.append(piece)
            line += "  args: " + ", ".join(parts)
            required = [a for a in cap.args if a in cap.required]
            if required:
                line += "  REQUIRED: " + ", ".join(required)
        if cap.destructive:
            line += "  [DESTRUCTIVE]"
        lines.append(line + "\n  (Arabic) " + SUMMARY_AR.get(cap.name, ""))
    # replace(), not .format(): the prompt itself contains JSON braces, and
    # str.format() would read them as fields (the CSS-brace lesson).
    return ROUTER_SYSTEM.replace("@CAPABILITIES@", "\n".join(lines))


def validate(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a raw model decision into a safe one, or ``None``.

    ``None`` means "no decision happened" -- an unknown action, a missing
    required argument, or an argument value outside its allowed choices. The
    dispatcher falls back to chat on ``None``; it never improvises.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("action") or "").strip().lower()
    cap = BY_NAME.get(name)
    if cap is None:
        return None
    given = raw.get("args")
    args_in = given if isinstance(given, dict) else {}
    args: dict[str, str] = {}
    for key, value in args_in.items():
        field_name = str(key).strip().lower()
        if field_name not in cap.args:
            continue  # an argument this capability never declared
        if isinstance(value, (dict, list, tuple, set)):
            continue  # a structure is never a machine name or a question
        text = value if isinstance(value, str) else str(value)
        text = text.strip()
        if not text:
            continue
        allowed = cap.choices.get(field_name)
        if allowed is not None and text.lower() not in allowed:
            continue  # "pc2", "the router", "format the disk": not a value
        args[field_name] = text[:MAX_ARG_CHARS]
    for needed in cap.required:
        if not args.get(needed):
            return None
    return {"action": cap.name, "args": args, "destructive": cap.destructive}


def classify(
    cfg: GroqConfig,
    text: str,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Decide what one free-text message means. ``None`` on anything unusable.

    ``None`` is deliberately cheap to produce: a network failure, a rate limit,
    a sentence that is not JSON, an invented action -- all of them land here,
    and all of them mean the same thing to the caller, which is "let the chat
    model answer instead of guessing".
    """
    try:
        raw = groq.chat_json(
            cfg,
            text,
            system=build_prompt(),
            history=history,
            model=model or INTENT_MODEL,
            max_tokens=MAX_INTENT_TOKENS,
        )
    except groq.GroqError:
        return None
    except Exception:
        # A router must never be the reason the bot dies.
        return None
    return validate(raw)


def capability(name: str) -> Capability | None:
    """Look a capability up by name (used by the dispatcher and by tests)."""
    return BY_NAME.get(str(name or "").strip().lower())