# آلي CLI — التحديث الذاتي | Aali CLI self-update

**العربية أولاً.** Terminology is identical across the three clients: Node,
Studio, Desktop and the CLI all run the SAME verification code
(`scripts/shared/updater.py`), so there is one security story to audit, not four.

---

## 1) بالعربية

### ما الذي يفعله `/update`

آلي CLI **يحدّث نفسه** من مركز التحديثات (Aali Hub) بنفس عقد الأمان الذي
تستخدمه آلي ستوديو وآلي Desktop وآلي Node:

- **لا يثق بالمركز**: توقيع الـ manifest يُعاد التحقق منه **على جهازك**
  (Ed25519) قبل أي شيء.
- **لا تنفيذ تلقائي**: لا يُشغَّل أي ملف منزَّل إطلاقاً. الملف الوحيد الذي يُشغَّله
  المحدِّث هو نسخة آلي نفسها بعد التبديل.
- **zip-slip / tar-slip مرفوض**: أي مسار يخرج من مجلد الوجهة يُرفض قبل الاستخراج.
- **النسخة السابقة محفوظة**: إذا فشل الإقلاع **مرتين** بعد التحديث، يعود
  التطبيق تلقائياً إلى الإصدار السابق.
- **HTTPS فقط** (عدا `localhost` للتطوير).
- **سجل خالٍ من المحتوى**: `<data root>/updates/update.log` — أسماء أحداث
  وأرقام إصدارات فقط، لا روابط ولا توكِّنات ولا نص.

### الأوامر

| الأمر | ما يفعله |
|---|---|
| `/update` | الحالة كاملة: الإصدار، هل يمكنه التحديث الذاتي، مفتاح التحقق، مسار السجل، مسار الإعداد |
| `/update check` | يسأل المركز الآن (يتجاوز كاش الست ساعات) |
| `/update apply` | تنزيل + تحقّق + تجهيز + تفعيل، **ثم يسألك** قبل إعادة التشغيل |
| `/update version` | الإصدار الحالي + أحدث إصدار منشور |
| `/update auto on\|off` | الفحص التلقائي عند التشغيل (مفعّل افتراضياً) |
| `/update channel stable\|beta` | القناة |
| `/update rollback` | استعادة الإصدار السابق فوراً |

### رسالة عند الإطلاق

عند وجود إصدار جديد (والفحص التلقائي مفعّل) يظهر بعد الافتتاح:

```
🎉 إصدار جديد v1.1.0 متوفر. اكتب /update apply للتحديث.
```

الفحص يجري في **خلفية** لا يعطّل الشعار ولا انتظار: مركز غير متاح لا
يمنعك من الكتابة.

### الإعداد

`~/.aali/cli_update.json` (على ويندوز: `%USERPROFILE%\.aali\cli_update.json`)

```json
{
  "auto_check": true,
  "channel": "stable",
  "hub_url": "aali.dpdns.org",
  "lang": "ar"
}
```

بديلٌ لها متغيّر `AALI_CLI_UPDATE_CONFIG`، ومجلد التحديثات في نسخة مبنية
يُحدَّد بـ `AALI_CLI_UPDATE_DIR` (اختبارات/بيئة تطوير).

### ثنائية اللغة

كل رسالة لها توأم إنجليزي في `MSG`، و`lang` يقرّر أيّهما يُطبع:

- `ar` (افتراضي): عربي فقط،
- `en`: إنجليزي فقط،
- `both`: العربي ثم الإنجليزي (الإنجليزي باهت).

`/update version` يحترم هذا الاختيار حرفياً. أمر `/update` بلا وسيط يطبع
الحالة كاملة، مفيدة حين يقرأها زميل لا عربي.

### التنفيذ

```
/update apply
  ↓ نزّل إلى  <install>/updates/downloads/
  ↓ تحقق: SHA256 ثم توقيع Ed25519 على الـ sha256 (ينفشل كله أو لا شيء)
  ↓ استخرج إلى <install>/updates/staged/1.1.0/  (مع فحص zip-slip لكل عضو)
  ↓ بدّل:  current/  ←  previous/1.0.0/  ،  staged/1.1.0/ ← current/
  ↓ اكتب update_state.json: {pending_ack: true, failures: 0}
  ↓ اسأل: «إعادة التشغيل الآن؟»
  ↓ عند الإقلاع: register_startup() يعدّ الإقلاع غير المؤكَّد،
     وعند 2 يعود إلى previous/ تلقائياً
```

### مفاتيح إيقاف طارئ

| المتغيّر | الأثر |
|---|---|
| `AALI_CLI_UPDATE_OFF=1` | تعطيل كل التحديثات |
| `AALI_CLI_UPDATE_NO_EXIT=1` | لا `os._exit` بعد التفعيل (الاختبارات والأتمتة) |
| `AALI_CLI_UPDATE_PUBKEY` | مفتاح Ed25519 **العام** (64 hex) الذي تُبنى به الحزمة |
| `AALI_CLI_UPDATE_DIR` | مجلد التحديثات (نسخة من المصدر / اختبارات) |
| `AALI_UPDATE_TOKEN` | توكن للمركز الخاص (المركز العام لا يحتاج) |

**بلا مفتاح عام = بلا تحديثات.** نسخة لا تحمل المفتاح **ترفض** كل نسخة جديدة
وتقول ذلك بصراحة، لأن عميلاً يجلب مفتاحه من المركز يثق بأيّ شيء يقدّمه
المركز.

### أخطاء شائعة

| ما تراه | السبب الحقيقي | الحل |
|---|---|---|
| `نسخة المصدر لا تُحدّث نفسها` | تشغّل `python scripts/aali_cli.py` | ابنِ `aali-cli.exe`/النسخة المثبّتة |
| `لا يوجد مفتاح تحقق مثبّت` | البناء بلا `AALI_CLI_UPDATE_PUBKEY` | أعد البناء بمفتاح، أو عيّن المتغيّر |
| `فشل التحديث: update download failed: HTTP 404` | لا شيء منشور لهذا التطبيق/المنصّة | انشر عبر `publish_cli.bat` |
| `فشل التحديث: manifest signature invalid` | توقيع غير صحيح أو مفتاح مختلف | **لا تتجاوز** — افحص `AALI_UPDATE_SIGNING_KEY` على المركز |
| `فشل التحديث: zip-slip refused` | الحزمة تحاول الخروج من مجلدها | **ارفضها** — هذه محاولة عبث |
| `فشل التحديث: refusing plain http` | عنوان المركز بـ http | `aali.dpdns.org` (بلا http) |
| لا شيء رغم وجود إصدار | الفحص التلقائي معطّل، أو الكاش 6 ساعات | `/update check` |

---

## 2) In English

### What `/update` does

The terminal client updates **itself** from the Aali Hub on the same signed
contract as Studio, Desktop and the Node: the hub's manifest signature is
re-verified **on your machine** (Ed25519), the artifact is SHA256-checked and
detached-signature-checked, nothing downloaded is ever executed, zip-slip and
tar-slip members are refused, the previous version is retained (two failed
launches roll back automatically), HTTPS is mandatory except on localhost,
and every event is written to a content-free log at
`<data root>/updates/update.log`.

### Commands

`/update` (status) · `/update check` · `/update apply` (download → verify →
stage → activate → **asks** before relaunching) · `/update version` ·
`/update auto on|off` · `/update channel stable|beta` · `/update rollback`

### Configuration

`~/.aali/cli_update.json` — `auto_check` (default ON), `channel`
(stable/beta), `hub_url` (default `aali.dpdns.org`), `lang` (`ar` / `en` /
`both`). Override the path with `AALI_CLI_UPDATE_CONFIG`.

### Bilingual output

Every message in `scripts/cli_update.MSG` is an `(arabic, english)` pair;
`t(key, lang)` is the only place a string is chosen. Arabic is the default
(Arabic-first product), `both` prints the Arabic then a dim English line.
A test formats every message with the same field set, so a message that
formats in Arabic and explodes in English cannot ship.

### Honesty rules

- A **source run** cannot update itself and says so.
- A build with **no verification key** refuses every release instead of
  showing an unverified "update available".
- The launch-time check runs on a **background thread**: an unreachable hub
  never delays the banner or the prompt.
- `/update apply` **asks** before relaunching — a terminal client that respawns
  itself behind your back is a surprise, not a feature.

### Environment switches

`AALI_CLI_UPDATE_OFF=1` (disable everything) · `AALI_CLI_UPDATE_NO_EXIT=1`
(never `os._exit`; used by the tests) · `AALI_CLI_UPDATE_PUBKEY` (bundled
Ed25519 **public** key, 64 hex) · `AALI_CLI_UPDATE_DIR` (install dir for a
source run / tests) · `AALI_UPDATE_TOKEN` (private hub).

### Tests

`tests/test_cli_update.py` — 38 cases: the bilingual table, every command and
its refusal path, the exact launch notice the owner specified, and the whole
check → download → verify → stage → activate → two-failed-launches → rollback
flow against a **real loopback hub serving a real signed manifest**. No test
reaches the public internet: the hub fixture is loopback, or the check is
replaced by an offline manifest.

### Build note

`scripts/build_desktop.bat` and `scripts/build_all_macos.sh` pass
`--paths scripts --hidden-import shared.updater --hidden-import cli_update` to
PyInstaller. Without them the binary builds cleanly and `/update` dies with
an `ImportError` at runtime — a test pins both scripts.
