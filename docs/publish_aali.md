# نشر آلي للعالم — منصات النشر

*للمالك: Hmam Kaadna — أُضيف 2026-09-09 بناءً على طلبه: "أريد رفع نموذج آلي
إلى Hugging Face وأمثالها ليجرب المستخدمون آلي كأي نموذج آخر".*

الأداة الرسمية: **`scripts\aali_deploy.bat`** (انقر مرتين — قائمة عربية).
تحتها كل شيء `scripts/publish_aali.py`. لا يُرفع ولا يُدفع أي شيء دون "y" منك.

## 0) الفحص (preflight)

```bat
scripts\publish_aali.bat 1        :: أو:
.venv\Scripts\python scripts\publish_aali.py --check
```

يتحقق: النموذج الأساسي، وجود محوّل مُدرَّب (من pipeline الترقية)، تذكرة HF،
وحدة المستودع. كل بند `[!!]` يجب حلّه قبل النشر.

## 1) التدريب والترقية (شرط النشر)

النشر يتم **فقط** لمحول تجاوز امتحان بوابة الترقية (24 حالة: ذاكرة + أمان +
مضاد هلوسة) — نفس بوابة استخدام آلي داخلياً. شغّلها من زر النشر [2] أو:

```bat
.venv\Scripts\python scripts\soup_pipeline.py
```

النتيجة: `D:\hwk-data\soup\resft_pipeline_report.md` و `promoted.json`.

## 2) الدمج — نموذج مستقل قابل للنشر

```bat
.venv\Scripts\python scripts\publish_aali.py --merge
```

يدمج LoRA في النموذج الأساسي (CPU، دقائق) → `D:\hwk-models\aali-merged`
مع **بطاقة نموذج** تنسب النموذج لصاحبه Hmam Kaadna تلقائياً.

## 3) Hugging Face

```bat
huggingface-cli login             :: مرة واحدة؛ التذكرة تبقى على جهازك
.venv\Scripts\python scripts\publish_aali.py --hf HmamK/aali-1.5b
```

- الرفع **خاص (private) أولاً دائماً** — تصير عاماً بقرارك من الموقع.
- البطاقة: عربي/إنجليزي، الترخيص (apache-2.0 افتراضياً)، نسبة كاملة للمالك.

## 4) Ollama (تشغيل محلي عندك أو عند المستخدم)

1. حوّل النموذج المدمج إلى GGUF (أداة llama.cpp، خطوة يدوية موثقة):

```bat
pip install llama-cpp-python  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl
python llama.cpp\convert_hf_to_gguf.py D:\hwk-models\aali-merged --outfile aali-q4_k_m.gguf --outtype q4_k_m
```

2. `scripts\publish_aali.py --ollama aali` يكتب `Modelfile` جاهزاً بجانب
   النموذج، ثم:

```bat
ollama create aali -f D:\hwk-models\aali-merged\Modelfile
ollama run aali
```

## 5) قائمة النشر لمنصات النماذج

```bat
.venv\Scripts\python scripts\publish_aali.py --openrouter
```

يكتب `D:\hwk-data\soup\openrouter_checklist.md`: خطوات تقديم النموذج،
والتحقق الحي من نقطة `/v1` القياسية (خادم آلي يدعمها أصلًا على
`/v1/chat/completions`). بيانات الحساب والأرباح تُدخلها أنت بنفسك.

## الحدود الصادقة

- نموذج 1.5B: جيد للتجارب والمجتمع الصغير، ليس منافساً للنماذج الضخمة.
- التذاكر والمفاتيح لا تمر أبداً عبر المحادثة أو الأدوات — تدخلها بنفسك.
- النشر **لك** وحدك: كل مسار يتوقف عند سؤال y/N قبل أي أثر خارجي.
