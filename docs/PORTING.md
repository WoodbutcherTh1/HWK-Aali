# تشغيل آلي على كل نظام — PORTING.md

قاعدة المالك: **كل تطبيق من تطبيقات آلي يجب أن يعمل على ويندوز وماك ولينكس**،
وأن يبقى الوصول من الهاتف. هذا المستند يقول بصراحةً ما يعمل أين، وما الذي
**لا** يمكن أن يعمل، وكيف تتحقق بنفسك على جهازك.

---

## 1. الخريطة الصادقة

| التطبيق | ويندوز | macOS | لينكس | iPhone / iPad |
|---|---|---|---|---|
| **آلي ستوديو** (المحرر) | ✅ `.exe` | ✅ `.app` (WKWebView) | ✅ ثنائي (WebKit2GTK) | ❌ ليس تطبيق سطح — انظر §5 |
| **آلي Desktop** | ✅ `.exe` | ✅ `.app` | ✅ نفس مسار ستوديو |
| **آلي CLI** | ✅ `.exe` | ✅ ثنائي واحد | ✅ ثنائي واحد | ❌ (Terminal.app على iPad فقط) |
| **واجهة الويب** `web/` | ✅ | ✅ | ✅ | ✅ الأفضل — المتصفح على الهاتف |
| **العقل نفسه** (`:5055` + checkpoint على `:20129`) | ✅ حيث الـGPU والأوزان | ❌ | ❌ | ❌ |

**الجملة الأهم:** **العقل يبقى على جهاز ويندوز المالك.** كرت الشاشة
(8GB) والأوزان والمدرب و`D:\hwk-data` كلها هناك. تطبيقاك على الماك
**عميل** يتصل بالعقل عبر الشبكة المحلية أو النفق — مثل Cursor الذي يتصل
بخادم OpenAI، لا كأننا نسخنا النموذج إلى الماك.

---

## 2. بناء كل شيء على الماك

```bash
git clone <repo>  HWK-Aali && cd HWK-Aali      # أو انسخ المجلد كما هو
bash scripts/build_all_macos.sh                 # Studio + Desktop + CLI
```

أو واحداً واحداً:

```bash
bash scripts/build_all_macos.sh studio     # -> build-desktop/dist/Aali-Studio.app
bash scripts/build_all_macos.sh desktop    # -> build-desktop/dist/Aali-Desktop.app
bash scripts/build_all_macos.sh cli        # -> build-desktop/dist/aali-cli
```

المخرجات كلها في `build-desktop/dist/`. كل بناء يستعمل **venv خاصاً به**
(`.venv-studio` / `.venv-desktop`) ولا يلمس venv التدريب إطلاقاً.

**نقاط قد تخنقك فعلاً (لا نخفيها):**

- **Gatekeeper**: أول تشغيل قد يقال له «الملف تالف». السبب أنه غير موقّع:
  ```bash
  xattr -dr com.apple.quarantine build-desktop/dist/Aali-Studio.app
  ```
- **التنفيذ**: `chmod +x build-desktop/dist/aali-cli`
- **أيقونة `.icns`**: مبنية الآن داخل المستودع (`build-desktop/icon.icns`)
  ومولَّدة من صور PNG الموجودة بـ `python scripts/make_icon_icns.py`.
  ولنسبة: PyInstaller لا يعود إلى أيقونة افتراضيةعند غياب الملف، بل يرمي `FileNotFoundError`، وقاصًا صار الփspec يقبل `icon=None` صراحةً: أيقونة مفقودة تعني أيقونة بلا هوية، ولا تعني فشل البناء.
  بل يرمي `FileNotFoundError`، صار الـspec يقبل `icon=None` صراحةً: أيقونة
  مفقودة تعني أيقونة بلا هوية، **ولا** تعني فشل البناء.
- **بُني على Apple Silicon؟** `pyinstaller` ينتج ثنائياً لبنية الجهاز
  الذي بُني عليه. للبناء لأن معمارية Intel شغّله على Rosetta.

---

## 3. التحقق على جهازك (هذا ما يجعل الكلام صادقاً)

لا نخمّن. قبل أن تقول «يعمل على الماك»، شغّل:

```bash
python3 scripts/portability_check.py            # تقرير مقروء
python3 scripts/portability_check.py --json     # للآلة
```

يفحص **على الجهاز الذي يعمل فيه**، حرفاً بحرف:

1. النظام ونسخة Python والمعمارية.
2. أن مجلدات الإعداد تتبع **اصطلاح النظام نفسه**
   (`~/Library/Application Support/AaliStudio` على الماك، `AppData` على ويندوز،
   `~/.config` على لينكس) — لا مجلدات متناثرة في المنزل.
3. أن الصندوق الذاتي (sandbox) يقرأ ويكتب، **ويرفض ٦ محاولات هروب**.
4. أن خادم آلي ستوديو يقلع على منفذ حر ويجيب `/api/health`، وأن شجرة
   الملفات والبحث والكتابة تعمل، وأن **هروب المسار مرفوض**.
5. أن قاموس أحداث لوحة الوكيل السبعة سليم على هذا النظام.
6. أن عارض العربية في الـCLI يعمل (يدعم الحروف المشكَّلة **و**-base).
7. هل العقل على `:5055` يمكن الوصول منه، وإن لم يستطع فلماذا بالضبط.

قواعد الخروج: `0` = كل الفحوص الأساسية نجحت، `1` = فشل أساسي، `2` =
التقرير نفسه لم يعمل. انسخ المخرج والصقه لي — **هو مصدر الحقيقة الوحيد**
لما يحدث على الماك.

---

## 4. أين يبقى كل غرض؟

| الغرض | ويندوز | غير ويندوز |
|---|---|---|
| `%APPDATA%\AaliDesktop` | `%APPDATA%` | `~/Library/Application Support/AaliDesktop` |
| `%APPDATA%\AaliStudio` | `%APPDATA%` | `~/Library/Application Support/AaliStudio` |
| `D:\hwk-data` | `D:\hwk-data` (إن وُجد) | `~/hwk-data` |
| سجلّ studio | `D:/hwk-data/studio.log` | `~/hwk-data/studio.log` |

كل هذا يمرّ عبر **`file-agent/file_agent/hwk_paths.py`** — وهو ما يفرضه
AGENTS.md أصلاً («لا ت hardcode مسارات المستخدمabsolute في كود المكتبة»).
كان لكل تطبيق نسخته الخاصة من `%APPDATA%`، وهو ما كان سيوزّع مجلدات
`~/AaliStudio` عشوائية على الماك. الآن هناك مصدر واحد، و
`tests/test_portability.py` يمنع الانحدار.

---

## 5. الهواتف — الحقيقة بلا تجميل

**iPhone لا يستطيع تشغيل آلي ستوديو أو Desktop.** هما تطبيقا سطح مكتب
(pywebview + Flask + نظام ملفات)، ونظام iOS لا يسمح بتطبيق يفتح processes
أو يفتح ملفات خارج حاويته. أي وعد بغير ذلك كذب.

**ما يعمل على الهاتف فعلاً:**

1. **واجهة الويب** — `web/` تعمل في Safari على iPhone. هذا هو عميل آلي
   الكامل: محادثة، بث، ذاكرة، جلسات.
2. **الشبكة المحلية** — MacBook و iPhone على نفس الواي-فاي:
   `http://<ip-of-the-pc>:5055` + المفتاح الرئيسي من `🔑`.
3. **النفق** — `https://aali.dpdns.org` (يعمل من أي مكان، بدون وي-فاي).
4. **تطبيق iOS حقيقي** — مشروع SwiftUI موجود في `ios/Aali.xcodeproj`،
   ويُبنى على جهاز Mac بـ Xcode (يحتاج حساب Apple مجانياً).

**إن أردت آلي ستوديو على iPad** someday: الطريق الصحيح ليس حزم Python،
بل نقل واجهة الويب إلى تطبيق WebView (`WKWebView`) يتصل بـ:5059 — أي
غلاف حول `web/` نفسه. هذا مشروع يوم مستقل، لا جزء من هذا الإصدار.

---

## 6. ما الذي تغيّر في الكود ليعمل على غير ويندوز

| المشكلة | قبل | بعد |
|---|---|---|
| زر «افتح الرابط» | `os.startfile` (ويندوز فقط) → انهيار على الماك | `hwk_paths.open_external`: `startfile` / `open` / `xdg-open` |
| زر المشاركة (cloudflared) | `cloudflared.exe` فقط → الزر يختفي بصمت | `hwk_paths.cloudflared_name()` حسب النظام |
| مجلدات الإعداد | `%APPDATA%` (وإلا `~/AaliStudio`) | اصطلاح النظام عبر `hwk_paths.config_dir()` |
| حزمة `.app` على الماك | `EXE + BUNDLE` **بدون** `COLLECT` | `EXE → COLLECT → BUNDLE` (وإلا اختفت نافذة WebView) |
| سكربت بناء الماك | **غير موجود** رغم أن الـspec يشير إليه | `scripts/build_all_macos.sh` + `build-mac.sh` |

**آلي CLI لم يحتج إصلاحاً** — كان أصلاً متعدد الأنظمة (`afplay/aplay`
للصوت، `clear` بدل `cls`، ثقة bidi حسب المنصة). تحتاج فقط هدف بناء.

---

## 7. حدود صادقة

1. **أول تشغيل حقيقي على ماك تم فعلاً** (MacBook Air · Darwin 25.6.0 arm64 ·
   Python 3.13.7 · 2026-10-03) وكشف عيبين عيبين حقيقيين لم يكن ليلاحظهما على ويندوز:
   **(a)** البناء مات في خطوة `BUNDLE` بـ `FileNotFoundError: icon.icns` بعد
   14 ثانية من تحليل ناجح — أيقونة مفقودة أوقفت بناءًا كاملاً.
   **(b)** `build_all_macos.sh check` نزل إلى `python3` النظامي بلا flask، فقال
   «studio_server imports: No module named 'flask'» — حكم على المفسّر ولا على ماك.
   كلاهما مُصلَح ومُختبَر (انظر `scripts/make_icon_icns.py` وهمر الاختيار).
   **ولما يزال macOS غير مُصدّق عليه بالكامل:** البناء الذي مات قبل
   الإصلاح لم يكتملب، والحكم نهائي يأتي من `build_all_macos.sh check`.
2. **حزمة `.app` غير موقّعة** → Gatekeeper سيزعجك مرة واحدة (الحل في §2).
3. **آلة Brain تبقى على ويندوز.** بدونها لا يوجد نموذج؛ العملاء مجرد واجهات.
4. **لينكس يحتاج WebKit2GTK** لتظهر النافذة:
   `sudo apt install libwebkit2gtk-4.1-0` (بدونه يعمل الخادم ولا تظهر نافذة).
5. **`print_file` ما زال ويندوز فقط** ويحتجب بوضوح — طابعة ويندوزRAW.
