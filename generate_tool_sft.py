"""Generate tool-calling / agentic SFT examples for Aali.

Produces data/tool_calling_sft.jsonl in the same {"messages": [...]} chat
format already used by data/agent_instructions.jsonl, but teaching the exact
JSON tool-call protocol agent_loop.py expects from the local scratch model:

    assistant -> {"tool": "<name>", "arguments": {...}}
    user      -> "نتيجة الأداة: " + json(tool_result)
    assistant -> {"tool": "final", "content": "<answer in user's language>"}

Every tool name / argument here is taken directly from
file-agent/file_agent/file_tools.py's _DEFINITIONS so the synthetic data
matches the real tool schema exactly. Also includes plain examples with no
tool call at all (final answer only), so the model learns *when* to call a
tool as well as how.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(1337)

OUT_PATH = Path(__file__).resolve().parent / "data" / "tool_calling_sft.jsonl"

SYSTEM_AR = (
    "أنت آلي، مساعد ذكاء اصطناعي محلي. لديك أدوات لقراءة/كتابة الملفات، تشغيل "
    "أوامر مسموحة، البحث في الويب، قراءة الصور والمستندات والفيديو، والمهارات "
    "المحفوظة. عند الحاجة لأداة أجب حصراً بكائن JSON واحد بالشكل "
    "{\"tool\": \"اسم_الأداة\", \"arguments\": {...}}. بعد تنفيذ الأداة ستصلك "
    "نتيجتها كرسالة تبدأ بـ 'نتيجة الأداة:'. للإجابة النهائية بعد إتمام العمل "
    "أو إن لم تحتج أداة أصلاً استخدم {\"tool\": \"final\", \"content\": \"ردك\"}. "
    "أجب دائماً بنفس لغة المستخدم، وكن صادقاً إن لم تعرف الإجابة."
)

SYSTEM_EN = (
    "You are Aali, a local AI assistant. You have tools to read/write files, "
    "run allow-listed commands, search the web, read images/documents/video, "
    "and load saved skills. When a tool is needed, reply with exactly one "
    "JSON object: {\"tool\": \"tool_name\", \"arguments\": {...}}. After it "
    "runs you'll get its result as a message starting with 'Tool result:'. "
    "For your final answer once done, or when no tool is needed at all, use "
    "{\"tool\": \"final\", \"content\": \"your reply\"}. Always answer in the "
    "user's language, and be honest when you don't know something."
)


def tool_call(name: str, arguments: dict) -> str:
    return json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False)


def final(content: str) -> str:
    return json.dumps({"tool": "final", "content": content}, ensure_ascii=False)


def tool_result_msg(ar: bool, result: dict) -> str:
    prefix = "نتيجة الأداة: " if ar else "Tool result: "
    return prefix + json.dumps(result, ensure_ascii=False)


def episode(system: str, user: str, turns: list[tuple[str, str]]) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for role, content in turns:
        messages.append({"role": role, "content": content})
    return {"messages": messages}


FILE_NAMES_AR = ["ملاحظات.txt", "تقرير.txt", "مهام.txt", "خطة_العمل.txt", "مسودة.txt"]
FILE_NAMES_EN = ["notes.txt", "report.txt", "todo.txt", "draft.md", "summary.txt"]
DIR_NAMES = ["project_x", "backup", "archive_2026", "reports", "assets"]


def gen_write_file_examples() -> list[dict]:
    out = []
    contents_ar = ["اجتماع الساعة 5 مساءً", "لا تنسَ شراء القهوة", "المشروع جاهز للمراجعة"]
    contents_en = ["Meeting at 5pm", "Don't forget to buy coffee", "Project ready for review"]
    for fname, content in zip(FILE_NAMES_AR, contents_ar):
        user = f"أنشئ ملف {fname} واكتب فيه: {content}"
        call = tool_call("write_file", {"path": fname, "content": content})
        result = tool_result_msg(True, {"ok": True, "result": {"path": fname, "created": True}})
        ans = final(f"تم إنشاء الملف {fname} وكتابة المحتوى بنجاح ✅")
        out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    for fname, content in zip(FILE_NAMES_EN, contents_en):
        user = f"Create a file called {fname} with this text: {content}"
        call = tool_call("write_file", {"path": fname, "content": content})
        result = tool_result_msg(False, {"ok": True, "result": {"path": fname, "created": True}})
        ans = final(f"Done — created {fname} with your text ✅")
        out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_read_file_examples() -> list[dict]:
    out = []
    samples = [
        ("اقرأ لي ملف تقرير.txt", "تقرير.txt", "الأرباح ارتفعت 10% هذا الربع.", True),
        ("What does todo.txt say?", "todo.txt", "Buy milk, call John, finish report.", False),
        ("افتح ملف الإعدادات وقلي شو فيه", "config.txt", "debug=false\nport=8080", True),
    ]
    for user, path, content, ar in samples:
        call = tool_call("read_file", {"path": path})
        result = tool_result_msg(ar, {"ok": True, "result": {"content": content}})
        if ar:
            ans = final(f"محتوى الملف: {content}")
        else:
            ans = final(f"Here's what's in it: {content}")
        sysmsg = SYSTEM_AR if ar else SYSTEM_EN
        out.append(episode(sysmsg, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_list_files_examples() -> list[dict]:
    out = []
    for d in DIR_NAMES[:3]:
        user_ar = f"شو في مجلد {d}؟"
        call = tool_call("list_files", {"path": d, "recursive": False})
        files = ["a.txt", "b.py", "notes.md"]
        result = tool_result_msg(True, {"ok": True, "result": {"entries": files}})
        ans = final("الملفات الموجودة: " + "، ".join(files))
        out.append(episode(SYSTEM_AR, user_ar, [("assistant", call), ("user", result), ("assistant", ans)]))
    user_en = f"List what's inside the {DIR_NAMES[3]} folder"
    call = tool_call("list_files", {"path": DIR_NAMES[3], "recursive": False})
    files = ["logo.png", "readme.md"]
    result = tool_result_msg(False, {"ok": True, "result": {"entries": files}})
    ans = final("It contains: " + ", ".join(files))
    out.append(episode(SYSTEM_EN, user_en, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_append_file_examples() -> list[dict]:
    out = []
    user = "ضيف على ملف مهام.txt هاد السطر: اتصل بالمحاسب"
    call = tool_call("append_file", {"path": "مهام.txt", "content": "اتصل بالمحاسب", "create": True})
    result = tool_result_msg(True, {"ok": True, "result": {"appended": True}})
    ans = final("تمت إضافة السطر لملف المهام ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Append 'follow up with client' to todo.txt, create it if missing"
    call = tool_call("append_file", {"path": "todo.txt", "content": "follow up with client", "create": True})
    result = tool_result_msg(False, {"ok": True, "result": {"appended": True}})
    ans = final("Added that line to todo.txt ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_replace_in_file_examples() -> list[dict]:
    out = []
    user = "بدّل كلمة 'قديم' بكلمة 'جديد' بملف مسودة.txt"
    call = tool_call("replace_in_file", {"path": "مسودة.txt", "old_text": "قديم", "new_text": "جديد"})
    result = tool_result_msg(True, {"ok": True, "result": {"replacements": 1}})
    ans = final("تم استبدال الكلمة بنجاح ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "In config.txt replace 'debug=false' with 'debug=true'"
    call = tool_call("replace_in_file", {"path": "config.txt", "old_text": "debug=false", "new_text": "debug=true"})
    result = tool_result_msg(False, {"ok": True, "result": {"replacements": 1}})
    ans = final("Replaced it — debug is now enabled ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_make_directory_examples() -> list[dict]:
    out = []
    user = "سوي مجلد جديد اسمه backup"
    call = tool_call("make_directory", {"path": "backup", "exist_ok": True})
    result = tool_result_msg(True, {"ok": True, "result": {"created": True}})
    ans = final("تم إنشاء مجلد backup ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Create a folder named archive_2026"
    call = tool_call("make_directory", {"path": "archive_2026", "exist_ok": True})
    result = tool_result_msg(False, {"ok": True, "result": {"created": True}})
    ans = final("Created the archive_2026 folder ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_move_file_examples() -> list[dict]:
    out = []
    user = "انقل ملف تقرير.txt لمجلد reports"
    call = tool_call("move_file", {"source": "تقرير.txt", "destination": "reports/تقرير.txt"})
    result = tool_result_msg(True, {"ok": True, "result": {"moved": True}})
    ans = final("تم نقل الملف لمجلد reports ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Move draft.md into the archive_2026 folder"
    call = tool_call("move_file", {"source": "draft.md", "destination": "archive_2026/draft.md"})
    result = tool_result_msg(False, {"ok": True, "result": {"moved": True}})
    ans = final("Moved draft.md into archive_2026 ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_delete_file_examples() -> list[dict]:
    out = []
    user = "احذف ملف مسودة.txt"
    call = tool_call("delete_file", {"path": "مسودة.txt", "missing_ok": True})
    result = tool_result_msg(True, {"ok": True, "result": {"deleted": True}})
    ans = final("تم حذف الملف ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Delete the old backup folder, it has files inside"
    call = tool_call("delete_file", {"path": "backup", "recursive": True})
    result = tool_result_msg(False, {"ok": True, "result": {"deleted": True}})
    ans = final("Deleted the backup folder and everything in it ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_search_files_examples() -> list[dict]:
    out = []
    user = "دور على كلمة 'خطأ' بكل ملفات المشروع"
    call = tool_call("search_files", {"pattern": "خطأ", "path": ".", "ignore_case": True})
    matches = [{"file": "log.txt", "line": 12}, {"file": "notes.txt", "line": 4}]
    result = tool_result_msg(True, {"ok": True, "result": {"matches": matches}})
    ans = final("لقيت الكلمة بملفين: log.txt سطر 12، وnotes.txt سطر 4.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Search the project for the word TODO"
    call = tool_call("search_files", {"pattern": "TODO", "path": ".", "ignore_case": True})
    matches = [{"file": "main.py", "line": 5}]
    result = tool_result_msg(False, {"ok": True, "result": {"matches": matches}})
    ans = final("Found one match: main.py, line 5.")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_run_command_examples() -> list[dict]:
    out = []
    user = "شغل اختبارات المشروع (pytest)"
    call = tool_call("run_command", {"command": "pytest", "timeout_seconds": 120})
    result = tool_result_msg(True, {"ok": True, "result": {"exit_code": 0, "output": "5 passed"}})
    ans = final("شغّلت الاختبارات: 5 نجحت وما في أي فشل ✅")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Run 'pip install requests' for me"
    call = tool_call("run_command", {"command": "pip install requests"})
    result = tool_result_msg(False, {"ok": True, "result": {"exit_code": 0, "output": "Successfully installed requests"}})
    ans = final("Installed requests successfully ✅")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_read_image_examples() -> list[dict]:
    out = []
    user = "شو مكتوب بهاد الصورة screenshot.png؟"
    call = tool_call("read_image", {"path": "screenshot.png"})
    result = tool_result_msg(True, {"ok": True, "result": {"text": "الاجتماع الساعة 3 عصراً"}})
    ans = final("النص بالصورة: الاجتماع الساعة 3 عصراً")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Extract the text from receipt.jpg"
    call = tool_call("read_image", {"path": "receipt.jpg"})
    result = tool_result_msg(False, {"ok": True, "result": {"text": "Total: $42.10"}})
    ans = final("The receipt shows: Total: $42.10")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_read_document_examples() -> list[dict]:
    out = []
    user = "لخص لي ملف العقد.pdf"
    call = tool_call("read_document", {"path": "العقد.pdf"})
    result = tool_result_msg(True, {"ok": True, "result": {"content": "مدة العقد سنة واحدة قابلة للتجديد."}})
    ans = final("العقد مدته سنة واحدة قابلة للتجديد.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Summarize budget.xlsx for me"
    call = tool_call("read_document", {"path": "budget.xlsx"})
    result = tool_result_msg(False, {"ok": True, "result": {"content": "Total spend: $12,400 across 4 categories."}})
    ans = final("Total spend is $12,400 across 4 categories.")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_analyze_video_examples() -> list[dict]:
    out = []
    user = "شو مضمون فيديو meeting.mp4؟"
    call = tool_call("analyze_video", {"path": "meeting.mp4", "transcript": True})
    result = tool_result_msg(True, {"ok": True, "result": {"transcript": "ناقشنا الميزانية والخطة القادمة."}})
    ans = final("الفيديو بيحكي عن مناقشة الميزانية والخطة القادمة.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "What's said in tutorial.mp4?"
    call = tool_call("analyze_video", {"path": "tutorial.mp4", "transcript": True})
    result = tool_result_msg(False, {"ok": True, "result": {"transcript": "This tutorial covers setting up a Python environment."}})
    ans = final("It covers setting up a Python environment.")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_skills_examples() -> list[dict]:
    out = []
    user = "شو المهارات المتوفرة عندك؟"
    call = tool_call("list_skills", {})
    result = tool_result_msg(True, {"ok": True, "result": {"skills": [
        {"name": "pdf_report", "description": "توليد تقرير PDF منسق"},
        {"name": "code_review", "description": "مراجعة كود ولقاء الأخطاء"},
    ]}})
    ans = final("عندي مهارتين محفوظتين: pdf_report لتوليد تقارير PDF، وcode_review لمراجعة الكود.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "I need to generate a PDF report, do you have a playbook for that?"
    call1 = tool_call("list_skills", {})
    result1 = tool_result_msg(False, {"ok": True, "result": {"skills": [
        {"name": "pdf_report", "description": "Generate a formatted PDF report"},
    ]}})
    call2 = tool_call("use_skill", {"name": "pdf_report"})
    result2 = tool_result_msg(False, {"ok": True, "result": {"instructions": "1. Collect sections 2. Render with template 3. Export pdf"}})
    ans = final("Found the pdf_report skill and loaded its steps — I'll collect the sections, render them with the template, then export the PDF.")
    out.append(episode(SYSTEM_EN, user, [
        ("assistant", call1), ("user", result1),
        ("assistant", call2), ("user", result2),
        ("assistant", ans),
    ]))
    return out


def gen_web_examples() -> list[dict]:
    out = []
    user = "دور لي بالنت عن آخر أخبار الذكاء الاصطناعي"
    call1 = tool_call("web_search", {"query": "آخر أخبار الذكاء الاصطناعي 2026"})
    result1 = tool_result_msg(True, {"ok": True, "result": {"results": [
        {"title": "تطورات جديدة بالذكاء الاصطناعي", "url": "https://example.com/ai-news"},
    ]}})
    call2 = tool_call("fetch_url", {"url": "https://example.com/ai-news"})
    result2 = tool_result_msg(True, {"ok": True, "result": {"text": "شركات كبرى أعلنت عن نماذج جديدة هذا الأسبوع."}})
    ans = final("آخر الأخبار: شركات كبرى أعلنت عن نماذج ذكاء اصطناعي جديدة هذا الأسبوع.")
    out.append(episode(SYSTEM_AR, user, [
        ("assistant", call1), ("user", result1),
        ("assistant", call2), ("user", result2),
        ("assistant", ans),
    ]))
    user = "Look up the current weather API docs for OpenWeather"
    call1 = tool_call("web_search", {"query": "OpenWeather API documentation"})
    result1 = tool_result_msg(False, {"ok": True, "result": {"results": [
        {"title": "OpenWeather API Docs", "url": "https://openweathermap.org/api"},
    ]}})
    call2 = tool_call("fetch_url", {"url": "https://openweathermap.org/api"})
    result2 = tool_result_msg(False, {"ok": True, "result": {"text": "The API provides current weather, forecasts, and historical data via REST endpoints."}})
    ans = final("The OpenWeather API offers current weather, forecasts, and historical data through REST endpoints — official docs are at openweathermap.org/api.")
    out.append(episode(SYSTEM_EN, user, [
        ("assistant", call1), ("user", result1),
        ("assistant", call2), ("user", result2),
        ("assistant", ans),
    ]))
    return out


def gen_n8n_examples() -> list[dict]:
    out = []
    user = "سوي لي أتمتة n8n ترسل تليجرام كل يوم الساعة 9 صباحاً"
    call = tool_call("make_n8n_workflow", {"description": "إرسال رسالة تليجرام يومياً الساعة 9 صباحاً", "filename": "daily_telegram"})
    result = tool_result_msg(True, {"ok": True, "result": {"path": "daily_telegram.json"}})
    ans = final("جهزت workflow باسم daily_telegram.json — استوردها بـ n8n وفعّلها.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def gen_no_tool_examples() -> list[dict]:
    out = []
    pairs = [
        (SYSTEM_AR, "شو رأيك بالطقس اليوم؟", "ما إلي عيون أشوف فيها الطقس، بس إذا حابب دور لك بالنت عن توقعات اليوم."),
        (SYSTEM_AR, "كيف حالك؟", "تمام الحمدلله، جاهز أساعدك بأي شي."),
        (SYSTEM_AR, "اشرح لي شو يعني API بسرعة", "API هو واجهة بتخلي برنامجين يحكوا مع بعض بطريقة منظمة."),
        (SYSTEM_EN, "How are you doing today?", "Doing well, thanks — ready to help with whatever you need."),
        (SYSTEM_EN, "What's 12 times 8?", "12 times 8 is 96."),
        (SYSTEM_EN, "Explain what a REST API is in one line", "A REST API lets two programs talk over HTTP using standard verbs like GET and POST."),
    ]
    for sysmsg, user, ans in pairs:
        out.append(episode(sysmsg, user, [("assistant", final(ans))]))
    return out


def gen_ambiguous_clarify_examples() -> list[dict]:
    out = []
    pairs = [
        (SYSTEM_AR, "احذف الملف", "أي ملف تقصد بالضبط؟ عطيني الاسم أو المسار الكامل قبل ما أحذف شي."),
        (SYSTEM_EN, "Move the file", "Which file, and where should I move it to? I need the exact source and destination path."),
        (SYSTEM_AR, "شغل الأمر", "شو الأمر يلي بدك ياه أشغله بالضبط؟"),
    ]
    for sysmsg, user, ans in pairs:
        out.append(episode(sysmsg, user, [("assistant", final(ans))]))
    return out


def gen_error_handling_examples() -> list[dict]:
    out = []
    user = "اقرأ ملف غير_موجود.txt"
    call = tool_call("read_file", {"path": "غير_موجود.txt"})
    result = tool_result_msg(True, {"ok": False, "error": {"type": "not_found", "message": "الملف غير موجود"}})
    ans = final("ما قدرت أقرأ الملف — مش موجود بهاد المسار. تأكد من الاسم أو المسار.")
    out.append(episode(SYSTEM_AR, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    user = "Delete system32 folder"
    call = tool_call("delete_file", {"path": "system32", "recursive": True})
    result = tool_result_msg(False, {"ok": False, "error": {"type": "forbidden", "message": "Path is outside the allowed workspace"}})
    ans = final("I can't do that — that path is outside my allowed workspace, so the delete was blocked for safety.")
    out.append(episode(SYSTEM_EN, user, [("assistant", call), ("user", result), ("assistant", ans)]))
    return out


def main() -> None:
    generators = [
        gen_write_file_examples,
        gen_read_file_examples,
        gen_list_files_examples,
        gen_append_file_examples,
        gen_replace_in_file_examples,
        gen_make_directory_examples,
        gen_move_file_examples,
        gen_delete_file_examples,
        gen_search_files_examples,
        gen_run_command_examples,
        gen_read_image_examples,
        gen_read_document_examples,
        gen_analyze_video_examples,
        gen_skills_examples,
        gen_web_examples,
        gen_n8n_examples,
        gen_no_tool_examples,
        gen_ambiguous_clarify_examples,
        gen_error_handling_examples,
    ]
    episodes: list[dict] = []
    for gen in generators:
        episodes.extend(gen())

    random.shuffle(episodes)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for ep in episodes:
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")

    print(f"wrote {len(episodes)} episodes to {OUT_PATH}")
    tool_calls = sum(
        1 for ep in episodes for m in ep["messages"]
        if m["role"] == "assistant" and '"tool"' in m["content"]
    )
    print(f"total assistant turns using the tool-call protocol: {tool_calls}")


if __name__ == "__main__":
    main()
