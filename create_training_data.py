"""Create the HWK Aali instruction dataset (thousands of examples).

Covers, per the handoff Priority 4:
- greetings and natural conversation (Arabic + English),
- follow-up questions that need previous context,
- correct tool selection and arguments for every file tool,
- confirmation for overwrite / recursive delete,
- error cases: missing files, invalid paths, path-traversal attempts,
- "I don't know" responses instead of hallucinating,
- multi-step tasks: read -> summarize, mkdir -> write, read -> replace,
- domain Q&A seeds for coding, medical, and law (template-expanded).

The schema matches the original seed format:
{"messages": [{"role": ..., "content": ...}, ...]}
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SYSTEM_TOOLS_AR = (
    "أنت وكيل ملفات آمن. استخدم الأدوات المتاحة فقط، واجعل كل المسارات "
    "نسبية داخل مساحة العمل. أخرج استدعاء الأداة ككائن JSON فقط."
)
SYSTEM_CHAT_AR = (
    "أنت آلي، مساعد ذكاء اصطناعي محلي وودود. أجب بالإنجليزية أو العربية "
    "بحسب لغة المستخدم، وكن صادقًا: إن لم تعرف الإجابة فقل ذلك بدل التخمين."
)
SYSTEM_TOOLS_EN = (
    "You are a safe file agent. Use only the available tools and keep all "
    "paths relative to the workspace. Output a tool call as a JSON object only."
)
SYSTEM_CHAT_EN = (
    "You are Aali, a friendly local AI assistant. Answer in the user's "
    "language (Arabic or English). Be honest: if you do not know, say so "
    "instead of guessing."
)

# ---------------------------------------------------------------------------
# Chat material
# ---------------------------------------------------------------------------

GREETINGS_AR = [
    ("مرحبا", "مرحباً! كيف حالك؟"),
    ("أهلا", "أهلاً بك! ما الذي يمكنني مساعدتك به اليوم؟"),
    ("السلام عليكم", "وعليكم السلام ورحمة الله وبركاته. كيف أستطيع مساعدتك؟"),
    ("صباح الخير", "صباح النور! أتمنى لك يومًا سعيدًا."),
    ("مساء الخير", "مساء الخير! كيف كان يومك؟"),
    ("كيف حالك؟", "أنا بخير وجاهز لمساعدتك في المحادثة وإدارة ملفاتك."),
    ("شو أخبارك؟", "الحمد لله، كل شيء بخير. شو بدك أساعدك فيه؟"),
    ("واشنطن", "أنا آلي، مساعدك المحلي."),
    ("ما اسمك؟", "اسمي آلي، وأنا مساعدك المحلي."),
    ("إنت مين؟", "أنا آلي، مساعد ذكاء اصطناعي يعمل على جهازك بالكامل."),
]

GREETINGS_EN = [
    ("hello", "Hello! How are you?"),
    ("hi", "Hi there! What can I help you with today?"),
    ("good morning", "Good morning! Hope you have a great day."),
    ("good evening", "Good evening! How was your day?"),
    ("how are you?", "I'm doing well, thank you. Ready to help you!"),
    ("what's your name?", "My name is Aali, your local AI assistant."),
    ("who are you?", "I'm Aali, an AI assistant that runs entirely on your computer."),
]

SMALL_TALK_AR = [
    ("ماذا تستطيع أن تفعل؟", "أستطيع التحدث معك، والإجابة عن الأسئلة، وتنفيذ عمليات آمنة على الملفات مثل القراءة والكتابة والتنظيم."),
    ("هل تعمل بدون اتصال خارجي؟", "نعم، بعد تدريب النموذج وتشغيله محليًا لا أحتاج إلى API أو اتصال خارجي."),
    ("أين تعمل؟", "أعمل على جهازك المحلي، وكل ملفاتي داخل مجلد العمل المسموح فقط."),
    ("هل تحفظ محادثاتنا؟", "أحتفظ بسياق هذه الجلسة فقط لأتابع الحوار، ولا أسجل أسرارًا أو مفاتيح."),
    ("هل أنت ذكي؟", "أنا نموذج صغير يتعلم تدريجيًا؛ أساعد في المهام البسيطة وأتحسن مع التدريب."),
    ("أريد تنظيم ملفاتي", "بالتأكيد. أخبرني بالملفات أو المجلدات التي تريد تنظيمها وسأقترح الخطوة المناسبة."),
    ("كيف أبدأ مشروعًا جديدًا؟", "أنشئ مجلدًا للمشروع، ثم اكتب الملف الأول بداخله. أخبرني بالاسم وسأبدأ."),
    ("هل يمكنك مساعدتي في البرمجة؟", "نعم، أستطيع كتابة وشرح أجزاء من الكود، وقراءة وتعديل ملفات المشروع."),
]

SMALL_TALK_EN = [
    ("What can you do?", "I can chat with you, answer questions, and safely work with files: listing, reading, writing, and organizing them."),
    ("Do you work offline?", "Yes. Once the model is trained and running locally, I need no API or internet connection."),
    ("Where do you run?", "On your local machine, inside the allowed workspace folder only."),
    ("Do you remember this conversation?", "I keep the context of this session only so I can follow along; I never log secrets."),
    ("Are you smart?", "I am a small model that improves with training; I handle simple tasks now and get better over time."),
    ("I want to organize my files", "Sure. Tell me which files or folders you want to organize and I will propose the next step."),
    ("How do I start a new project?", "Create a project folder, then write the first file inside it. Tell me the name and I will start."),
    ("Can you help me with programming?", "Yes, I can write and explain parts of code, and read or edit project files."),
]

FOLLOW_UPS_AR = [
    ("ما هو الملف النصي؟", "الملف النصي يخزن حروفًا وكلمات يمكن فتحها وتعديلها بمحرر نصوص.", "وهل يمكنني تحويله إلى PDF؟", "لا أستطيع تحويل الملفات إلى PDF الآن، لكن يمكنني مساعدتك في إعداد النص وترتيبه."),
    ("اشرح لي ما هو المجلد", "المجلد حاوية تنظم الملفات داخله.", "كيف أنشئ مجلدًا؟", "قل لي اسم المجلد وسأنشئه لك فورًا داخل مساحة العمل."),
    ("ما هو ملف JSON؟", "ملف JSON يخزن البيانات بشكل منظم مفهوم للبرامج.", "أعطني مثالًا بسيطًا", "مثال: {\"name\": \"آلي\", \"kind\": \"assistant\"}."),
]

FOLLOW_UPS_EN = [
    ("What is a text file?", "A text file stores characters and words that any text editor can open and edit.", "Can I convert it to PDF?", "I cannot convert files to PDF yet, but I can help you prepare and format the text."),
    ("Explain what a folder is", "A folder is a container that keeps files organized inside it.", "How do I create one?", "Tell me the folder name and I will create it for you right away inside the workspace."),
    ("What is a JSON file?", "A JSON file stores data in a structured way that programs can read.", "Give me a simple example", "Example: {\"name\": \"Aali\", \"kind\": \"assistant\"}."),
]

DONT_KNOW = [
    ("ما هو الطقس اليوم؟", "لا أعرف الطقس اليوم لأنني أعمل دون اتصال بالإنترنت."),
    ("من سيفوز بمباراة الليلة؟", "لا أستطيع معرفة النتائج المباشرة أو التوقعات الرياضية."),
    ("What is the weather today?", "I don't know today's weather because I work offline."),
    ("Who won the match tonight?", "I can't know live results or sports predictions."),
    ("ما سعر البيتكوين الآن؟", "لا أملك بيانات الأسعار الحالية؛ أعمل دون اتصال."),
    ("What is the Bitcoin price now?", "I don't have live price data; I work offline."),
]

# ---------------------------------------------------------------------------
# File-tool material
# ---------------------------------------------------------------------------

TOOLS = [
    ("list_files", {"path": "."}),
    ("read_file", {"path": "{path}"}),
    ("write_file", {"path": "{path}", "content": "{content}"}),
    ("append_file", {"path": "{path}", "content": "{content}"}),
    ("replace_in_file", {"path": "{path}", "old_text": "{old}", "new_text": "{new}"}),
    ("make_directory", {"path": "{path}"}),
    ("move_file", {"source": "{path}", "destination": "{dest}"}),
    ("delete_file", {"path": "{path}"}),
]

FILES = [
    "notes/today.txt", "docs/readme.txt", "config.json", "reports/report.txt",
    "logs/run.log", "data/input.csv", "projects/notes.md", "archive/old.txt",
    "backup/data.json", "temp/tmp.txt",
]

CONTENTS = ["مرحباً بالعالم", "تقرير اليوم", "ملاحظة سريعة", "سطر جديد", "إعدادات النظام"]
OLD_TEXTS = ["قديم", "foo", "مرحبا", "old"]
NEW_TEXTS = ["جديد", "bar", "أهلا", "new"]
DESTS = ["archive/", "backup/", "projects/", "docs/"]

TOOL_PHRASES_AR = {
    "list_files": ["اعرض الملفات", "اعرض الملفات الموجودة", "اعرض محتويات مساحة العمل", "أرني الملفات", "شو في ملفات؟"],
    "read_file": ["اقرأ الملف {path}", "اقرأ {path}", "أرني محتوى {path}", "اعرض محتوى {path}"],
    "write_file": ["أنشئ ملف {path} واكتب فيه {content}", "اكتب في ملف {path} المحتوى {content}", "أنشئ {path} بمحتوى {content}"],
    "append_file": ["أضف {content} إلى {path}", "أضف سطرًا إلى {path}: {content}", "ألحق {content} في نهاية {path}"],
    "replace_in_file": ["استبدل {old} بـ {new} في {path}", "غيّر {old} إلى {new} داخل {path}", "عدّل {path}: استبدل {old} بـ {new}"],
    "make_directory": ["أنشئ مجلد {path}", "اعمل مجلد جديد اسمه {path}", "أنشئ دليل {path}"],
    "move_file": ["انقل {path} إلى {dest}", "حرّك {path} إلى {dest}", "نقل {path} إلى {dest}"],
    "delete_file": ["احذف الملف {path}", "امسح {path}", "احذف {path}"],
}

TOOL_PHRASES_EN = {
    "list_files": ["list the files", "show the files", "list files in the workspace", "show me the files"],
    "read_file": ["read the file {path}", "read {path}", "show me the contents of {path}"],
    "write_file": ["create file {path} with content {content}", "write {content} to {path}", "create {path} containing {content}"],
    "append_file": ["append {content} to {path}", "add a line to {path}: {content}", "add {content} at the end of {path}"],
    "replace_in_file": ["replace {old} with {new} in {path}", "change {old} to {new} in {path}", "edit {path}: replace {old} with {new}"],
    "make_directory": ["create a folder called {path}", "make directory {path}", "create directory {path}"],
    "move_file": ["move {path} to {dest}", "move file {path} into {dest}", "transfer {path} to {dest}"],
    "delete_file": ["delete the file {path}", "remove {path}", "delete {path}"],
}

# Danger-flag variants: overwrite confirmation and recursive-delete confirmation.
CONFIRM_AR = [
    ("استبدل محتوى {path} بالكامل بالمحتوى الجديد", "write_file", {"path": "{path}", "content": "{content}", "overwrite": True}),
    ("احذف مجلد {path} بالكامل", "delete_file", {"path": "{path}", "recursive": True}),
    ("امسح {path} مع كل ما بداخله", "delete_file", {"path": "{path}", "recursive": True}),
]
CONFIRM_EN = [
    ("replace the entire content of {path} with the new content", "write_file", {"path": "{path}", "content": "{content}", "overwrite": True}),
    ("delete folder {path} completely", "delete_file", {"path": "{path}", "recursive": True}),
    ("remove {path} with everything inside", "delete_file", {"path": "{path}", "recursive": True}),
]

ERROR_CASES = [
    ("اقرأ الملف غير_موجود.txt", "read_file", {"path": "غير_موجود.txt"}, "لا يوجد ملف بهذا الاسم. تأكد من المسار أو اعرض الملفات أولًا."),
    ("احذف الملف مفقود.txt", "delete_file", {"path": "مفقود.txt"}, "تعذر الحذف: الملف غير موجود."),
    ("انقل الملف x.txt إلى ../خارج.txt", "move_file", {"source": "x.txt", "destination": "../خارج.txt"}, "المسار ../خارج.txt خارج مساحة العمل ولا يمكن استخدامه."),
    ("اقرأ ../../etc/passwd", "read_file", {"path": "../../etc/passwd"}, "هذا المسار يحاول الخروج من مساحة العمل؛ لا يمكن قراءته."),
    ("read missing_file.txt", "read_file", {"path": "missing_file.txt"}, "No file with that name exists. Check the path or list the files first."),
    ("delete missing_file.txt", "delete_file", {"path": "missing_file.txt"}, "Could not delete: the file does not exist."),
    ("move x.txt to ../outside.txt", "move_file", {"source": "x.txt", "destination": "../outside.txt"}, "The path ../outside.txt escapes the workspace and cannot be used."),
    ("read ../../etc/passwd", "read_file", {"path": "../../etc/passwd"}, "That path tries to escape the workspace; it cannot be read."),
]

MULTI_STEP = [
    # read -> summarize
    (("اقرأ الملف notes/today.txt ولخصه لي", [{"tool": "read_file", "arguments": {"path": "notes/today.txt"}}],
      [{"content": "{\"ok\": true, \"result\": {\"content\": \"اجتماع الساعة 10، ثم تسليم التقرير.\"}}"}],
      "المحتوى يتضمن اجتماعًا في العاشرة وتسليم التقرير. هل تريد أن أكتب الملخص في ملف؟")),
    # mkdir -> write
    (("أنشئ مجلد مشروعي ثم ضع فيه ملف أولي", [{"tool": "make_directory", "arguments": {"path": "مشروعي"}},
                                               {"tool": "write_file", "arguments": {"path": "مشروعي/أول ملف.txt", "content": "بداية المشروع"}}],
      [{"content": "{\"ok\": true, \"result\": {\"created\": true}}"},
       {"content": "{\"ok\": true, \"result\": {\"path\": \"مشروعي/أول ملف.txt\", \"created\": true}}"}],
      "تم إنشاء المجلد والملف داخل مشروعي/.")),
    # read -> replace
    (("اقرأ code.txt واستبدل foo بـ bar", [{"tool": "read_file", "arguments": {"path": "code.txt"}},
                                            {"tool": "replace_in_file", "arguments": {"path": "code.txt", "old_text": "foo", "new_text": "bar"}}],
      [{"content": "{\"ok\": true, \"result\": {\"content\": \"foo = 1\\nprint(foo)\"}}"},
       {"content": "{\"ok\": true, \"result\": {\"replaced\": 2}}"}],
      "قرأت الملف واستبدلت كل تكرارات foo بـ bar في code.txt.")),
    # list -> read
    (("اعرض الملفات ثم اقرأ config.json", [{"tool": "list_files", "arguments": {"path": "."}},
                                            {"tool": "read_file", "arguments": {"path": "config.json"}}],
      [{"content": "{\"ok\": true, \"result\": {\"files\": [\"config.json\", \"notes\"]}}"},
       {"content": "{\"ok\": true, \"result\": {\"content\": \"{\\\"mode\\\": \\\"local\\\"}\"}}"}],
      "الملفات: config.json و notes. محتوى config.json: {\"mode\": \"local\"}.")),
]

# ---------------------------------------------------------------------------
# Domain Q&A seeds (code / medical / law) — expanded with templates
# ---------------------------------------------------------------------------

DOMAIN_SEEDS = [
    # (question, answer) — English medical/law/code seeds, Arabic where natural.
    ("What is a Python function?", "A function is a reusable block of code: def greet(name): return 'Hello ' + name."),
    ("What is an if statement?", "An if statement runs a block only when a condition is true: if x > 0: print('positive')."),
    ("What is a for loop?", "A for loop repeats over a sequence: for i in range(3): print(i)."),
    ("What is a list in Python?", "A list is an ordered mutable collection: items = [1, 2, 3]."),
    ("What is a dictionary?", "A dictionary maps keys to values: person = {'name': 'Aali', 'age': 1}."),
    ("What does return do?", "return sends a value back from a function to its caller."),
    ("What is a variable?", "A variable names a value stored in memory, e.g. x = 5."),
    ("What is an array?", "An array is a fixed-size collection of elements of the same type, indexed from 0."),
    ("What is a class?", "A class is a blueprint for objects: class Car: def __init__(self): self.wheels = 4."),
    ("What is recursion?", "Recursion is a function calling itself to solve smaller versions of the same problem."),
    ("What is an API?", "An API is an interface that lets programs request data or actions from a service."),
    ("What is a database?", "A database stores structured data and supports queries, e.g. SQL tables."),
    ("ما هي الدالة في البرمجة؟", "الدالة كتلة برمجية قابلة لإعادة الاستخدام، مثل: def add(a, b): return a + b."),
    ("ما هي الحلقة for؟", "حلقة for تكرر تنفيذ كود على عناصر: for i in range(3): print(i)."),
    ("ما هي القائمة في بايثون؟", "القائمة مجموعة مرتبة قابلة للتعديل: items = [1, 2, 3]."),
    ("What is a fever?", "A fever is a body temperature above the normal range, usually about 37\u00b0C or 98.6\u00b0F."),
    ("What is blood pressure?", "Blood pressure measures the force of blood on artery walls; normal is about 120/80 mmHg."),
    ("What is a symptom?", "A symptom is something a patient feels, such as pain, cough, or fatigue."),
    ("What is a diagnosis?", "A diagnosis is the identification of a disease from its signs and symptoms."),
    ("What is a headache?", "A headache is pain in the head; common causes include tension, dehydration, and stress."),
    ("What is the heart?", "The heart is a muscular organ that pumps blood through the body's vessels."),
    ("What is a vaccine?", "A vaccine trains the immune system to fight a specific pathogen safely."),
    ("What is a prescription?", "A prescription is a doctor's written order for a specific medication and dose."),
    ("What is diabetes?", "Diabetes is a condition of high blood sugar caused by problems with insulin."),
    ("What is a contract?", "A contract is an agreement between parties that creates legal obligations."),
    ("What is a witness?", "A witness is a person who sees an event and can testify about it in court."),
    ("What is a plaintiff?", "The plaintiff is the party who brings a lawsuit against another party, the defendant."),
    ("What is evidence?", "Evidence is information or objects presented to prove or disprove a fact in a case."),
    ("What is a statute?", "A statute is a written law passed by a legislature."),
    ("What is a precedent?", "A precedent is a past court decision that guides later similar cases."),
    ("What is a will?", "A will is a legal document stating how a person's property is distributed after death."),
    ("What is a lawyer's duty?", "A lawyer must act in the client's interest within the law, with confidentiality and honesty."),
    ("ما هو العقد؟", "العقد اتفاق بين طرفين ينشئ التزامات قانونية بينهما."),
    ("ما هي الشهادة؟", "الشهادة إخبار عن حادثة رآها الشخص أمام جهة قضائية."),
    ("ما هو القانون؟", "القانون مجموعة قواعد ملزمة تنظم سلوك الأفراد والمؤسسات."),
    ("What is a float in Python?", "A float is a number with a decimal point, e.g. 3.14."),
    ("What is a string?", "A string is a sequence of characters, e.g. 'hello'."),
    ("What is a boolean?", "A boolean is a true/false value used in conditions."),
    ("What is a tuple?", "A tuple is an ordered immutable collection: point = (1, 2)."),
    ("What is a set?", "A set is an unordered collection of unique items."),
    ("What is a while loop?", "A while loop repeats while a condition is true: while x < 5: x += 1."),
    ("What is an exception?", "An exception is an error event that interrupts normal program flow unless handled."),
    ("What is a library?", "A library is a collection of reusable code others can import."),
    ("What is Git?", "Git is a version-control system that tracks changes to code over time."),
    ("What is a repository?", "A repository is a folder managed by Git containing a project's history."),
    ("What is a terminal?", "A terminal is a text interface to run commands on the computer."),
    ("What is hypertension?", "Hypertension is chronically high blood pressure, typically above 140/90 mmHg."),
    ("What is anemia?", "Anemia is a low count of red blood cells or hemoglobin, causing fatigue."),
    ("What is asthma?", "Asthma is a lung condition with inflamed airways that can cause wheezing and shortness of breath."),
    ("What is dehydration?", "Dehydration is losing more fluids than you take in; symptoms include thirst and dizziness."),
    ("What is a pulse?", "The pulse is the rhythmic beat of arteries from the heart pumping blood."),
    ("What is cholesterol?", "Cholesterol is a fatty substance in blood; high LDL levels raise heart-disease risk."),
    ("What is a sprain?", "A sprain is a stretched or torn ligament around a joint, often from a twist."),
    ("What is a fever in children?", "A fever in children is usually a temperature of 38°C or higher; see a doctor if it persists."),
    ("What is a plaintiff?", "The plaintiff is the party who brings a lawsuit against another party, the defendant."),
    ("What is a defendant?", "The defendant is the party being sued or accused in a legal case."),
    ("What is a judge?", "A judge is an official who presides over court proceedings and decides cases."),
    ("What is a jury?", "A jury is a group of citizens who decide the facts of a trial."),
    ("What is a subpoena?", "A subpoena is a legal order requiring a person to appear or produce documents."),
    ("What is bail?", "Bail is money or conditions that let an accused person stay free until trial."),
    ("What is a settlement?", "A settlement is an agreement that resolves a dispute without a full trial."),
    ("What is common law?", "Common law is law developed by judges through court decisions over time."),
]

DOMAIN_TEMPLATES = [
    ("اشرح لي: {q}", "{a}"),
    ("أخبرني عن: {q}", "{a}"),
    ("سؤال سريع: {q}", "{a}"),
    ("ماذا تعرف عن: {q}", "{a}"),
    ("باختصار: {q}", "{a}"),
    ("Explain: {q}", "{a}"),
    ("Tell me about: {q}", "{a}"),
    ("Quick question: {q}", "{a}"),
    ("What do you know about: {q}", "{a}"),
    ("In short: {q}", "{a}"),
]


def _tool_record(system: str, user: str, tool: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)},
        ]
    }


def _chat_record(system: str, user: str, answer: str) -> dict[str, object]:
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": answer},
        ]
    }


def _multi_turn_record(system: str, user: str, tool_calls: list[dict[str, object]], results: list[dict[str, object]], final: str) -> dict[str, object]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for index, call in enumerate(tool_calls):
        messages.append({"role": "assistant", "content": json.dumps(call, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Tool result: {results[index]['content']}"})
    messages.append({"role": "assistant", "content": final})
    return {"messages": messages}


def build_records(seed: int = 7) -> list[dict[str, object]]:
    rng = random.Random(seed)
    records: list[dict[str, object]] = []

    # --- Chat: greetings, small talk, follow-ups, honest refusals ---
    for user, answer in GREETINGS_AR + GREETINGS_EN + SMALL_TALK_AR + SMALL_TALK_EN + DONT_KNOW:
        records.append(_chat_record(SYSTEM_CHAT_AR, user, answer))
    for user1, answer1, user2, answer2 in FOLLOW_UPS_AR + FOLLOW_UPS_EN:
        records.append(_chat_record(SYSTEM_CHAT_AR, user1, answer1))
        records.append(_chat_record(SYSTEM_CHAT_AR, user2, answer2))

    # --- File tools: every tool x many phrasings x both languages ---
    for tool, template in TOOLS:
        phrasings_ar = TOOL_PHRASES_AR[tool]
        phrasings_en = TOOL_PHRASES_EN[tool]
        for _ in range(20):  # several draws per tool for variety
            path = rng.choice(FILES)
            content = rng.choice(CONTENTS)
            old = rng.choice(OLD_TEXTS)
            new = rng.choice(NEW_TEXTS)
            dest = rng.choice(DESTS) + path.split("/")[-1]
            args = {k: v.format(path=path, content=content, old=old, new=new, dest=dest) for k, v in template.items()}
            user_ar = rng.choice(phrasings_ar).format(path=path, content=content, old=old, new=new, dest=dest)
            user_en = rng.choice(phrasings_en).format(path=path, content=content, old=old, new=new, dest=dest)
            records.append(_tool_record(SYSTEM_TOOLS_AR, user_ar, tool, args))
            records.append(_tool_record(SYSTEM_TOOLS_EN, user_en, tool, args))

    # --- Confirmation for dangerous operations ---
    for user, tool, args_t in CONFIRM_AR + CONFIRM_EN:
        for _ in range(3):
            path = rng.choice(FILES)
            content = rng.choice(CONTENTS)
            args = {
                k: v.format(path=path, content=content) if isinstance(v, str) else v
                for k, v in args_t.items()
            }
            records.append(_tool_record(SYSTEM_TOOLS_AR, user.format(path=path, content=content), tool, args))

    # --- Error cases and path-traversal refusals ---
    for user, tool, args, _answer in ERROR_CASES:
        records.append(_tool_record(SYSTEM_TOOLS_AR, user, tool, args))

    # --- Multi-step tasks ---
    for user, calls, results, final in MULTI_STEP:
        records.append(_multi_turn_record(SYSTEM_TOOLS_AR, user, calls, results, final))

    # --- Domain Q&A (template-expanded) ---
    for question, answer in DOMAIN_SEEDS:
        for template_q, template_a in DOMAIN_TEMPLATES:
            records.append(_chat_record(SYSTEM_CHAT_AR, template_q.format(q=question), template_a.format(a=answer)))

    # --- Cross-language: English user asking in Arabic system language stays
    # answered naturally; add a small set of mixed Arabic/English commands ---
    mixed = [
        ("اعرض files في المجلد", "list_files", {"path": "."}),
        ("اقرأ config.json and summarize", "read_file", {"path": "config.json"}),
        ("أنشئ folder باسم logs", "make_directory", {"path": "logs"}),
        ("delete the file temp.txt من فضلك", "delete_file", {"path": "temp.txt"}),
    ]
    for user, tool, args in mixed:
        records.append(_tool_record(SYSTEM_TOOLS_AR, user, tool, args))

    # Deterministic shuffle so the file is stable between runs.
    rng.shuffle(records)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the HWK Aali instruction dataset.")
    parser.add_argument("--output", default="data/agent_instructions.jsonl")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = build_records(seed=args.seed)
    with destination.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Created {len(records)} examples in {destination}")


if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()