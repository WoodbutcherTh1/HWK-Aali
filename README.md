# مشروع آلي (HWK-Aali)

مساعد ذكاء اصطناعي **محلي** يُبنى من الصفر (أوزان عشوائية، بدون نموذج
مدرَّب مسبقًا، بدون مفتاح API إلزامي) على بطاقة RTX 3070 / 8GB، ثنائي
اللغة (عربية + إنجليزية)، ويأتي مع **وكيل كامل**: ذاكرة دائمة، أدوات
ملفات وحاسوب، وسائط، أمان، واختبارات ترقية.

> **الاتجاه الوحيد المدعوم هو التدريب من الصفر.** سكربتات Qwen /
> Transformers القديمة في `legacy/` للرجوع إليها فقط.

## الحالة الآن (2026-09-07)

- **المرحلة أ (تدريب عام)**: جارية — ~80,800 / 90,000 خطوة (≈90%)،
  أفضل eval_loss حتى الآن **3.88** ويستمر بالتحسن (بدأ الانهيار من 5.6).
  تنتهي الليلة، وبمجرد تحرر الـ GPU ينطلق **تلقائيًا** سلسلة
  الامتحان → إعادة SFT → الحكم على الترقية (انظر أدناه).
- **الوكيل (العقل)**: يعمل بالفعل — كل القدرات في الجدول أدناه
  مُنفَّذة في الكود ومختبرة (42/42 اختبارًا أخضر).
- **النموذج الحالي**: 125M معلمة تقريبًا (d_model 768، 12 طبقة،
  12 رأس)، سياق 1024 الآن مع خطة تمديد إلى 4096
  (`scripts/after_phaseA.bat`).

## ماذا يستطيع آلي أن يفعل؟ (Capabilities at a glance)

| القدرة | الأداة / الملف | التفاصيل |
|---|---|---|
| قراءة/كتابة/تعديل/بحث الملفات | `read_file` `write_file` `append_file` `search_files` `list_files` `delete_file` | محصورة داخل مجلد العمل، كل عملية مُسجَّلة |
| تشغيل أوامر آمنة | `run_command` | قائمة مسموحات (python/pytest/node/npm/git…)، حاجب **منع تسريب البيئة** (يحظر `os.environ`/`process.env`/`printenv`/`$VAR`/`.env`)، وإخفاء أسرار من المخرجات. تعطيل كلي: `HWK_ALLOW_COMMANDS=0` |
| التحكم بالحاسوب | `machine_ops` (scripts/machine_ops.py) | فتح تطبيقات/ملفات/روابط، تثبيت وإزالة برامج (winget/npm)، قائمة وإيقاف العمليات، إحصاءات النظام. الإجراءات غير القابلة للتراجع تحتاج `force=true` + تأكيد المستخدم، وحماية خاصة لعمليات التدريب |
| ذاكرة دائمة | `memory` (file_agent/memory.py) | يتذكر عبر إعادة التشغيل والمحادثات الجديدة في `%USERPROFILE%\.aali\` — لو قلت الصباح "لا تفتح روابط واتساب" وعشية قلت "افتحها" سيخبرك بما قلته سابقًا ثم يتبع الأحدث. البحث هجين (كلمات + تشابه دلالي). عرض وتعديل وحذف: `scripts/memory_review.py` |
| أمان مُدرَّس من كوارث حقيقية | skills/security-rules.md + docs/ai_security_lessons.md | دروس موثقة بمصادرها (Samsung 2023، OpenAI 2023، DeepSeek 2025، Microsoft 2024، nx/npm 2025): لا يكشف كوده/سيس‌برومبته/بيئته أبدًا، لا تسريب أسرار، حقن الأوامر من الويب يُعامل كبيانات لا أوامر، حقن الذاكرة محجوب (المستخدم فقط يكتب فيها) |
| فهم/توليد الصور | `read_image` (OCR ويندوز) + Stable Diffusion | التوليد والتعديل في بيئة منفصلة `D:\hwk-tools\sd-venv` — النتائج في `D:\hwk-projects\images\` |
| تعديل الصور والفيديو | `edit_image` `edit_video` `analyze_video` | Pillow + ffmpeg: قص/تدوير/تغيير حجم، GIF، قطع مقاطع، استخراج صوت/إطارات، سرعة، تلاشي، علامة مائية… (skills/media-editing.md) |
| توليد إيموجي من الصفر | `generate_emoji` (scripts/generate_emoji.py) | وجوه مخصصة بأشكال مشاعر × لوحات ألوان × إكسسوارات، PNG شفاف — على المعالج في ثوانٍ |
| ذكاء الإيموجي | file_agent/emoji.py + skills/emoji-etiquette.md | يقرأ المشاعر في إيموجي المستخدم ويرد عليها أولًا، ويستخدم إيموجي واحدًا مناسبًا — ولا إيموجي أبدًا على الأخبار السيئة أو التحذيرات الأمنية |
| تصحيح تلقائي ذكي | file_agent/autocorrect.py | "hai dode can u help me" → يفهمها ويُظهر قراءة مصححة "hi dude…". تطبيع عربي (همزات/تاء مربوطة). لا يلمس المسارات أو الأكواد أو الاقتباسات أبدًا |
| اقتراحات تلقائية | file_agent/suggestions.py | حتى 3 اقتراحات متابعة بعد كل رد، في لغة المستخدم، بلا اقتراحات خطرة (لا حذف/إزالة/إيقاف) — عبر الـ API و رقم في الـ CLI |
| تحقق ذاتي طبقة-بطبقة | skills/self-verification.md | بروتوكول مقاومة الهلوسة: تحقق من الدليل قبل الادعاء، أعد التحقق بعد التنفيذ، قل "لا أعرف" بأمان |
| بحث ويب وجلب روابط | `fetch_url` + skills/web-research.md | قراءة الصفحات والمصادر قبل الإجابة |

كل الأدوات مسجلة في `file-agent/file_agent/file_tools.py` (15 أداة)،
والعقل (`file-agent/agent_loop.py`) يشغّل 4 مسارات: نموذج محلي مدرَّب
من الصفر، Ollama (سياق مرفوع إلى 8192)، مزودون أصليون، ووضع سحابي
اختياري (OpenRouter) إن أضفت مفتاحًا.

## مهارات جاهزة (skills/)

`self-verification` · `security-rules` · `machine-ops` · `pc-commands` ·
`media-tools` · `media-editing` · `emoji-etiquette` · `web-research` ·
`connectors` · `n8n-workflow`

## حلقة التعلم — كيف يتعلّم آلي فعليًا

1. **معلمون حقيقيون**: `claude_teach.py` و `deepseek_teacher.py` يحولان
   ردود النماذج القوية إلى بيانات تدريب. و عبر بوابة **OmniRoute**
   المحلية (localhost:20128) يمكن استعمال نماذج مجانية بعد نفاد
   الحصة — `claude-free.bat` يشغّل Claude Code عبرها دون المساس
   باشتراكك العادي (docs/omniroute_setup.md).
2. **مختبر المعلم** (`scripts/omniroute_mentor_lab.py`): أعطى Claude
   **30 مهمة بناء مختلفة** (تطبيقات/ألعاب/ملفات، عربي وإنجليزي) نفّذها
   عبر طبقة أدوات آلي نفسها داخل بيئات معزولة — **190 نداء أداة،
   55 فشلًا حقيقيًا مع التعافي** — كلها بالصيغة التدريبية الدقيقة لآلي.
3. **التقاط الإخفاقات من الواقع** (`scripts/mentor_capture.py`): يقرأ
   محفوظات Claude Code نفسها (بما فيها جلسات Freebuff) ويستخرج حلقات
   "خطأ ← تعافٍ" — الإخفاقات تُرفَّع ×3 في التدريب لأنها أغلى درس.
4. **بوابة جودة** (`scripts/audit_mentor_lab.py`): كل بناء يُشغَّل فعليًا
   (demo/help/استيراد) ولا يدخل التدريب إلا ما يعمل — 14 بناءً تحقق،
   وبناءان فاسدان جُرِّحا.
5. **التحويل** (`scripts/build_aali_sft_v2.py`): يدمج كل المصادر في
   `sft_v2` — **6,108 سجلات، ~34% عربي** (المزيج القديم كان 1.4%
   عربي)، مع إزالة تكرار وبوابة "لا تسريب امتحان" أثبتت نفسها (أسقطت
   4 حلقات مولَّدة كانت ستُسرب أسئلة الامتحان).

## المعلم والمقارنة — Soup + امتحان موحد

- **Soup** (`soup.yaml`): نموذج معلم محلي Qwen2.5-1.5B-Instruct في
  بيئة منفصلة (`D:\hwk-tools\soup-venv`) — يخدم عبر OpenAI-compatible
  API ويُدرَّب بـ QLoRA (docs/soup_setup.md).
- **الامتحان** (`data/exam_tool_calling.jsonl`): **26 حالة** أدوات +
  أمان + ذاكرة + تحقق (عربي/إنجليزي). نفس المُصحِّح للجميع
  (`scripts/soup_exam.py`) فالنتائج قابلة للمقارنة مباشرة.
- **السلسلة التلقائية** (`scripts/soup_pipeline.py`، عبر Task
  Scheduler باسم `HWK SoupPipeline`): ينتظر تحرر الـ GPU (يرفض المس
  بأي تدريب جارٍ) ثم: يخدم المعلم → امتحان أساس → إعادة SFT على
  sft_v2 → امتحان بعد الضبط → **حكم الترقية**: لا ترقية إلا إذا تفوق
  النموذج المضبوط على المعلم صراحةً. التقرير في
  `D:\hwk-data\soup\reports\resft_pipeline_report.md`
  (docs/post_phaseA_pipeline.md).

## التشغيل

```bash
# البيئة
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv/Scripts/python check_env.py

# تشغيل الوكيل (محادثة + أدوات داخل مجلد العمل)
scripts\start_app.bat        # ثم افتح http://127.0.0.1:5000
# أو CLI تفاعلي مع اقتراحات مرقمة:
.venv/Scripts/python aali_cli.py

# اختبار سريع
PYTHONIOENCODING=utf-8 PYTHONPATH=file-agent .venv/Scripts/python test_agent.py
PYTHONIOENCODING=utf-8 PYTHONPATH=file-agent .venv/Scripts/python -m pytest tests/test_hwk.py -q
```

## التدريب من الصفر (المرحلة أ)

```bash
.venv/Scripts/python train_scratch.py \
  --data D:/hwk-data/tokens \
  --corpora pile,arabic \
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model \
  --output-dir D:/hwk-models/scratch \
  --mirror-dir X:/hwk-backups/scratch \
  --context 1024 --d-model 768 --heads 12 --layers 12 \
  --batch-size 4 --gradient-accumulation 8 \
  --max-steps 90000 --save-steps 2500 --log-steps 25
# استئناف: --resume  (أو scripts/resume_training.bat)
```

أتمتة الحراسة:

| الأداة | الوظيفة |
|---|---|
| `scripts/checkpoint_watchdog.py` | ينسخ ويتحقق من كل نقطة حفظ فور صدورها (نافذة متحركة) |
| `scripts/night_caretaker.py` | مناوب ليلي: يراقب التدريب، يشفي المختبر، يوصل السلسلة عند تحرر الـ GPU، ويكتب تقرير الصباح `D:\hwk-data\MORNING_REPORT.md` |
| `scripts/soup_pipeline.py` + Task Scheduler | السلسلة التلقائية أعلاه |
| `scripts/after_phaseA.bat` | تمديد السياق 1024→4096 (bf16، على نسخة، بعد انتهاء المرحلة أ) |
| `scripts/fetch_arabic_corpus.py` | متون عربية موثقة: Wikisource dump + مراجعات فنادق HARD → حلقات SFT |

## بيانات وتوثيق

- المتون: `D:\hwk-data\raw` → شرائح `D:\hwk-data\tokens` (pile 18.8B+
  رمز؛ العربية عبر `download_corpora.py` و`fetch_arabic_corpus.py`).
- توثيق مفصّل في `docs/`: `long_term_memory.md`، `ai_security_lessons.md`
  (بمصادر الحوادث)، `mentor_learning_loop.md`، `soup_setup.md`،
  `post_phaseA_pipeline.md`، `omniroute_setup.md`، `training.md`،
  `image_generation.md`، و`freebuff_recovery_report_2026-09-06.md`
  (تشخيص أعطال التدريب السابقة وإصلاحها).
- إدارة الذاكرة يدويًا: `scripts/memory_review.py` (عرض/بحث/تعديل/حذف).

## توقعات صادقة

نموذج ~125M مدرَّب من الصفر على هذا الجهاز سيتقن أساسيات المحادثة
والأدوات، ويقدم إجابات أولية في البرمجة/الطب/القانون. لن يكون بديلًا عن
طبيب أو محامٍ أو نموذجًا ضخمًا مثل Claude/DeepSeek — لكن **الإطار**
(أدوات + ذاكرة دائمة + أمان + تحقق ذاتي + حلقة تعلم + امتحان ترقية)
مبني بحيث يتحسن أداؤه مع كل مرحلة تدريب، وكل قدرة في هذا الملف موجودة
في الكود الآن وليست خططًا.
