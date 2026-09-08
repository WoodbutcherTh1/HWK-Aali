# تدريب HWK-Aali — دليل مرجعي

هذا الملف يلخّص كل ما يخص تدريب النموذج من الصفر: حالة البيانات، كيفية إطلاق التدريب الحقيقي (Phase A) على جهازك، كيفية المراقبة، والبديل السريع عبر Google Colab.

## 1. حالة البيانات الآن

- المتون (corpora) تم تنزيلها ورمزها (tokenize) بالفعل: حوالي **139GB** من الـ shards المرمّزة (uint16 `.bin`) في `D:/hwk-data/tokens`، منظّمة كمجلدات فرعية لكل corpus (منها `pile` و`arabic` كأساس الحالي).
- المرمّز (tokenizer): `D:/hwk-data/tokenizer/hwk_spm.model` (SentencePiece/BPE) — مبني مسبقاً عبر `scripts/tokenize.bat`.
- **لم يبدأ تدريب فعلي بعد** على هذه البيانات — يوجد فقط checkpoint تجريبي صغير (~491KB) من اختبار سابق (smoke test)، وليس نموذجاً مدرَّباً.

إذا أردت إضافة corpora جديدة، راجع `download_corpora.py` (تمت إضافة `instruct` و`arabic_instruct` و`math` و`orca_math` و`reasoning` مؤخراً) ثم أعد `scripts/tokenize.bat`.

بالإضافة، `download_github_corpora.py` (جديد) يسحب 3 مجموعات تعليمات مفتوحة الترخيص من GitHub مباشرة (لا تحتاج huggingface_hub ولا مفاتيح API): `alpaca` (52K), `code_alpaca` (20K تعليمات برمجة), `alpaca_gpt4` (52K، أعلى جودة). شغّلها بـ:

```
.venv\Scripts\python download_github_corpora.py
.venv\Scripts\python tokenize_corpus.py --tokenizer D:/hwk-data/tokenizer/hwk_spm.model --corpus alpaca --corpus code_alpaca --corpus alpaca_gpt4
```

وأيضاً `generate_tool_sft.py` يولّد `data/tool_calling_sft.jsonl`: أمثلة تعليمية تُعلّم آلي استخدام الأدوات الفعلية (write_file, read_file, run_command, web_search...) بصيغة JSON بالضبط كما يتوقعها `agent_loop.py`.

## 2. إطلاق تدريب Phase A الحقيقي (على بطاقة الشاشة المحلية)

هذا يتطلب جهازك فعلياً (GPU + PyTorch) — أنا (آلي عبر الجسر السحابي) لا أملك GPU ولا PyTorch في بيئتي، فلا أستطيع تشغيله من هنا. الأمر جاهز ومُعدّ مسبقاً:

```
scripts\resume_training.bat
```

هذا السكربت يشغّل:

```
".venv\Scripts\python.exe" train_scratch.py ^
  --data D:/hwk-data/tokens ^
  --corpora pile,arabic ^
  --tokenizer D:/hwk-data/tokenizer/hwk_spm.model ^
  --output-dir D:/hwk-models/scratch ^
  --mirror-dir X:/hwk-backups/scratch ^
  --context 1024 --d-model 768 --heads 12 --layers 12 ^
  --batch-size 4 --gradient-accumulation 8 ^
  --max-steps 90000 --save-steps 2500 --log-steps 25 ^
  --warmup-steps 500 --dtype fp16 ^
  --resume > D:\hwk-data\training.log 2>&1
```

نقاط مهمة:
- `--resume` يجعل السكربت آمناً للتشغيل المتكرر: إذا توقف التدريب (إغلاق الجهاز، انقطاع كهرباء) يكفي تشغيل نفس الأمر من جديد وسيكمل من آخر checkpoint في `D:/hwk-models/scratch`.
- كل حفظ (`--save-steps 2500`) يُنسخ تلقائياً إلى `X:/hwk-backups/scratch` (`--mirror-dir`) كنسخة احتياطية.
- تأكد أن لديك مساحة حرة كافية (حسب AGENTS.md: لا تبدأ تدريباً إن كانت المساحة الحرة أقل من 20%).
- بالإعدادات الحالية (context=1024, d-model=768, 12 layers, 90000 خطوة) هذا تدريب طويل قد يستغرق أياماً على بطاقة الشاشة المحلية — راقب `training.log` لتقدير المعدل الفعلي (tokens/sec) ثم احسب الوقت التقريبي.

## 3. مراقبة التدريب

```
scripts\watch_training.bat
```

يعرض آخر الأسطر من `D:\hwk-data\training.log` مباشرة (tail -f / Get-Content -Wait). كل سطر يحتوي: step, epoch, tokens, loss, eval_loss, perplexity, lr, tok_per_sec, elapsed_min.

أو فحص سريع للحالة العامة (GPU + مساحة الأقراص + آخر 5 أسطر):

```
scripts\status.bat
```

إذا كان الجهاز مرتبطاً بهذه الجلسة، يمكنني أنا أيضاً قراءة `D:\hwk-data\training.log` دورياً وتلخيص التقدم (loss، السرعة، الوقت المتبقي التقريبي) — فقط اطلب ذلك.

## 4. تجربة سريعة على Google Colab (اختياري، منفصل عن Phase A)

للتأكد أن الكود يعمل فعلياً على GPU حقيقي دون انتظار أيام، هناك دفتر Colab جاهز (`HWK_Aali_Colab_Quickstart.ipynb` + `hwk_colab_bundle.zip`) يُشغّل تدريب SFT صغير جداً (context=256, 6 layers, 400 خطوة فقط) على `data/agent_instructions.jsonl` باستخدام مرمّز أحرف بسيط (بدون الحاجة لملف tokenizer خارجي) — ينتهي خلال دقائق على T4 المجانية.

هذا **لا يُنتج نموذجاً نهائياً** ولا يمسّ بيانات Phase A أو checkpoints الحقيقية — هدفه فقط التحقق العملي من أن `train_scratch.py` وبنية `hwk_model` تعملان بشكل صحيح على GPU حقيقي، بمعزل عن أي مشاكل بيئة محلية.

الخطوات: افتح الدفتر في colab.research.google.com، فعّل `Runtime -> Change runtime type -> T4 GPU`، ثم شغّل الخلايا بالترتيب (ستُطلب منك رفع `hwk_colab_bundle.zip` في الخلية الثانية).

## 5. تمديد نافذة السياق (context) + SFT تلقائي بعد انتهاء Phase A

الموديل يستخدم RoPE (rotary position embeddings) مش جداول مواضع ثابتة (`file-agent/hwk_model/model.py`) — يعني طول نافذة السياق (context) خيار وقت التدريب، مش شي محفور بشكل الأوزان. هذا خلّاني أقدر أبني خط أنابيب (pipeline) كامل يشتغل تلقائياً بعد بعضه بدون ما تحتاج تراقبه:

```
Phase A (context=1024، شغالة حالياً)
  -> scripts\extend_context.bat   (تكملة تدريب بنفس الأوزان عند context=4096)
  -> scripts\sft_training.bat     (SFT لاستدعاء الأدوات + تعليمات عامة، فوق الموديل بـ 4096)
```

شغّل هذا بنافذة منفصلة عن `resume_training.bat` (لا يستهلك GPU إطلاقاً، فقط يراقب ملفات الـ log):

```
scripts\queue_sft.bat
```

بيراقب `training.log`، وبمجرد ما Phase A توصل لخطوتها المستهدفة (90000) أو تتوقف، بيشغّل تلقائياً `extend_context.bat` (ينسخ checkpoint فيز A لمجلد منفصل `D:\hwk-models\context-4k` ويكمل التدريب فيه عند context=4096 مع `--grad-checkpoint` لتوفير الذاكرة على الـ 8GB). لما هاي توصل لخطوتها (100000) أو تتوقف، بيشغّل تلقائياً `sft_training.bat` (ينسخ checkpoint الـ context-4k لمجلد `D:\hwk-models\sft-v1` ويعمل SFT عليه بـ `data/sft_mix.jsonl`، 12,084 سجل: 84 حلقة استدعاء أدوات + ~12,000 مثال تعليمات عامة، كلها بنفس بروتوكول `{"tool": ...}`).

كل مرحلة إلها مجلد output ولوق منفصل — لا شي بيلمس مخرجات Phase A الأصلية:

| المرحلة | context | output-dir | log |
|---|---|---|---|
| Phase A | 1024 | `D:\hwk-models\scratch` | `D:\hwk-data\training.log` |
| تمديد السياق | 4096 | `D:\hwk-models\context-4k` | `D:\hwk-data\context_training.log` |
| SFT | 4096 | `D:\hwk-models\sft-v1` | `D:\hwk-data\sft_training.log` |

ملاحظة صادقة: 4096 مش 800K. هذا رفع حقيقي (4x) عن الـ 1024 الحالية وممكن نزيده أكتر تدريجياً (8192 مثلاً) لاحقاً بنفس الطريقة، بس الأرقام الضخمة متل 800K محتاجة عتاد مختلف كلياً (راجع نقاش سابق حول حجم KV cache).

لإعادة بناء/تحديث `data/sft_mix.jsonl` لاحقاً، استخدم `scripts/prepare_sft_mix.py` (يقرأ `data/tool_calling_sft.jsonl` + بيانات alpaca الخام من `D:\hwk-data\raw`).

## 6. ما بعد التدريب (القديم)

## 6. ما بعد التدريب (القديم)

بعد أن ينتج Phase A أول checkpoint فعلي، الخطوات المنطقية التالية (Stage B / Stage C حسب README.md) هي: تقييم النموذج، ثم SFT على `data/agent_instructions.jsonl` وأي بيانات تعليمات إضافية، ثم دمجه في واجهة الوكيل (`file-agent/agent_loop.py`) كموفر محلي بديل عن Ollama إن رغبت.

## 7. تشغيل SFT فوراً (بدون انتظار Phase A) + امتحان الأدوات

إذا ما بدك تنتظر Phase A، شغّل:

```
scripts\sft_now.bat
```

بينسخ آخر checkpoint من Phase A (بأي خطوة كانت الآن) لمجلد منفصل `D:\hwk-models\sft-now`، ويسوي SFT عليه مباشرة بـ context=1024 (بدون تمديد سياق بعد) على `data/sft_mix.jsonl`. بيشتغل بالتوازي مع Phase A (نفس الكرت، فبطبيعة الحال الاثنين رح يصيروا أبطأ)، ومحفوظ بمجلد ولوق منفصلين — ما بيلمس Phase A ولا خط الأنابيب الآلي (`queue_sft.bat`) الشغال بالخلفية.

بعد ما يمشي كم مية خطوة (شوف `D:\hwk-data\sft_now_training.log`)، اختبر الموديل فعلياً بامتحان آلي:

```
.venv\Scripts\python.exe scripts\exam_tool_calling.py --checkpoint D:/hwk-models/sft-now/checkpoint.pt
```

الامتحان بـ `data/exam_tool_calling.jsonl` (12 حالة، صياغة مختلفة كلياً عن أمثلة التدريب — يعني يقيس التعميم مش الحفظ): طلبات صور، طلبات فيديو "بجودة إعلان" (لازم يرفض بصراحة ويعرض نسخة محلية بسيطة بدلها)، حالات تحكم (سؤال عادي بدون أداة، قراءة ملف). النتيجة تنكتب بـ `D:\hwk-data\exam_report.txt` (مقروء) و`exam_report.json` (تفصيلي).
