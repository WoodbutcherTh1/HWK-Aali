"""Command parsing and handlers. Arabic-first replies, owner-only.

Safety model
------------
* Every destructive action (``shutdown`` / ``reboot`` / ``sleep``) needs a
  two-step confirmation: the first call mints a short token, the second must
  quote it. A forwarded message, or a fat-fingered command, cannot power off
  the owner's machine.
* The remote command itself is looked up from a fixed table in :mod:`power` —
  a Telegram message can only ever *select* an action, never supply one.
* Chat text never reaches a log; :mod:`log` whitelists the fields.

Speaking instead of typing
--------------------------
The owner wants to talk to this bot, not type at it. :mod:`intent` reads every
free-text message and returns the name of ONE capability from a closed table;
the handlers below do the work. So "send Buffy this prompt" and "/freebuff
this prompt" end up in the same place, and the router cannot invent a command
— it can only choose between handlers that already exist.
"""

from __future__ import annotations

import os
import random
import re
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import brain, groq, handoff, intent, power, tts, wol
from .config import GroqConfig, config_dir, load_aali_key
from .log import log_event, text_fingerprint
from .machines import Machine, resolve

CONFIRM_TTL = 300  # seconds a power token stays valid

# How many Freebuff answers one delivery carries. Telegram has room for more,
# but a phone screen does not, and a wall of answers is unread either way.
ANSWER_BATCH = 5

# The owner's rule: every report is Arabic TEXT, and the bot speaks ENGLISH.
# The voice line is therefore a short English rendering of the Arabic report,
# not the Arabic string read aloud — XTTS with an English voice would turn
# Arabic script into noise.
#
# The cap is a latency budget, not a style choice: XTTS on this CPU costs
# about 2s PER WORD (measured warm: 5 words 11.8s, 10 words 21.3s). 14 words
# lands near 30s, which is as long as a voice note should ever be — and the
# Arabic text has already arrived by then.
SPOKEN_MAX_WORDS = 14

# "Send it to Freebuff." The owner talks to Jarvis by voice and by text, and
# the phrase arrives misspelled, in caps, in Arabic, or with a bot mention, so
# the matcher is generous about the WORDING and strict about the INTENT: it
# only fires on an explicit send/forward verb aimed at the agent's name, or on
# the name opening the message as a label ("freebuff: ..."). A bare mention
# of the name in ordinary chat ("what can buffy do?") must still go to the
# brain, not silently become a handoff.
FREEBUFF_TTL = 300  # seconds a "tell me what to send" request stays open
_FB_NAME = r"(?:free\s?buff|freebuff|barney|byrne|buffy|فريفب|فريف ?اف|بوفي)"
# The name must be a whole word and NOT a possessive ("buffy's status" is a
# question about the agent, not a message addressed to it).
_FB_TARGET = rf"{_FB_NAME}(?!['’\w])"
_FB_VERB = (
    # Arabic verbs are \u escapes on purpose: the file transport has silently
    # dropped literal Arabic from a hand-edited block here before, and a regex
    # that quietly loses "send" is a feature the owner thinks exists.
    r"(?:send|forward|give|pass|tell|show|hand|"
    r"\u0623\u0631\u0633\u0644|\u0627\u0631\u0633\u0644|"
    r"\u0628\u0639\u062b|\u062d\u0648\u0644)\s*"
    # ...and the Arabic object suffixes, so "أرسلها ل فريفب" reads like
    # "send it to freebuff" instead of falling through to the brain.
    r"(?:it|this|that|them|me|\u0647\u0627|\u0647\u0645|\u0647|\u0644\u064a|\u064a)?\s*"
    r"(?:to\s+|over\s+to\s+|across\s+to\s+|إلى|الى|لـ|ل)?\s*"
)
FREEBUFF_SEND = re.compile(
    rf"{_FB_VERB}{_FB_TARGET}\s*[:,،\-]?\s*(?P<payload>.+?)\s*$", re.IGNORECASE | re.UNICODE
)
# "…send to freebuff" with NOTHING after it: the owner named the destination
# and has not attached the message yet. Unanchored so "please send to
# freebuff" opens the request too; it can only fire when the name ends the
# message, which is never ambiguous.
FREEBUFF_VERB_ONLY = re.compile(
    rf"{_FB_VERB}{_FB_TARGET}\s*[:,،\-]?\s*$", re.IGNORECASE | re.UNICODE
)
FREEBUFF_LABEL = re.compile(
    rf"^\W*{_FB_TARGET}\s*[:,،\-]\s*(?P<payload>.+?)\s*$", re.IGNORECASE | re.UNICODE
)

# Speaking a confirmation. The owner should be able to say "yes 123456" instead
# of retyping a slash command, but the pattern is deliberately rigid: the WHOLE
# message must be an approval (or a refusal, or nothing) plus the six digits.
# Anything longer is a sentence, and a sentence is never an approval. Arabic
# words are \u escapes because this block has already lost literal Arabic once.
_YES = (
    r"(?:yes|yeah|yep|ok|okay|confirm|approve|approved|do it|go ahead|"
    r"\u0646\u0639\u0645|\u0627\u064a\u0648\u0647|\u0627\u064a\u0648\u0627|"
    r"\u062a\u0645\u0627\u0645|\u0627\u0643\u064a\u062f|\u062a\u0623\u0643\u064a\u062f|"
    r"\u0627\u0641\u0639\u0644|\u0646\u0641\u0630)"
)
_NO = (
    r"(?:no|nope|cancel|abort|never|"
    r"\u0644\u0627|\u0627\u0644\u063a\u064a|\u0627\u0644\u063a\u0627\u0621)"
)
CONFIRM_RE = re.compile(
    rf"^\W*(?:(?P<yes>{_YES})|(?P<no>{_NO}))?\W*(?P<token>\d{{6}})\W*[.!؟?]*$",
    re.IGNORECASE | re.UNICODE,
)

# Shown when the router says "I have no capability for that". Naming what IS
# possible turns a dead end into a next step.
CAPABILITY_HINT = (
    "أستطيع: حالة الأجهزة، المساحة والذاكرة، التشغيل والإطفاء، "
    "سؤال عقل آلي، السجلات، إرسال شيء إلى فريفب، وتسجيل مهمة."
)

SPOKEN_SYSTEM = (
    "You prepare a short line for text-to-speech. Given a message, reply with "
    "at most {n} words of plain spoken English that convey the same meaning. "
    "No markdown, no emoji, no bullet points, no quotes, no preamble, no "
    "explanation — the sentence itself and nothing else."
)

HELP_AR = """🛰️ **جارفيس** — أوامرك

**الحالة**
• `/status` أو `/status all` — حالة كل الأجهزة
• `/disk` `/ram` `/gpu` — موارد الجهاز (الراسبيري)
• `/tasks` — المهام الجارية

**الطاقة** (كلها تحتاج تأكيداً)
• `/wake pc` `/wake mac` `/wake all` — تشغيل
• `/shutdown pc` — إطفاء (يطلب تأكيداً)
• `/reboot pc` — إعادة تشغيل (تطلب تأكيداً)
• `/sleep pc` — نوم (يطلب تأكيداً)

**العقل**
• `/aali status` — حالة عقل آلي
• `/ask <سؤال>` — اسأل عقل آلي على الحاسوب وأجبك بجوابه
• أو بكلماتك: «اسأل العقل: كم ملفاً في المشروع؟»

**عام**
• `/logs pc <file>` — آخر سطور من سجل
• `/update all` — فحص التحديثات
• `/freebuff <كلام>` — أرسل ما بعده إلى فريفب (مساعد البرمجة)
• أو بكلمة عادية: «أرسل إلى فريفب: …»
• `/help` — هذه القائمة

🗣️ **تكلم عادي**
لا تحتاج أوامر أصلاً — اكتب أو أرسل تسجيلاً بكلماتك وأفهم الطلب وأنفّذه:
• «أرسل لفريفب: …» · «اسأل فريفب أن يعمل كذا» · «هل وصلني رد فريفب؟»
• «كم مساحة القرص؟» · «هل الحاسوب يعمل؟» · «شغّل الحاسوب»
• «اسأل العقل: كم ملفاً في المشروع؟» — يكمل عنده الملفات والأدوات
• «أطفئ الحاسوب» — سيطلب رمز تأكيد (أو قُل: نعم 123456)

🎤 أرسل رسالة صوتية وسأفهمها، وأجيبك بصوت."""

HELP_EN = """🛰️ **Jarvis** — commands

/status [all|pc|mac] · /disk · /ram · /gpu · /tasks
/wake pc|mac|all
/shutdown|reboot|sleep <machine>  (asks for confirmation)
/aali status|restart
/logs <machine> [file] · /update all · /help
/freebuff <message>  —  hand the rest of the message to Freebuff
/ask <question>      —  ask the Aali brain on the PC and get its answer
/freebuffread        —  deliver whatever Freebuff wrote back

🗣️ Just talk to me — typed or spoken, no commands needed:
"send Buffy this prompt", "how much disk is left?", "wake the PC",
"ask the brain how many files are in the repo", "shut it down" (asks first).

🎤 Send a voice message and I will answer in kind."""


@dataclass
class Pending:
    """A destructive action waiting for its confirmation token."""

    action: str
    machine_key: str
    token: str
    expires: float


def mint_token() -> str:
    return f"{random.SystemRandom().randrange(100000, 999999)}"


class Jarvis:
    """Wires the config, the clients and the command handlers together."""

    def __init__(
        self,
        groq_cfg: GroqConfig,
        tg: Any,
        machines: dict[str, Machine],
        runner: Callable[..., Any] | None = None,
        pinger: Callable[..., bool] = power.ping_ok,
        wake_sender: Callable[..., bytes] | None = None,
        audio_dir: str | None = None,
    ) -> None:
        self.groq_cfg = groq_cfg
        self.tg = tg
        self.machines = machines
        self.runner = runner
        self.pinger = pinger
        self.wake_sender = wake_sender or wol.send_magic_packet
        self.audio_dir = audio_dir
        self.pending: dict[str, Pending] = {}
        self.history: list[dict[str, str]] = []
        self.last_task: str | None = None
        # Set when the owner says "send to freebuff" with nothing after it:
        # the NEXT message (text or voice) is the payload.
        self.freebuff_until: float = 0.0
        # One lock for the whole bot: the answer poller runs on its own thread
        # and must never interleave a Telegram send with a handler's. RLock, not
        # Lock: a voice note re-enters handle_text from cmd_voice, and a plain
        # lock would deadlock the bot on its own most-used feature.
        self.lock = threading.RLock()
        # The natural-language router. Kept as attributes (not module globals)
        # so a test -- or a future kill switch -- can turn it off per instance.
        self.intent_enabled = True
        self.intent_model = os.environ.get("AALI_JARVIS_INTENT_MODEL", intent.INTENT_MODEL)
        self.last_text: str = ""

    # ------------------------------------------------------------ freebuff
    def _freebuff_payload(self, text: str) -> tuple[bool, str | None]:
        """Is this message a handoff to Freebuff, and what is the payload?

        Returns ``(matched, payload)``. ``payload`` is ``None`` when the owner
        named the destination but attached nothing yet.
        """
        m = FREEBUFF_LABEL.match(text)
        if m:
            return True, m.group("payload")
        m = FREEBUFF_SEND.search(text)
        if m:
            return True, m.group("payload")
        if FREEBUFF_VERB_ONLY.search(text):
            return True, None
        return False, None

    def _take_freebuff_pending(self, chat_id: int, text: str) -> bool:
        """Consume the open handoff request; True means "this is the payload"."""
        if self.freebuff_until <= 0:
            return False
        if text.startswith("/"):
            # A command cancels the request rather than being swallowed by it.
            self.freebuff_until = 0.0
            return False
        if time.time() > self.freebuff_until:
            self.freebuff_until = 0.0
            return False
        self.freebuff_until = 0.0
        self.cmd_freebuff(chat_id, text, origin="pending")
        return True

    def cmd_freebuff(
        self, chat_id: int, args: str, voice: bool = False, origin: str = "telegram"
    ) -> None:
        """Hand a note to the Freebuff agent through the Pi-side inbox.

        There is no API that can inject into a live Freebuff session, so this
        writes to a mailbox Freebuff reads over SSH. That is the honest
        version of the feature and it cannot fail silently: the reply carries
        the note id, so the owner can ask "did you get it?" and get a checkable
        answer instead of a vague yes.
        """
        payload = (args or "").strip()
        if not payload:
            self.freebuff_until = time.time() + FREEBUFF_TTL
            log_event("handoff_waiting", ok=True, secs=FREEBUFF_TTL)
            self.speak(
                chat_id,
                "📥 تمام. أرسل لي الآن ما تريد إرساله إلى فريفب"
                f" (خلال {FREEBUFF_TTL // 60} دقائق).",
                voice,
            )
            return
        rec = handoff.append(payload, origin=origin)
        if rec is None:
            log_event("handoff_failed", ok=False)
            self.speak(chat_id, "❌ لم أستقبل شيئاً—aرسل النص بعد الكلمة مباشرة.", voice)
            return
        log_event(
            "handoff_sent",
            ok=True,
            chars=len(rec["text"]),
            count=int(rec.get("secrets_masked") or 0),
        )
        text = (
            "📨 أرسلت إلى **فريفب** ✅\n"
            f"🆔 `{rec['id']}` · {rec['ts']}\n"
            "اسألني لاحقاً: «هل وصل فريفب؟» لأتأكد."
        )
        if rec.get("secrets_masked"):
            n = rec["secrets_masked"]
            text += (
                f"\n⚠️ حذفت {n} نصّاً يشبه مفتاحاً قبل الإرسال — "
                "لن أرسل أسراراً إلى صندوق الوارد."
            )
        self.speak(chat_id, text, voice)

    def cmd_freebuff_status(self, chat_id: int, voice: bool = False) -> None:
        """Answer "did Freebuff get it?" from the inbox itself, not from memory."""
        every = handoff.read_all(limit=handoff.MAX_ENTRIES)
        if not every:
            self.speak(
                chat_id,
                "📭 صندوق فريفب فارغ — لم يُرسل شيء بعد.\n"
                "اكتب: «أرسل إلى فريفب: …»",
                voice,
            )
            return
        pending = handoff.read_all(limit=handoff.MAX_ENTRIES, unread_only=True)
        last = every[-1]
        log_event("handoff_checked", ok=True, count=len(pending))
        self.speak(
            chat_id,
            f"📬 صندوق فريفب: **{len(every)}** رسالة "
            f"({'لم يقرأها فريفب بعد' if pending else 'قرأها كلها'}).\n"
            f"🕐 آخر واحدة: `{last['id']}` · {last['ts']}",
            voice,
        )

    # ------------------------------------------------------------- answers
    def cmd_freebuff_read(self, chat_id: int, voice: bool = False) -> int:
        """Deliver what Freebuff wrote back, on demand."""
        return self._deliver_answers(chat_id, voice=voice, quiet_when_empty=True)

    def deliver_answers(self, voice: bool = False) -> int:
        """Poll-and-deliver for the background thread: silent when nothing."""
        return self._deliver_answers(self.tg.owner_chat_id, voice=voice, quiet_when_empty=False)

    def _deliver_answers(self, chat_id: int, voice: bool, quiet_when_empty: bool) -> int:
        """Send unseen ``answer`` records, then mark exactly those handled.

        Marking happens only AFTER a successful send: a failed Telegram call
        must leave the answer unread so the next poll tries again, because a
        Freebuff answer silently dropped is the owner's lost work.
        """
        answers = handoff.read_answers(limit=ANSWER_BATCH, unread_only=True)
        if not answers:
            if quiet_when_empty:
                self.speak(chat_id, "📭 لا يوجد رد جديد من فريفب.", voice)
            return 0
        lines: list[str] = []
        for rec in answers:
            head = f"↩️ `{rec.get('id')}`"
            if rec.get("reply_to"):
                head += f" (رد على `{rec['reply_to']}`)"
            lines.append(f"{head}\n{rec.get('text', '')}")
        self.speak(chat_id, "📬 **رد فريفب**\n\n" + "\n\n".join(lines), voice)
        handoff.mark_handled([r["id"] for r in answers if r.get("id")])
        log_event("answers_delivered", count=len(answers))
        return len(answers)

    # -------------------------------------------------------------- intent
    def classify_intent(self, text: str) -> dict[str, Any] | None:
        """Ask the router what this sentence means. ``None`` means no decision.

        ``None`` is a normal outcome, not an error: the brain is unreachable,
        the reply was not JSON, or the owner simply chatted. In every one of
        those cases the message goes to ordinary chat instead, which is always
        safe -- chat cannot act on the house.
        """
        if not self.intent_enabled:
            return None
        try:
            found = intent.classify(
                self.groq_cfg,
                text,
                history=self.history[-4:],
                model=self.intent_model,
            )
        except Exception:
            return None
        if found:
            log_event("intent", cmd=str(found.get("action") or "")[:32], ok=True)
        return found

    def run_intent(
        self,
        chat_id: int,
        found: dict[str, Any],
        text: str,
        voice: bool = False,
    ) -> None:
        """Execute a routed decision. Every branch lands in an existing handler."""
        action = str(found.get("action") or "")
        args = found.get("args") or {}
        machine = str(args.get("machine") or "").strip()
        if action == "chat":
            self.cmd_chat(chat_id, text, voice)
        elif action == "unsupported":
            why = str(args.get("why") or "").strip()[:200]
            body = "🚫 ما أقدر أنفّذ هذا الطلب." + (f"\n{why}" if why else "")
            self.speak(chat_id, f"{body}\n\n{CAPABILITY_HINT}", voice)
        elif action == "status":
            self.cmd_status(chat_id, machine or "all", voice)
        elif action == "resources":
            self.cmd_sysinfo(
                chat_id, str(args.get("what") or ""), voice, machine=machine or None
            )
        elif action == "wake":
            self.cmd_wake(chat_id, machine or "all", voice)
        elif action == "power":
            # The router SELECTED the action; it can never run it. This mints the
            # token exactly like "/shutdown pc" does, so speech is no faster
            # and no more willing than a typed command.
            self.cmd_power(chat_id, str(args.get("action") or ""), machine, voice)
        elif action == "aali_status":
            self.cmd_aali(chat_id, "status", voice)
        elif action == "aali_ask":
            self.cmd_aali_ask(chat_id, str(args.get("question") or ""), voice)
        elif action == "logs":
            tail = " ".join(
                p for p in (machine, str(args.get("file") or "")) if p
            ).strip()
            self.cmd_logs(chat_id, tail, voice)
        elif action == "freebuff_send":
            self.cmd_freebuff(chat_id, str(args.get("message") or ""), voice, origin="intent")
        elif action == "freebuff_read":
            self.cmd_freebuff_read(chat_id, voice)
        elif action == "freebuff_status":
            self.cmd_freebuff_status(chat_id, voice)
        elif action == "task":
            body = " ".join(
                p for p in (machine, str(args.get("task") or "")) if p
            ).strip()
            self.cmd_task(chat_id, body, voice)
        else:
            # Unreachable while the table and this method agree; chat is the
            # harmless landing spot if they ever drift.
            self.cmd_chat(chat_id, text, voice)

    def _take_natural_confirm(self, chat_id: int, text: str, voice: bool = False) -> bool:
        """Let a spoken/t typed confirmation close a pending power token.

        Same two-step contract as ``/shutdown pc <token>``: the owner asks, a
        token is minted, and nothing happens until that exact token comes back.
        """
        m = CONFIRM_RE.match((text or "").strip())
        if not m:
            return False
        token = m.group("token")
        pend = self.pending.get(token)
        if pend is None or pend.expires < time.time():
            self.pending.pop(token, None)
            self.speak(chat_id, "⌛ انتهت صلاحية التأكيد — اطلب من جديد.", voice)
            return True
        if m.group("no"):
            self.pending.pop(token, None)
            log_event("power_cancelled", machine=pend.machine_key, ok=True)
            self.speak(chat_id, "🛑 أُلغي الطلب. لم أفعل شيئاً.", voice)
            return True
        self._apply_pending(chat_id, pend, voice)
        return True

    # ------------------------------------------------------------- helpers
    def _machine_or_none(self, name: str | None) -> Machine | None:
        return resolve(name, self.machines)

    def _targets(self, name: str | None) -> list[Machine]:
        if (name or "").strip().lower() in ("all", "الكل"):
            return list(self.machines.values())
        m = self._machine_or_none(name)
        return [m] if m else []

    def english_line(self, text: str) -> str:
        """A short English line to speak for an Arabic ``text``.

        Already-English text is cleaned and returned without spending a model
        call. Arabic text goes through the same Groq brain the chat uses. An
        honest empty string on failure: the caller then SKIPS the voice rather
        than speaking something wrong.
        """
        raw = (text or "").strip()
        if not raw:
            return ""
        arabic = sum(1 for ch in raw if "\u0600" <= ch <= "\u06ff")
        latin = sum(1 for ch in raw if ch.isascii() and ch.isalpha())

        if arabic == 0 and latin:
            spoken = re.sub(r"[*_`#>]", " ", raw)
            spoken = re.sub(r"\s+", " ", spoken).strip()
            words = spoken.split()
            return " ".join(words[:SPOKEN_MAX_WORDS]) if words else ""

        try:
            reply = groq.chat(
                self.groq_cfg,
                raw,
                history=None,
                system=SPOKEN_SYSTEM.format(n=SPOKEN_MAX_WORDS),
            )
        except groq.GroqError as exc:
            log_event("spoken_failed", ok=False, reason=str(exc)[:40])
            return ""
        words = (reply or "").split()
        return " ".join(words[:SPOKEN_MAX_WORDS])

    def speak(
        self,
        chat_id: int,
        text: str,
        voice: bool = False,
        reply_to: int | None = None,
        voice_text: str | None = None,
    ) -> None:
        """Send the Arabic report as text, plus an ENGLISH voice note.

        The owner asked for one rule: the report reads as Arabic text, the bot
        speaks English. So the voice never reads the Arabic string aloud — it
        gets its own short English line via :meth:`english_line`.

        The text goes out FIRST and immediately. XTTS on CPU takes 16-29s, so
        waiting for the audio before answering would leave him staring at a
        silent chat; the voice note lands on its own a few seconds later.

        Voice is a bonus: any failure logs and returns, never raises.
        """
        self.tg.send_text(chat_id, text, reply_to=reply_to)
        if not voice or not tts.available():
            return
        spoken = self.english_line(text) if voice_text is None else voice_text
        # Honest degradation: if no English line could be produced (the brain
        # is down), fall back to speaking the Arabic text with the local
        # Arabic voice. A robot voice is better than silence, and the clone is
        # an English voice so it must not be handed Arabic script.
        allow_clone = bool(spoken)
        spoken = spoken or text
        if not spoken:
            return
        try:
            path = tts.synthesize(spoken, allow_clone=allow_clone)
            self.tg.send_voice(chat_id, path)
            log_event(
                "tts_sent",
                bytes=path.stat().st_size,
                source=tts.LAST_SOURCE,
                spoken_chars=len(spoken),
            )
        except Exception as exc:  # voice is a bonus, never fatal
            log_event("tts_failed", ok=False, reason=type(exc).__name__)
        finally:
            try:
                path.unlink(missing_ok=True)  # type: ignore[possibly-undefined]
            except Exception:
                pass

    # -------------------------------------------------------------- status
    def cmd_status(self, chat_id: int, args: str, voice: bool = False) -> None:
        targets = self._targets(args or "all")
        if not targets:
            self.speak(chat_id, "❓ ما اسم الجهاز؟ (`pc` أو `mac`)", voice)
            return
        lines = ["📡 **الحالة**"]
        any_up = False
        for m in targets:
            status = power.machine_status(m, self.pinger)
            if status["online"]:
                any_up = True
            lines.append("• " + power.describe_status(status))
        lines.append("🕐 " + time.strftime("%Y-%m-%d %H:%M"))
        log_event("status", count=len(targets))
        self.speak(chat_id, "\n".join(lines), voice)

    # ---------------------------------------------------------------- wake
    def cmd_wake(self, chat_id: int, args: str, voice: bool = False) -> None:
        targets = self._targets(args or "all")
        if not targets:
            self.speak(chat_id, "❓ أي جهاز؟ (`pc` أو `mac` أو `all`)", voice)
            return
        done: list[str] = []
        for m in targets:
            if not m.mac:
                continue
            try:
                self.wake_sender(m.mac)
                done.append(m.name)
                log_event("wake", machine=m.key, ok=True)
            except Exception:
                log_event("wake", machine=m.key, ok=False)
        if done:
            text = "💡 أرسلت إشعار التشغيل إلى: " + "، ".join(done)
        else:
            text = "⚠️ لا يوجد MAC مسجّل لأي جهاز."
        self.speak(chat_id, text, voice)

    # ------------------------------------------------- destructive (2-step)
    def _confirm_prompt(self, chat_id: int, action: str, m: Machine) -> None:
        token = mint_token()
        self.pending[token] = Pending(
            action=action, machine_key=m.key, token=token, expires=time.time() + CONFIRM_TTL
        )
        verb = {"shutdown": "إطفاء", "reboot": "إعادة تشغيل", "sleep": "نوم"}.get(
            action, action
        )
        self.speak(
            chat_id,
            f"⚠️ تأكيد {verb} **{m.name}**؟\n"
            f"اكتب: `/{action} {m.key} {token}` — أو فقط: `{token}`\n"
            f"ينتهي خلال {CONFIRM_TTL // 60} دقائق.",
        )

    def _apply_pending(self, chat_id: int, pend: Pending, voice: bool = False) -> None:
        """Run a confirmed destructive action. The only path that does."""
        self.pending.pop(pend.token, None)
        m = self.machines.get(pend.machine_key)
        if m is None:
            self.speak(chat_id, "❌ الجهاز غير موجود في الإعدادات.", voice)
            return
        result = power.power_action(m, pend.action, runner=self.runner)
        log_event(f"power_{pend.action}", machine=m.key, ok=result.ok)
        self.speak(
            chat_id,
            ("✅ " if result.ok else "❌ ") + f"{m.name}: {result.detail or 'تم'}",
            voice,
        )

    def cmd_power(self, chat_id: int, action: str, args: str, voice: bool = False) -> None:
        parts = (args or "").split()
        if not parts:
            self.speak(chat_id, f"❓Usage: `/{action} <pc|mac>`", voice)
            return

        # Step 2: a token was supplied.
        if len(parts) >= 2 and parts[1].isdigit():
            token = parts[1]
            pend = self.pending.pop(token, None)
            if pend is None:
                self.speak(chat_id, "⌛ انتهت صلاحية التأكيد، أعد الطلب.", voice)
                return
            if pend.expires < time.time():
                self.speak(chat_id, "⌛ انتهت صلاحية التأكيد، أعد الطلب.", voice)
                return
            if pend.machine_key != parts[0].strip().lower():
                self.speak(chat_id, "❌ الرمز لا يطابق هذا الجهاز.", voice)
                return
            self._apply_pending(chat_id, pend, voice)
            return

        # Step 1: mint a token.
        m = self._machine_or_none(parts[0])
        if m is None:
            self.speak(chat_id, "❓ ما اسم الجهاز؟ (`pc` أو `mac`)", voice)
            return
        if m.os not in power.POWER_COMMANDS:
            self.speak(chat_id, f"❌ لا أملك أمراً لـ `{m.os}`.", voice)
            return
        self._confirm_prompt(chat_id, action, m)

    # -------------------------------------------------------------- aali
    def cmd_aali_ask(self, chat_id: int, question: str, voice: bool = False) -> None:
        """Ask the Aali brain on the PC a question, and relay the answer.

        This is the honest answer to "do this from wherever I am": the brain on
        the PC already owns the files, the shell and the tools, so an order
        routed here becomes a real task rather than a simulation of one.
        """
        question = (question or "").strip()
        if not question:
            self.speak(chat_id, "❓ ما السؤال؟ جرّب: «اسأل العقل: كم ملفاً في المشروع؟»", voice)
            return
        target = self._machine_or_none("pc")  # the brain lives on the PC
        if target is None:
            self.speak(chat_id, "❌ لا يوجد PC في الإعدادات.", voice)
            return
        key = load_aali_key()
        self.tg.send_typing(chat_id)
        ok, detail = brain.ask(key, target, question)
        log_event("brain_ask", ok=ok, chars=len(detail))
        if ok:
            # Telegram rejects a message over 4096 characters outright, so the
            # cap belongs here as well as in brain.ask: this is the last point
            # before the send, and a lost answer is worse than a short one.
            shown = (detail or "")[: brain.MAX_REPLY]
            if len(detail or "") > len(shown):
                shown += "\n\n…(طول الجواب تجاوز حد تيليجرام)"
            self.speak(chat_id, f"🧠 **العقل على الحاسوب**:\n\n{shown}", voice)
            return
        hint = ""
        if not key:
            hint = (
                "\n\n💡 يلزم مفتاح العقل ليعمل عن بُعد. ضعه في "
                f"`{config_dir() / 'aali_key.txt'}`"
            )
        self.speak(chat_id, f"❌ لم يرد العقل على الحاسوب ({detail}).{hint}", voice)

    def cmd_aali(self, chat_id: int, args: str, voice: bool = False) -> None:
        parts = (args or "").split()
        sub = parts[0].lower() if parts else "status"
        target = self._machine_or_none("pc")  # the brain lives on the PC
        if target is None:
            self.speak(chat_id, "❌ لا يوجد PC في الإعدادات.", voice)
            return
        if sub == "status":
            alive = power.tcp_open(target.ip, target.api_port or 5055)
            self.speak(
                chat_id,
                ("🟢 عقل آلي يعمل على " if alive else "🔴 عقل آلي لا يستجيب على ")
                + f"{target.name} (:{target.api_port or 5055})",
                voice,
            )
            log_event("aali_status", ok=alive)
            return
        self.speak(chat_id, "❌ أمر غير معروف. جرّب `/aali status`.", voice)

    # --------------------------------------------------------------- misc
    def cmd_logs(self, chat_id: int, args: str, voice: bool = False) -> None:
        parts = (args or "").split()
        m = self._machine_or_none(parts[0] if parts else "pc")
        if m is None:
            self.speak(chat_id, "❓ أي جهاز؟ (`pc` أو `mac`)", voice)
            return
        filename = parts[1] if len(parts) > 1 else "aali_server.log"
        if "/" in filename or filename.startswith("."):
            self.speak(chat_id, "❌ اسم ملف غير صالح.", voice)
            return
        result = power.ssh_exec(
            m, f"tail -n 20 ~/{shlex.quote(filename)}", runner=self.runner
        )
        log_event("logs", machine=m.key, ok=result.ok)
        if not result.ok:
            self.speak(chat_id, f"❌ {m.name}: {result.detail[:120]}", voice)
            return
        self.speak(chat_id, f"📄 آخر سطور `{filename}`:\n```\n{result.detail[:1200]}\n```")

    def cmd_sysinfo(
        self, chat_id: int, what: str, voice: bool = False, machine: str | None = None
    ) -> None:
        runner = self.runner
        if runner is None:
            self.speak(chat_id, "❌ المعلومة غير متاحة بدون SSH.", voice)
            return
        table = {
            "disk": "df -h / | tail -1",
            "ram": "free -h | head -2",
            "gpu": "nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv,noheader",
        }
        key = (what or "").strip().lower()
        if key not in table:
            self.speak(
                chat_id, "❓ أي مورد؟ (`disk` أو `ram` أو `gpu`)", voice
            )
            return
        m = self._machine_or_none(machine or "pc")
        if m is None:
            self.speak(chat_id, "❓ ما اسم الجهاز؟ (`pc` أو `mac`)", voice)
            return
        result = power.ssh_exec(m, table[what], runner=runner)
        log_event(f"sys_{what}", machine=m.key, ok=result.ok)
        self.speak(chat_id, f"📊 {what}:\n```\n{result.detail[:600]}\n```" if result.ok else "❌ فشل")

    def cmd_task(self, chat_id: int, args: str, voice: bool = False) -> None:
        """Record a task so it is not forgotten -- into the same inbox.

        This used to set a variable and admit that no bridge existed. The inbox
        IS that bridge now, so a task becomes a real queued note Freebuff can
        see. The machine is kept in a FIELD rather than glued onto the text, so
        the payload stays verbatim.
        """
        parts = (args or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.speak(chat_id, "❓ Usage: `/task pc <what to do>`", voice)
            return
        m = self._machine_or_none(parts[0])
        body = parts[1] if m is not None else (args or "").strip()
        rec = handoff.append(body, origin="task", machine=m.key if m else None)
        if rec is None:
            self.speak(chat_id, "❌ لم أستقبل مهمة فارغة.", voice)
            return
        self.last_task = args
        log_event("task_queued", machine=m.key if m else "any", chars=len(body))
        where = f" لـ **{m.name}**" if m else ""
        self.speak(
            chat_id,
            f"📌 سجّلت المهمة{where} في صندوق فريفب ✅\n🆔 `{rec['id']}`",
            voice,
        )

    def cmd_chat(self, chat_id: int, text: str, voice: bool = False) -> None:
        """Free-form chat, answered by Groq."""
        self.tg.send_typing(chat_id)
        try:
            reply = groq.chat(self.groq_cfg, text, history=self.history)
        except groq.GroqError as exc:
            log_event("groq_error", ok=False, reason=str(exc)[:40])
            self.speak(chat_id, "⚠️ تعذّر الوصول إلى العقل الآن.", voice)
            return
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-8:]
        log_event("chat", chars=len(reply), model=self.groq_cfg.chat_model)
        self.speak(chat_id, reply or "…", voice)

    def cmd_voice(
        self, chat_id: int, file_id: str, duration: int = 0, voice_reply: bool = True
    ) -> None:
        """Transcribe a Telegram voice note, then answer out loud."""
        self.tg.send_typing(chat_id)
        try:
            audio = self.tg.download_file(file_id)
        except Exception as exc:
            log_event("voice_download_failed", ok=False, reason=type(exc).__name__)
            self.speak(chat_id, "⚠️ لم أستقبل الصوت.", voice_reply)
            return
        log_event("voice_in", bytes=len(audio), secs=duration)
        try:
            text = groq.transcribe(self.groq_cfg, audio, filename="voice.ogg")
        except groq.GroqError as exc:
            log_event("stt_error", ok=False, reason=str(exc)[:40])
            self.speak(chat_id, "⚠️ لم أفهم التسجيل، حاول مرة أخرى.", voice_reply)
            return
        log_event("stt_ok", secs=duration)
        self.speak(chat_id, f"🎧 «{text}»", voice=False)
        # Through the dispatcher, not straight to chat: the owner may have said
        # "send to Freebuff" out loud, and a voice note must reach the same
        # handler a typed message would.
        self.handle_text(chat_id, text, voice=voice_reply)

    # ---------------------------------------------------------- dispatcher
    def handle_text(self, chat_id: int, text: str, voice: bool = False) -> None:
        with self.lock:
            self._handle_text(chat_id, text, voice)

    def _handle_text(self, chat_id: int, text: str, voice: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.last_text = text
        if text.startswith("/"):
            parts = text[1:].split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]
            args = parts[1] if len(parts) > 1 else ""
            # Any command closes an open "send to Freebuff" request, so a
            # later question is never mistaken for the payload.
            self.freebuff_until = 0.0
            if cmd == "start":
                self.speak(chat_id, "🛰️ جارفيس مستيقظ. أرسل `/help`.", voice)
            elif cmd == "help":
                self.speak(chat_id, HELP_AR, voice)
            elif cmd == "freebuff":
                self.cmd_freebuff(chat_id, args, voice)
            elif cmd in ("freebuffs", "freebuffstatus", "fbstatus"):
                self.cmd_freebuff_status(chat_id, voice)
            elif cmd in ("freebuffread", "answers", "fbread", "fbreplies"):
                self.cmd_freebuff_read(chat_id, voice)
            elif cmd in ("ask", "brain", "aali_ask"):
                self.cmd_aali_ask(chat_id, args, voice)
            elif cmd == "status":
                self.cmd_status(chat_id, args, voice)
            elif cmd == "wake":
                self.cmd_wake(chat_id, args, voice)
            elif cmd in ("shutdown", "reboot", "sleep"):
                self.cmd_power(chat_id, cmd, args, voice)
            elif cmd == "aali":
                self.cmd_aali(chat_id, args, voice)
            elif cmd == "logs":
                self.cmd_logs(chat_id, args, voice)
            elif cmd in ("disk", "ram", "gpu"):
                self.cmd_sysinfo(chat_id, cmd, voice)
            elif cmd == "task":
                self.cmd_task(chat_id, args, voice)
            elif cmd == "kill":
                self.last_task = None
                self.speak(chat_id, "🛑 أُلغيت المهمة المسجّلة.", voice)
            else:
                self.cmd_chat(chat_id, text, voice)
            return
        # An open "send to Freebuff" request swallows the next message.
        if self._take_freebuff_pending(chat_id, text):
            return
        matched, payload = self._freebuff_payload(text)
        if matched:
            self.cmd_freebuff(chat_id, payload or "", voice)
            return
        # A spoken confirmation closes a pending power token BEFORE the router
        # runs: "yes 123456" is an answer to a question, and must never be
        # re-read as a fresh request.
        if self._take_natural_confirm(chat_id, text, voice):
            return
        # Everything else is natural language. The router may pick a capability;
        # if it declines, the sentence is just a message to answer.
        found = self.classify_intent(text)
        if found:
            self.run_intent(chat_id, found, text, voice)
            return
        self.cmd_chat(chat_id, text, voice)

    def handle_update(self, update: dict, voice: bool = False) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        if not self.tg.is_owner(chat_id):
            log_event("unauthorized", ok=False)
            return
        text = (message.get("text") or "").strip()
        if text:
            log_event("inbound_text", chars=len(text))
            self.handle_text(chat_id, text, voice=voice)
            return
        voice_msg = message.get("voice") or message.get("audio")
        if voice_msg and voice_msg.get("file_id"):
            log_event("inbound_voice")
            self.cmd_voice(
                chat_id, voice_msg["file_id"], int(voice_msg.get("duration") or 0), voice_reply=voice
            )