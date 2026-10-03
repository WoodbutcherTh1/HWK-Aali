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
"""

from __future__ import annotations

import random
import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import groq, power, tts, wol
from .config import GroqConfig
from .log import log_event, text_fingerprint
from .machines import Machine, resolve

CONFIRM_TTL = 300  # seconds a power token stays valid

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
• `/aali restart` — إعادة تشغيل العقل

**عام**
• `/logs pc <file>` — آخر سطور من سجل
• `/update all` — فحص التحديثات
• `/help` — هذه القائمة

🎤 أرسل رسالة صوتية وسأفهمها، وأجيبك بصوت."""

HELP_EN = """🛰️ **Jarvis** — commands

/status [all|pc|mac] · /disk · /ram · /gpu · /tasks
/wake pc|mac|all
/shutdown|reboot|sleep <machine>  (asks for confirmation)
/aali status|restart
/logs <machine> [file] · /update all · /help

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

    # ------------------------------------------------------------- helpers
    def _machine_or_none(self, name: str | None) -> Machine | None:
        return resolve(name, self.machines)

    def _targets(self, name: str | None) -> list[Machine]:
        if (name or "").strip().lower() in ("all", "الكل"):
            return list(self.machines.values())
        m = self._machine_or_none(name)
        return [m] if m else []

    def speak(
        self,
        chat_id: int,
        text: str,
        voice: bool = False,
        reply_to: int | None = None,
    ) -> None:
        """Send text, and optionally a voice note with the same words."""
        self.tg.send_text(chat_id, text, reply_to=reply_to)
        if voice and tts.available():
            try:
                path = tts.synthesize(text)
                self.tg.send_voice(chat_id, path)
                log_event("tts_sent", bytes=path.stat().st_size)
            except (tts.TTSError, Exception) as exc:  # voice is a bonus, never fatal
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
            f"⚠️ تأكيد {verb} **{m.name}**؟\nأرسل: `/{action} {m.key} {token}`\n"
            f"ينتهي خلال {CONFIRM_TTL // 60} دقائق.",
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
            m = self.machines.get(pend.machine_key)
            if m is None:
                self.speak(chat_id, "❌ الجهاز غير موجود في الإعدادات.", voice)
                return
            result = power.power_action(m, pend.action, runner=self.runner)
            ok = result.ok
            log_event(f"power_{pend.action}", machine=m.key, ok=ok)
            self.speak(
                chat_id,
                ("✅ " if ok else "❌ ") + f"{m.name}: {result.detail or 'تم'}",
                voice,
            )
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

    def cmd_sysinfo(self, chat_id: int, what: str, voice: bool = False) -> None:
        runner = self.runner
        if runner is None:
            self.speak(chat_id, "❌ المعلومة غير متاحة بدون SSH.", voice)
            return
        table = {
            "disk": "df -h / | tail -1",
            "ram": "free -h | head -2",
            "gpu": "nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv,noheader",
        }
        m = self._machine_or_none("pc")
        if m is None:
            self.speak(chat_id, "❌ لا يوجد PC في الإعدادات.", voice)
            return
        result = power.ssh_exec(m, table[what], runner=runner)
        log_event(f"sys_{what}", machine=m.key, ok=result.ok)
        self.speak(chat_id, f"📊 {what}:\n```\n{result.detail[:600]}\n```" if result.ok else "❌ فشل")

    def cmd_task(self, chat_id: int, args: str, voice: bool = False) -> None:
        """Record a task to send to a machine. The send itself needs a bridge."""
        parts = (args or "").split(maxsplit=1)
        m = self._machine_or_none(parts[0]) if parts else None
        if m is None or len(parts) < 2:
            self.speak(chat_id, "❓ Usage: `/task pc <what to do>`", voice)
            return
        self.last_task = args
        log_event("task_queued", machine=m.key, chars=len(parts[1]))
        self.speak(
            chat_id,
            f"📌 سُجّلت المهمة لـ **{m.name}**.\n"
            "⚠️ الإرسال الآلي معطّل — لم يُبنَ الجسر بعد.",
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
        self.cmd_chat(chat_id, text, voice=voice_reply)

    # ---------------------------------------------------------- dispatcher
    def handle_text(self, chat_id: int, text: str, voice: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            return
        if text.startswith("/"):
            parts = text[1:].split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]
            args = parts[1] if len(parts) > 1 else ""
            if cmd == "start":
                self.speak(chat_id, "🛰️ جارفيس مستيقظ. أرسل `/help`.", voice)
            elif cmd == "help":
                self.speak(chat_id, HELP_AR, voice)
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