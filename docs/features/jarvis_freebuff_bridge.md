# الجسر: من جارفيس إلى فريفب · Jarvis → Freebuff bridge

> **TL;DR** — اكتب لجارفيس: `أرسل إلى فريفب: <الكلام>` (بالتربي أو بالإنجليزي أو
> **بصوتك**)، فيضع الكلام في صندوق يصل إليّ أنا. اسأل `/freebuffs` إن وصل.
>
> **TL;DR (EN)** — Tell Jarvis `send to freebuff: <message>` (Arabic, English,
> or **by voice**). It drops the message into an inbox I read. Ask
> `/freebuffs` whether it landed.

---

## 1. لماذا صندوق وليس "إرسال مباشر"؟ · Why an inbox, not a push

**لا يوجد أي واجهة برمجية تحقن رسالة داخل محادثة Freebuff حيّة.** هذا ليس
نقصاً في التنفيذ — لا توجد نقطة دخول واحدة، وأي محاولة لحقنها تعني اختراق
جلسة المستخدم. فالعنصر الصحيح هنا هو **صندوق بريد**:

```
تيليجرام ──▶ جارفيس (الراسبيري) ──▶ freebuff_inbox.jsonl ──▶ فريفب (الـPC)
                    │                       │
                    └─ تأكيد بالمعرّف        └─ يُقرأ عبر SSH
```

الجسر **لا يدّعي** أنه أرسل شيئاً لم يصل: كل رسالة تحصل على معرّف (`#a1b2c3d4`)
وطابع وقت، فيستطيع المالك أن يسأل «هل وصل فريفب؟» فيقرأ جارفيس **الصندوق
نفسه** لا ذاكرته.

**There is no API that can inject a message into a live Freebuff session** — not
a missing implementation, there is no such endpoint. So the honest shape of the
feature is a mailbox, and the reply carries a note id plus a timestamp so "did
you get it?" is answered by reading the inbox, not by recalling a promise.

---

## 2. كيف تستخدمه · How to use it

| اكتب هذا | النتيجة |
|---|---|
| `/freebuff شغّل الاختبارات` | أمر صريح |
| `send to freebuff شغّل الاختبارات` | العبارة الطبيعية (إنجليزي) |
| `أرسل إلى فريفب: شغّل الاختبارات` | بالعربي |
| `freebuff: شغّل الاختبارات` | الاسم في بداية الرسالة |
| **مقطع صوتي** يقل: «send to freebuff …» |Whisper يفرّغه ثم يمرّره نفس المعالج |
| `send to freebuff` (بدون كلام) | يفتح طلباً: **الرسالة التالية** هي المحتوى |
| `/freebuffs` | كم رسالة، وهل قُرئت |

**الكلمات المقبولة للاسم:** `freebuff` · `free buff` · `buffy` · `barney` ·
`فريفب` · `فريف اف` · `بوفي`. والأفعال: `send / forward / give / pass / tell /
show / hand` و `أرسل / ارسل / بعث / حول` مع لواحقها (`أرسلها ل فريفب`).

### ما لا يحدث · What does NOT happen

* **الكلام العادي لا يُختطف.** «شو يقدر فريفب يسوي؟» أو `show buffy's status`
  تذهب إلى **العقل** (Groq) كسؤال عادي. المطابقة تشترط **فعل إرسال صريح** +
  الاسم، أو الاسم في بداية الرسالة بعد نقطتين.
* **أي شخص غير المالك لا يكتب شيئاً.** البوابة `is_owner` قبل كل شيء؛ stranger
  يُسجَّل كـ `unauthorized` ويتجاهَل.
* **لا صوت في أمر عادي.** `/freebuff` يستجيب نصاً فقط، لأن الصوت يستغرق ~٣٠
  ثانية، ولا يستحق الأمرُ صوتياً.

---

## 3. الصندوق · The inbox

| | |
|---|---|
| **المسار** | `~/.aali/jarvis/freebuff_inbox.jsonl` (على الراسبيري) |
| **الصلاحية** | المجلد `700` والملف `600` |
| **السقف** | ٥٠٠ رسالة / ٢٥٦ كيلوبايت — الأحدث تُحفظ |
| **الشكل** | JSONL: `{id, ts, epoch, origin, text, secrets_masked, handled}` |
| **القراءة** | `python scripts/freebuff_inbox.py [--take] [--json]` |

`--take` يقرأ ويضع علامة `handled`، فتختفي من «غير المقروء». **هذا هو الإجراء
الوحيد الذي يُعدّ تسليماً**: لا شيء في النظام يدفع إشعاراً تلقائياً إلى هذه المحادثة،
فمن يقرأ هو من يؤكّد.

### الأسرار تُحذف قبل الكتابة · Secrets are stripped on the way in

المالك تسريب مفتاح Groq ومفتاح DeepSeek في محادثة سابقاً. الصندوق **لا يصبح
المكان الذي يُخزَّن فيه المفتاح التالي**: أي نصّ يشبه مفتاحاً
(`gsk_…`, `sk-…`, `ghp_…`, توكن تيليجرام, `api_key = …`, hex طويل) يُستبدل
بـ «مفتاح» **قبل** الكتابة، والمالك يُخبر أن شيئاً حُذف. النص العادي الذي يشبه
المفتاح («the deployment key is the green one») يمرّ بلا مساس.

### السجل يبقى بلا محتوى · The log stays content-free

`jarvis.log` يسجّل `{"event":"handoff_sent","ok":true,"chars":37,"count":0}` —
**حجم وحدث فقط**. الصندوق هو المكان الوحيد في جارفيس الذي يحتفظ بنص رسالة،
وهذا سبب حصره بملف ٦٠٠ داخل مجلد ٧٠٠.

---

## 4. من يقرأ الصندوق · Who reads it

```bash
# من الـPC (هنا) — يفتح الراسبيري عبر مفتاح Pi-CI الموجود
python scripts/freebuff_inbox.py            # ماذا ينتظرني
python scripts/freebuff_inbox.py --take     # اقرأ وسمّها كمسلَّمة
python scripts/freebuff_inbox.py --send "…" # اختبار الجسر
```

المسارات **مكتشفة لا مكتوبة**: `AALI_PI_SSH_KEY` أولاً، ثم مفتاح Pi-CI تحت
`hwk_paths.data_root()`، و`AALI_PI_HOST` / `AALI_PI_USER` للجهاز نفسه.

---

## 5. حدود صريحة · Honest limits

* **لا يوجد دفع تلقائي.** إن لم أقرأ الصندوق، تبقى الرسالة فيه. هذه هي
  طبيعة صندوق بريد، والدفع الفوري يتطلب API لا يوجد.
* **الرسالة نص فقط.** صورة أو ملف يُحلَّل أولاً بـ Whisper/OCR ثم النص وحده
  يُرسل — الملفات لا تُنقل.
* **المعرّف هو الضمان الوحيد.** لا أؤكد استلاماً قبل `--take`.
* **٤٠٠٠ حرف كحد أقصى** للرسالة الواحدة، والاسم يُقصد به **رسالة** لا ملف.

---

## 6. الملفات · Files

* `scripts/jarvis/handoff.py` — الصندوق (كتابة، سقف، حذف الأسرار، قراءة/تسليم، CLI)
* `scripts/jarvis/commands.py` — `cmd_freebuff` · `cmd_freebuff_status` · المطابقة
* `scripts/freebuff_inbox.py` — قارئ الـPC عبر SSH
* `tests/test_jarvis.py` — ٤٤ اختباراً: كل صياغة للعبارة، كل شكل مفتاح، السقف،
  الصلاحيات، عدم اختطاف الكلام العادي، المسار الصوتي

---

## الاتجاهان / Two-way (2026-10-04)

الصندوق صار ذا اتجاهين: `kind: note` (أنت ← فريفب) و`kind: answer` (فريفب ← أنت).

```bash
python scripts/freebuff_inbox.py --reply-to <id> --text "وصلني، جار العمل"   # رد
python scripts/freebuff_inbox.py --answers                                 # ما لم يُقرأ
```

و جارفيس يوصل الردود إلى تيليجرام تلقائياً كل ٦٠ ثانية (`/freebuffread` للفوري).
يعدّ الرد «مقروءاً» بعد نجاح الإرسال فقط، ولا يعيد إليك رسائلك أنت.

انظر أيضاً: `docs/features/jarvis_natural_language.md` — كيف يفهم جارفيس الكلام
العادي بلا أوامر.
