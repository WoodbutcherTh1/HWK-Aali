# Desktop-Apps — تطبيقات آلي المكتبية والجوالة
# Aali's Mac & iPhone apps — one folder the owner can copy anywhere

هذا المجلد **نسخة قابلة للنسخ** من تطبيقي آلي على ماك وآيفون، لأن بناء
تطبيقات macOS/iOS لا يتم إلا على جهاز ماك (قاعدة المستودع: أعمال البناء على
جهاز المالك فقط). انسخ المجلد كاملاً إلى الماك (فلاش USB / AirDrop / أي طريقة)
ثم اتبع الخطوات أدناه.

This folder is a **copy-ready bundle** of both native Aali clients. Native
macOS/iOS builds can only happen on the owner's MacBook, so this folder is
what gets copied over there.

---

## ١) آلي ستوديو — macOS  ·  Aali Studio for macOS

**المحتويات:** `Aali-Studio-macOS/` — سكربت البناء + ملف PyInstaller spec.
الشيفرة الكاملة للستوديو موجودة في المستودع نفسه
(`build-desktop/aali-studio/`) — هذا المجلد يكفي للبناء على ماك بعد استنساخ
المستودع أو نسخه.

**الخطوات على الماك:**

```bash
cd ~/HWK-Aali            # جذر المستودع (the repo root on the Mac)
./Desktop-Apps/Aali-Studio-macOS/build-mac.sh
# الناتج: build-desktop/dist/Aali-Studio.app
open build-desktop/dist/Aali-Studio.app
```

- أول تشغيل فقط، إذا شكا Gatekeeper:
  `xattr -dr com.apple.quarantine build-desktop/dist/Aali-Studio.app`
- السكربت ينشئ بيئته الخاصة `.venv-studio` (flask + pywebview + pyinstaller)
  ولا يلمس بيئة التدريب أبداً.
- العقل (HWK-AZiZA) يعيش على حاسوب الويندوز: من ستوديو اضغط 🔑 وأدخل عنوان
  الحاسوب مثل `http://192.168.1.13:5055` والمفتاح الرئيسي. على الماك
  `127.0.0.1` هو الماك نفسه — لذلك يوجد شريط «العقل غير متصل» يشرح ذلك.

---

## ٢) آلي — iOS  ·  Aali for iPhone

**المحتويات:** `Aali-iOS/` — ملفات SwiftUI الخمسة + `project.pbxproj`.
انسخها فوق `ios/Aali/` و`ios/Aali.xcodeproj/` في استنساخ الماك للمستودع
(أو استخدم المستودع مباشرة: `ios/` هو المصدر، وهذا مجرد اعتماد مطابق).

**الخطوات على الماك:**

```bash
open ios/Aali.xcodeproj   # في Xcode
# اربط الآيفون → اختر فريق التطوير في Signing & Capabilities → Run ▶︎
```

- التطبيق يسأل العقل عبر `http://<عنوان-الحاسوب>:5055/api/ask` — نفس مفتاح
  الشبكة المحلية (عنوان الحاسوب + المفتاح من `scripts/aali_share.bat`).
- بعيداً عن المنزل: النفق `aali.dpdns.org` (عنوان الحاسوب يبقى مخفياً).

---

## ماذا يوجد أين  ·  What lives where

| المجلد | ما هو | المصدر في المستودع |
|---|---|---|
| `Aali-Studio-macOS/` | بناء ستوديو على ماك | `build-desktop/aali-studio/` |
| `Aali-iOS/` | تطبيق الآيفون (SwiftUI) | `ios/` |

قاعدة المستودع تُطبَّق هنا أيضاً: لا يُبنى أي شيء على ويندوز من هذا المجلد،
ولا يُزامَن أي شيء تلقائياً — النسخ يدوية ومقصودة.
