"""Create a small Arabic instruction dataset for the file-agent tool protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SYSTEM_PROMPT = (
    "أنت وكيل ملفات آمن. استخدم الأدوات المتاحة فقط، واجعل كل المسارات "
    "نسبية داخل مساحة العمل. أخرج استدعاء الأداة ككائن JSON فقط."
)


EXAMPLES = [
    ("اعرض الملفات الموجودة في مساحة العمل", "list_files", {"path": "."}),
    ("اعرض محتويات مجلد المشاريع بشكل متكرر", "list_files", {"path": "projects", "recursive": True}),
    ("اقرأ ملف notes/today.txt", "read_file", {"path": "notes/today.txt"}),
    ("اقرأ أول 2000 حرف من docs/readme.txt", "read_file", {"path": "docs/readme.txt", "max_chars": 2000}),
    ("أنشئ ملف hello.txt واكتب فيه مرحباً بالعالم", "write_file", {"path": "hello.txt", "content": "مرحباً بالعالم"}),
    ("اكتب تقرير اليوم في reports/today.txt", "write_file", {"path": "reports/today.txt", "content": "تقرير اليوم"}),
    ("استبدل محتوى config.json بالكامل بالمحتوى الجديد", "write_file", {"path": "config.json", "content": "{}", "overwrite": True}),
    ("أضف سطر النجاح إلى logs/run.log", "append_file", {"path": "logs/run.log", "content": "نجح التشغيل\n"}),
    ("استبدل كلمة قديم بكلمة جديد في notes.txt", "replace_in_file", {"path": "notes.txt", "old_text": "قديم", "new_text": "جديد"}),
    ("استبدل كل تكرارات foo بـ bar في code.txt", "replace_in_file", {"path": "code.txt", "old_text": "foo", "new_text": "bar", "replace_all": True}),
    ("أنشئ مجلد archive/2026", "make_directory", {"path": "archive/2026"}),
    ("انقل report.txt إلى archive/report.txt", "move_file", {"source": "report.txt", "destination": "archive/report.txt"}),
    ("انقل النسخة إلى backup/report.txt مع السماح بالاستبدال", "move_file", {"source": "report.txt", "destination": "backup/report.txt", "overwrite": True}),
    ("احذف الملف temp.txt", "delete_file", {"path": "temp.txt"}),
    ("احذف مجلد cache بالكامل", "delete_file", {"path": "cache", "recursive": True}),
]

CHAT_EXAMPLES = [
    ("مرحبا", "مرحباً! أنا آلي، كيف حالك؟"),
    ("السلام عليكم", "وعليكم السلام ورحمة الله وبركاته. كيف أستطيع مساعدتك؟"),
    ("كيف حالك؟", "أنا بخير وجاهز لمساعدتك في المحادثة وإدارة ملفاتك."),
    ("ما اسمك؟", "اسمي آلي، وأنا مساعدك المحلي."),
    ("ماذا تستطيع أن تفعل؟", "أستطيع التحدث معك، والإجابة عن الأسئلة، وتنفيذ عمليات آمنة على الملفات."),
    ("هل تعمل بدون اتصال خارجي؟", "نعم، بعد تدريب النموذج وتشغيله محليًا لا أحتاج إلى API أو اتصال خارجي."),
    ("أريد تنظيم ملفاتي", "بالتأكيد. أخبرني بالملفات أو المجلدات التي تريد تنظيمها وسأقترح الخطوة المناسبة."),
    ("اشرح لي ما هو الملف النصي", "الملف النصي يخزن حروفًا وكلمات يمكن فتحها وتعديلها بمحرر نصوص."),
]


def build_records() -> list[dict[str, object]]:
    tool_records = [
        {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"tool": tool, "arguments": arguments},
                        ensure_ascii=False,
                    ),
                },
            ]
        }
        for user_message, tool, arguments in EXAMPLES
    ]
    chat_records = [
        {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer},
            ]
        }
        for user_message, answer in CHAT_EXAMPLES
    ]
    return chat_records + tool_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Arabic file-agent training data.")
    parser.add_argument("--output", default="data/agent_instructions.jsonl")
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = build_records()
    with destination.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Created {len(records)} examples in {destination}")


if __name__ == "__main__":
    main()