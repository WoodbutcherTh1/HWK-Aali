# مشروع آلي (HWK)

مشروع تجريبي لبناء مساعد ذكاء اصطناعي محلي يستطيع فهم طلبات الملفات
وتنفيذها بأمان، مع مسار اختياري لنموذج سحابي. المشروع مناسب للتطوير على
Replit ثم نقله لاحقًا إلى جهاز مزود بـ RTX 3070.

## ما يعمل الآن

- أدوات آمنة لقراءة وكتابة وتعديل ونقل وحذف الملفات داخل مجلد عمل محدد.
- وضع محلي بلا API key للأوامر الواضحة، ويعمل حتى قبل تنزيل نموذج.
- دعم اختياري لـ Replit AI المُدار أو OpenRouter في `agent_loop.py`.
- واجهة Flask عربية متوافقة مع الهاتف في `file-agent/app.py`.
- سجل JSONL كامل في `file-agent/logs/agent.log`.
- تنزيل وتنظيف أول 10 أجزاء من The Pile بصيغة Parquet.
- سكربت تدريب وتقييم لنموذج Transformers صغير.

## التشغيل السريع على Replit

```bash
uv sync
python file-agent/app.py
```

افتح تبويب **Preview**. اختر **نموذج محلي / وضع بلا مفتاح**، ثم جرّب:

```text
أنشئ ملف test.txt واكتب بداخله مرحباً
```

```text
اقرأ test.txt
```

```text
احذف الملف test.txt
```

لتشغيل اختبار الطرفية:

```bash
PYTHONPATH=file-agent python test_agent.py
```

## تجهيز البيانات

لا يستخدم `prepare_data.py` دالة `load_dataset("EleutherAI/pile")` القديمة.
ينزل ملفات Parquet مباشرة من Hugging Face ويحفظ النسخ الخام مؤقتًا داخل
`processed_data/raw/`.

```bash
uv run python prepare_data.py --parts 10
```

لتحميل الأجزاء الثلاثين، استخدم الأمر صراحةً لأن البيانات كبيرة:

```bash
uv run python prepare_data.py --all
```

الناتج:

- `processed_data/cleaned_pile.parquet`
- `cleaned_pile.parquet`

## التدريب والتقييم

الفكرة الافتراضية تبدأ بـ `distilgpt2` لأنه صغير نسبيًا. على RTX 3070 يمكن
تجربة:

```bash
uv run python train.py \
  --data processed_data/cleaned_pile.parquet \
  --model-name distilgpt2 \
  --gradient-accumulation 8 \
  --save-steps 500
```

للتدريب بتكميم 4-bit وLoRA:

```bash
uv run python train.py --use-4bit
```

لا تشغّل التدريب قبل تجهيز البيانات والتأكد من CUDA:

```bash
uv run python check_env.py
```

بعد التدريب:

```bash
uv run python evaluate.py --model model --data processed_data/cleaned_pile.parquet
```

يُحفظ سجل الخسارة في `training_log.csv` والنموذج في `model/`.

## ربط النموذج المحلي بالوكيل

عندما يوجد نموذج مدرّب داخل `model/`، يحاول الوضع المحلي استخدام
`transformers.pipeline`. إذا كان المجلد غير موجود أو كانت مكتبات التدريب
غير مثبتة، يعود الوكيل تلقائيًا إلى الوضع المحلي المحدد للأوامر بدل عرض خطأ
غامض أو طلب مفتاح.

يمكن تغيير المسار:

```bash
LOCAL_MODEL_PATH=model/agent python file-agent/app.py
```

## تدريب وكيل الملفات بالعربية

تدريب The Pile العام لا يكفي لتعليم استدعاء الأدوات، لذلك يوجد مسار منفصل
لبيانات تعليمات عربية قصيرة:

```bash
uv run python create_training_data.py
```

ينتج الأمر `data/agent_instructions.jsonl`، وتحتوي كل عينة على رسالة عربية
واستدعاء JSON مطابق لأسماء ومعاملات الأدوات الموجودة فعليًا.

للتدريب على نموذج صغير مناسب كبداية:

```bash
uv run python train_agent.py \
  --data data/agent_instructions.jsonl \
  --model-name Qwen/Qwen2.5-0.5B-Instruct \
  --output-dir model/agent \
  --epochs 3
```

على RTX 3070 يمكن إضافة `--use-4bit` لتقليل استهلاك الذاكرة. هذه العينة
التوضيحية صغيرة وليست كافية لإنتاج نموذج نهائي؛ زِد عدد أمثلة الاستخدام
والأخطاء والتأكيدات قبل الاعتماد الإنتاجي.

## سجل العمليات

كل طلب يسجل رقم تتبع واحدًا في:

```text
file-agent/logs/agent.log
```

الفحص:

```bash
tail -f file-agent/logs/agent.log
```

السجل يوثق الطلب، مزود الذكاء المستخدم، الأداة المطلوبة، المدخلات، النتيجة،
الرد الظاهر للمستخدم، والأخطاء. لا يسجل مفاتيح API.

## النقل إلى Windows أو macOS

1. ثبّت Python 3.10 أو أحدث.
2. أنشئ بيئة افتراضية:

   ```bash
   python -m venv .venv
   ```

3. فعّلها:

   ```bash
   # macOS/Linux
   source .venv/bin/activate

   # Windows PowerShell
   .venv\Scripts\Activate.ps1
   ```

4. ثبّت الاعتماديات:

   ```bash
   python -m pip install -r requirements.txt
   ```

على جهاز RTX، ثبّت إصدار PyTorch المتوافق مع إصدار CUDA الموجود لديك من
صفحة PyTorch الرسمية قبل تشغيل التدريب. لا تستخدم `--break-system-packages`
كحل دائم؛ البيئة الافتراضية أو `uv` أكثر أمانًا.

## تصدير نسخة احتياطية

```bash
chmod +x export_project.sh
./export_project.sh
```

ينتج الملف `project_backup.tar.gz` مع استثناء البيئات الافتراضية وملفات
`__pycache__` و`.git`.