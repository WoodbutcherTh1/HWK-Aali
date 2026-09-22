#!/usr/bin/env python3
"""Print-skill SFT episodes - teach Aali the print_file tool behaviorally.

2026-09-22 night, Buffy (owner request: a printer is attached to the PC and
network). Episodes follow the repo's exact tool-protocol format used by
generate_tool_sft.py and build_aali_sft_v2.py rows:

    user  -> request
    assistant -> {"tool": "print_file", "arguments": {...}}
    user  -> "Tool result: " + json(ok result)   (or the confirm gate)
    assistant -> {"tool": "final", "content": ...}   (user's language)

Behaviors taught (mirroring the CONFIRM_REQUIRED gate):
1. happy path EN/AR: confirm first in chat, then call, then report honestly.
2. guest/deny path: when the user does NOT confirm, Aali must NOT call the
   tool - final answer only.
3. failure honesty: spooler error -> say it failed, never claim paper came out.
4. unsupported format: .docx -> suggest converting first (no call).

Output: data/print_file_sft.jsonl (dedup-safe; builder merge is a later step).
"""
from __future__ import annotations

import json
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "print_file_sft.jsonl"

SYSTEM_EN = (
    "You are Aali. For tools reply {\"tool\": name, \"arguments\": {...}}. "
    "For the final answer use {\"tool\": \"final\", \"content\": reply}. "
    "Answer in the user's language and be honest."
)
SYSTEM_AR = (
    "أنت آلي. للأدوات أجب بـ {\"tool\": اسم, \"arguments\": {...}}. "
    "للنهائي استخدم {\"tool\": \"final\", \"content\": ردك}. "
    "أجب بلغة المستخدم وكن صادقاً."
)


def call(name: str, arguments: dict) -> str:
    return json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False)


def final(content: str) -> str:
    return json.dumps({"tool": "final", "content": content}, ensure_ascii=False)


def result_msg(ar: bool, payload: dict) -> str:
    prefix = "نتيجة الأداة: " if ar else "Tool result: "
    return prefix + json.dumps(payload, ensure_ascii=False)


def row(rid: str, ar: bool, turns: list[tuple[str, str]]) -> dict:
    return {
        "id": rid,
        "system": SYSTEM_AR if ar else SYSTEM_EN,
        "turns": [[role, content] for role, content in turns],
    }


EPISODES: list[dict] = []

# --- 1. happy path, EN: user asks, Aali confirms, user agrees, print, report
EPISODES.append(row("print_happy_en", False, [
    ("user", "Can you print my shopping list notes.txt on the office printer?"),
    ("assistant", final(
        "Sure - just to confirm before I use paper and ink: you want "
        "notes.txt (1 copy) on the office printer, right? Say the word and "
        "I'll print it.")),
    ("user", "Yes, go ahead."),
    ("assistant", call("print_file", {"path": "notes.txt", "copies": 1})),
    ("user", result_msg(False, {"ok": True, "tool": "print_file",
                                "file": "notes.txt", "printer": "HP LaserJet 200",
                                "copies": 1, "pages": "all"})),
    ("assistant", final(
        "Done! notes.txt (1 copy) was queued on HP LaserJet 200. If nothing "
        "comes out in a minute, tell me and I'll check the printer status.")),
]))

# --- 2. happy path, AR
EPISODES.append(row("print_happy_ar", True, [
    ("user", "اطبع لي تقرير الأمس report.pdf نسختين على الطابعة"),
    ("assistant", final(
        "بكل سرور — تأكيد سريع قبل أن أستهلك الورق والحبر: تقرير report.pdf "
        "بنسختين على الطابعة الافتراضية، صحيح؟ أخبرني وأبدأ فورًا.")),
    ("user", "نعم اطبع"),
    ("assistant", call("print_file", {"path": "report.pdf", "copies": 2})),
    ("user", result_msg(True, {"ok": True, "tool": "print_file",
                               "file": "report.pdf", "printer": "HP LaserJet 200",
                               "copies": 2, "pages": "all"})),
    ("assistant", final(
        "تم! أرسلت report.pdf بنسختين إلى الطابعة HP LaserJet 200. إن لم تخرج "
        "الأوراق خلال دقيقة أخبرني لأفحص حالة الطابعة.")),
]))

# --- 3. explicit printer name + unique substring matching, EN
EPISODES.append(row("print_named_printer_en", False, [
    ("user", "Print diagram.png on the Canon."),
    ("assistant", final(
        "Confirming: print diagram.png (1 copy) on the Canon printer?")),
    ("user", "yeah"),
    ("assistant", call("print_file", {"path": "diagram.png", "printer": "Canon"})),
    ("user", result_msg(False, {"ok": True, "tool": "print_file",
                                "file": "diagram.png", "printer": "Canon PIXMA G3420",
                                "copies": 1, "pages": "all"})),
    ("assistant", final(
        "Queued: diagram.png on Canon PIXMA G3420. Tell me if the page "
        "doesn't come out and I'll investigate.")),
]))

# --- 4. NO confirmation -> NO call (the gate is behavioral, not just runtime)
EPISODES.append(row("print_no_confirm_en", False, [
    ("user", "Print the contract file for me."),
    ("assistant", final(
        "I can do that — it will use real paper and ink, so I need a quick "
        "yes first: print contract.pdf (1 copy) on your default printer?")),
    ("user", "Hmm, actually not now. Maybe later."),
    ("assistant", final(
        "No problem — I won't print anything. Just say \"print it\" whenever "
        "you're ready and I'll queue contract.pdf for you.")),
]))
# The same lesson in one turn: no confirm, no call, honest final.
EPISODES.append(row("print_unconfirmed_single_turn_en", False, [
    ("user", "Just print the invoice without asking me anything."),
    ("assistant", final(
        "Printing is a physical action (paper + ink), so I always confirm the "
        "file first even when asked not to: shall I print invoice.pdf (1 "
        "copy) on your default printer? One yes and it's queued.")),
]))

# --- 5. AR denial
EPISODES.append(row("print_no_confirm_ar", True, [
    ("user", "اطبع ملف العقد الآن"),
    ("assistant", final(
        "أستطيع ذلك — لكن الطباعة تستهلك ورقًا وحبرًا، فأحتاج تأكيدًا سريعًا: "
        "أطبع contract.pdf نسخة واحدة على الطابعة الافتراضية؟")),
    ("user", "لا، استنى شوي"),
    ("assistant", final(
        "تمام، لن أطبع شيئًا حتى الآن. قل «اطبع» متى جهزت وسأرسل contract.pdf "
        "إلى الطابعة فورًا.")),
]))

# --- 6. failure honesty: spooler refused -> admit it, suggest a check
EPISODES.append(row("print_failure_honest_en", False, [
    ("user", "print my_cv.pdf, one copy"),
    ("assistant", final("Confirming: print my_cv.pdf (1 copy) on the default printer?")),
    ("user", "yes print it"),
    ("assistant", call("print_file", {"path": "my_cv.pdf", "copies": 1})),
    ("user", result_msg(False, {"ok": False, "error":
                                "print spooler refused (exit 1): printer not found"})),
    ("assistant", final(
        "Honest answer: the print FAILED — the spooler said \"printer not "
        "found\", so nothing was printed and no paper was used. Could you "
        "check that the printer is powered and connected (USB or Wi-Fi)? "
        "Once it shows up I'll retry my_cv.pdf immediately.")),
]))

# --- 7. failure honesty, AR
EPISODES.append(row("print_failure_honest_ar", True, [
    ("user", "اطبع الفاتورة invoice.pdf"),
    ("assistant", final("تأكيد سريع: أطبع invoice.pdf نسخة واحدة على الطابعة الافتراضية؟")),
    ("user", "اطبعها"),
    ("assistant", call("print_file", {"path": "invoice.pdf"})),
    ("user", result_msg(True, {"ok": False, "error":
                               "print spooler refused (exit 1): out of paper"})),
    ("assistant", final(
        "بصراحة: الطباعة فشلت — خادم الطباعة يقول «الورق منتهي»، لم تُطبع أي "
        "ورقة. أضف ورقًا للطابعة وأخبرني، وسأعيد المحاولة فورًا.")),
]))

# --- 8. unsupported format: honest refusal + suggestion, no call
EPISODES.append(row("print_unsupported_en", False, [
    ("user", "Print my assignment.docx please."),
    ("assistant", final(
        "I can't send .docx straight to the printer yet — I'd risk printing "
        "garbage. Two clean options: save it as PDF from Word (then I'll "
        "print it perfectly), or copy the text into a .txt file and I'll "
        "print that. Which do you prefer?")),
]))

# --- 9. unsupported format, AR
EPISODES.append(row("print_unsupported_ar", True, [
    ("user", "اطبع ملف الواجب docx"),
    ("assistant", final(
        "لا أستطيع إرسال ملفات .docx للطابعة مباشرة بعدُ — قد تخرج أوراق "
        "مشوّهة. الحلان: احفظه PDF من Word وسأطبعه بدقة، أو انسخ النص في ملف "
        "txt وسأطبعه مباشرة. أيّهما تفضّل؟")),
]))

# --- 10. copies clamp honesty (asking for 500 copies -> sensible counter)
EPISODES.append(row("print_copies_sane_en", False, [
    ("user", "Print flyer.pdf 500 times, we're going big!"),
    ("assistant", final(
        "Going big indeed! One caution: 500 copies is a LOT of paper and ink, "
        "and my print queue tops out at 50 per call. Confirm and I'll send "
        "the first 50 copies of flyer.pdf now — then just tell me \"again\" "
        "for the next batch.")),
    ("user", "Ok, 50 is fine, print"),
    ("assistant", call("print_file", {"path": "flyer.pdf", "copies": 50})),
    ("user", result_msg(False, {"ok": True, "tool": "print_file",
                                "file": "flyer.pdf", "printer": "HP LaserJet 200",
                                "copies": 50, "pages": "all"})),
    ("assistant", final(
        "Queued: 50 copies of flyer.pdf on HP LaserJet 200. Say \"again\" "
        "when you want the next batch of 50.")),
]))


def main() -> int:
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for episode in EPISODES:
            fh.write(json.dumps(episode, ensure_ascii=False) + "\n")
    print(f"wrote {len(EPISODES)} episodes -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
