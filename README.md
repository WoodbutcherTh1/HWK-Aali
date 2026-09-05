# مشروع آلي (HWK-Aali)

مساعد ذكاء اصطناعي **محلي** يُبنى من الصفر (أوزان عشوائية، بدون نموذج
مدرَّب مسبقًا، بدون API key) على جهاز RTX 3070، ثنائي اللغة (عربية +
إنجليزية)، بهدف: محادثة طويلة، بناء وتعديل المشاريع البرمجية داخل مجلد
العمل، إدارة ملفات، ومعلومات طبية وقانونية أولية.

> **الاتجاه الوحيد المدعوم هو التدريب من الصفر.** سكربتات Qwen /
> Transformers القديمة أُزيلت إلى `legacy/` للرجوع إليها فقط.

## المزايا

- **ذاكرة محادثة طويلة**: حتى 100 دور لكل جلسة، تُحفظ 30 يومًا (مقارنة
  بـ20 دورًا / 48 ساعة سابقًا).
- **نافذة سياق قابلة للتمديد**: الترميز الموضعي RoPE بدل المواضع المكتسبة،
  والتدريب على سياق 1024 حاليًا مع إمكانية رفعه لاحقًا دون تغيير البنية.
- **أدوات بناء تطبيقات (بأسلوب Cursor)**: قراءة/كتابة/استبدال/بحث في
  الملفات + أمر `search_files` (بحث regex مع رقم السطر) + أمر `run_command`
  (قائمة مسموحات: python/pip/pytest/node/npm/git للقراءة والمترجمات) يعمل
  داخل مجلد المشروع فقط، وكل أمر يُسجَّل. إيقاف التنفيذ كليًا:
  `HWK_ALLOW_COMMANDS=0`.
- **التعديل على جهازك**: يشغّل التطبيق على أي مجلد تختاره (الافتراضي
  `D:\hwk-projects` عبر `scripts/start_app.bat`)؛ كل عمليات الملفات
  محصورة داخل هذا المجلد.
- **سحابي اختياري لاحقًا**: الوضع السحابي (OpenRouter) جاهز في
  `file-agent/agent_loop.py` إن أضفت مفتاحًا يومًا ما — لا شيء يتطلب ذلك الآن.

## ترتيب القرص

| المسار | المحتوى |
|---|---|
| `D:\hwk-data\raw` | التنزيلات الخام (pile، عربية، كود، قانوني، طبي، codeinstruct) |
| `D:\hwk-data\tokens` | شرائح الرموز (uint16) بعد الترميز — مجلد لكل متن |
| `D:\hwk-data\tokenizer` | نموذج BPE المدرب من الصفر (32k) |
| `D:\hwk-models` | نقاط الحفظ أثناء التدريب |
| `X:\hwk-backups` | نسخ احتياطي لنقاط الحفظ |
| `D:\hwk-projects` | مجلد عمل التطبيق (المشاريع التي يبنيها/يعدّلها) |

تُضبط المسارات في `hwk_paths.py` ويمكن تغييرها بمتغيرات بيئة.

## التثبيت

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv/Scripts/python check_env.py
```

## سير العمل الكامل (من الصفر)

1. **توليد بيانات التعليمات** (ألف+ مثال: محادثة، أدوات ملفات، تأكيدات،
   أخطاء، أسئلة برمجة/طبية/قانونية):

   ```bash
   .venv/Scripts/python create_training_data.py
   ```

2. **تنزيل المتون** (خلفية، قابلة للاستئناف — الموجة الأولى والثانية):

   ```bash
   .venv/Scripts/python download_corpora.py
   ```

3. **تدريب الترميز BPE** على عينة من المتون:

   ```bash
   .venv/Scripts/python train_bpe.py
   ```

4. **ترميز المتون إلى شرائح** — لكل متن مجلد داخل `tokens`:

   ```bash
   .venv/Scripts/python tokenize_corpus.py --corpus pile --corpus arabic --tokenizer D:/hwk-data/tokenizer/hwk_spm.model
   ```

5. **التدريب من الصفر** — المرحلة أ (عامة: إنجليزي + عربي):

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
   ```

   - الملاءمة الفعلية ~32k رمزًا/خطوة عند ~23k رمزًا/ثانية، أي نحو
     2–3 مليارات رمز خلال يوم–يومين.
   - استئناف بعد إيقاف: أضف `--resume` (أو استخدم `scripts/resume_training.bat`).
   - نسخ احتياطي تلقائي إلى `X:\hwk-backups\scratch`.
   - `--corpora` تختار المتنات (مثل `pile,arabic` للمرحلة أ ثم
     `code,law,medical,codeinstruct` لاحقًا)؛ وتُدمج شرائح التقييم تلقائيًا.

6. **المرحلتان ب و ج** (بعد اكتمال أ): تدريب استمراري على متون التخصص
   (كود/قانون/طبي + CodeAlpaca) في مجلد مخرجات جديد، ثم ضبط تعليمي (SFT)
   على `data/agent_instructions.jsonl` — نفس السكربت بوضع jsonl.

7. **التقييم**:

   ```bash
   .venv/Scripts/python evaluate_scratch.py --checkpoint D:/hwk-models/scratch/final.pt
   .venv/Scripts/python probe_model.py --checkpoint D:/hwk-models/scratch/final.pt
   ```

8. **تشغيل التطبيق** (محادثة + بناء مشاريع + أوامر داخل المجلد):

   ```bash
   scripts\start_app.bat
   ```

   ثم افتح `http://127.0.0.1:5000`. التطبيق يستخدم مجلد
   `D:\hwk-projects` ويستطيع: قراءة الملفات وكتابتها والبحث فيها، وتشغيل
   أوامر البناء/الاختبار المسموحة هناك.

## المعلم — التعلم من Claude وDeepSeek

أدوات تفتح نموذجًا آخر على جهازك وتسأله، ثم تحفظ الإجابات أمثلة تدريبية
(تُحوَّل لاحقًا إلى بيانات SFT للنموذج المحلي):

```bash
# Claude Desktop (مثبَّت على الجهاز — افتحه وسجّل الدخول مرة واحدة)
.venv/Scripts/python claude_teach.py "سؤالك هنا"

# DeepSeek عبر نافذة ويب مدمجة (chat.deepseek.com) — بدون تطبيق سطح مكتب رسمي
.venv/Scripts/python deepseek_teacher.py "سؤالك هنا"

# أسئلة متعددة من ملف، ثم تحويل كل التسجيلات إلى بيانات تعليمية
.venv/Scripts/python teacher_to_sft.py
```

تُحفظ الردود في `D:\hwk-data\teacher\` وتُدمج في
`data\teacher_sft.jsonl`. ملاحظات صادقة: أتمتة واجهات الطرف الثالث قد تنكسر
عند تحديث التطبيقات؛ وDeepSeek المجهول له حد يومي للرسائل؛ والإجابات لأغراض
الدراسة الشخصية.

## اختبار سريع

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=file-agent .venv/Scripts/python test_agent.py
```

بدون نقطة حفظ يشتغل الوضع المحلي المحدد (fallback) بلا مفتاح.

## أحدث الإضافات (Latest additions)

- **قراءة الصور**: أداة الوكيل `read_image` تستخرج نص الصور عبر OCR ويندوز
  المدمج (صور الشاشات والمستندات واللافتات).
- **توليد وتعديل الصور**: `scripts\run_image_tools.bat gen "وصف" --out ...`
  و `edit "تعليمات" --image in.png --out out.png` — Stable Diffusion في بيئة
  منفصلة (`D:\hwk-tools\sd-venv`) بدون المساس ببيئة التدريب. النتائج في
  `D:\hwk-projects\images\`.
- **بيانات التاريخ/الجغرافيا/الطبيعة**: مجاميع `know` (ويكيبيديا الإنجليزية
  البسيطة) و `mmlu` (أسئلة تغطي التاريخ والجغرافيا والعلوم والقانون والطب +
  مجموعة تقييم مستقبلية).
- **n8n**: مثبّت محليًا (`D:\hwk-tools`). واجهة المحرر على
  http://localhost:5678 — فعّل سير العمل "HWK Aali - Local Agent Ask"
  بضغطة واحدة بعد إنشاء حساب المالك، ثم
  `POST /webhook/<workflow-id>/webhook/hwk-ask` بـ `{"message": ...}`.
  (البديل المباشر بدون n8n: `POST http://127.0.0.1:5055/api/ask`).
- **اختبار قدرات الحاسوب**: `scripts/pc_tour_test.py` يشغّل كل أدوات الملفات
  الحقيقية داخل المساحة الآمنة + فحص قراءة عبر C: و D: و X:، والتقرير في
  `D:\hwk-data\pc_tour_report.txt`.
- **معلم Arena (تجريبي)**: `arena_teach.py` لسؤال نماذج عبر arena.ai — فشلت
  أتمتة الالتقاط لأن المحادثة لا تُفتح تلقائيًا؛ الأفضل استخدام
  `claude_teach.py` و `deepseek_teacher.py`.

## توقعات صادقة

نموذج ~125M مدرب من الصفر لأيام على هذا الجهاز سيتقن أساسيات المحادثة
والأدوات، ويقدم إجابات أولية في البرمجة/الطب/القانون. لن يكون بديلًا عن
طبيب أو محامٍ أو نموذج ضخم مثل Claude/Deepseek — وهذه الميزات تُبنى
**كإطار برمجي** (أدوات، ذاكرة، بحث) يتحسن جودته مع نمو النموذج نفسه.
انظر `FREEBUFF_HANDOFF.md` للخلفية الكاملة.
