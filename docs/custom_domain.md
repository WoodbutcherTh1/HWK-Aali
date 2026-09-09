# آلي على الإنترنت — دومينك الخاص + HTTPS دائم

يستبدل الرابط المؤقت (`xxxx.trycloudflare.com`) برابط **دائم** باسم نطاقك:

```
https://aali.موقعك.com/ui/
```

مجاناً بالكامل، HTTPS تلقائي (شهادة سحابية لا تنتهي عليك)، بدون فتح منافذ
في الراوتر، وبدون الحاجة لأن يعمل أي شيء بعد إعادة تشغيل الجهاز —
النفق يشتغل كخدمة ويندوز تلقائياً.

---

## المتطلبات (مرة واحدة)

1. **نطاق (Domain)** مملوك لك — من أي مسجّل (Namecheap، GoDaddy…).
2. **حساب Cloudflare مجاني** — [dash.cloudflare.com](https://dash.cloudflare.com).
3. النطاق **مضاف في Cloudflare** (Nameservers الموجّهة إلى Cloudflare —
   Cloudflare يشرح الخطوة عند الإضافة ويستغرق ساعات قليلة أحياناً).
4. خادم آلي يعمل محلياً: `scripts\start_app.bat` على المنفذ 5055.

> لا تحتاج VPS ولا استضافة — الجهاز عندك في البيت هو الخادم، والنفق
> يوصّل الإنترنت به من الداخل بأمان.

---

## نطاق مجاني من DigitalPlat (dpdns.org) — الخطوة الإضافية الوحيدة

إذا كان نطاقك مجانياً من [DigitalPlat FreeDomain](https://dashboard.digitalplat.org)
(مثل `aali.dpdns.org`)، فالفرق الوحيد عن المسجّل العادي: بدلاً من تغيير
الـ nameservers عند المسجّل، تغيّرها في **لوحة DigitalPlat نفسها**:

1. **Cloudflare أولاً**: dash.cloudflare.com ← Add a site ← `aali.dpdns.org`
   (خطة Free) ← انسخ **الـ nameservers الاثنين** اللذين يعيّنهما Cloudflare
   (مثال: `xxx.ns.cloudflare.com` / `yyy.ns.cloudflare.com`).
2. **لوحة DigitalPlat**: صفحة نطاقك ← Delegation mode =
   **external nameservers** ← الصق الاثنين ← حفظ.
3. انتظر انتشار DNS (دقائق إلى ساعات) — تحقق من
   [dnschecker.org](https://dnschecker.org) حتى تظهر الـ NS الخاصة
   بـ Cloudflare في الجواب.
4. ثم تابع الطريقة الآلية بالأسفل: `scripts\aali_domain.bat`
   (نفس النفق المسمى `aali`، نفس النتيجة `https://aali.dpdns.org`).

> ملاحظة: لا يمكن لـ DigitalPlat نفسه تحرير سجلات DNS لنطاقك — دوره
> ينتهي عند تفويض الـ nameservers (حسب توثيقهم الرسمي). سجلات A/CNAME
> تُدار في Cloudflare (أو أي مزوّد DNS خارجي تختاره).

---

## الطريقة الآلية (الأسهل)

شغّل `scripts\aali_domain.bat` **كمسؤول** (نقرة يمين ← تشغيل كمسؤول) واتبع:

| الخطوة | ماذا يحدث |
|---|---|
| ١ | يفتح المتصفح لتسجيل الدخول إلى Cloudflare واختيار النطاق (Authorize) |
| ٢ | تكتب العنوان الذي تريده، مثال: `aali.yourdomain.com` |
| ٣ | ينشئ نوفقاً دائماً باسم `aali` ويجهّز `config.yml` |
| ٤ | يربط النطاق بالنفق (سجل DNS تلقائي) ويثبّت خدمة ويندوز |

النتيجة: **`https://aali.yourdomain.com/ui/` يعمل فوراً ودائماً**،
ويبدأ تلقائياً مع كل تشغيل للويندوز.

---

## الطريقة اليدوية (لفهم ما يجري)

```bat
:: 1) تنزيل cloudflared
curl -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe

:: 2) تسجيل الدخول (يفتح المتصفح)
cloudflared.exe tunnel login

:: 3) إنشاء النفق
cloudflared.exe tunnel create aali

:: 4) ملف الإعداد %USERPROFILE%\.cloudflared\config.yml
tunnel: <UUID-من-خطوة-الإنشاء>
credentials-file: C:\Users\<أنت>\.cloudflared\<UUID>.json
ingress:
  - hostname: aali.yourdomain.com
    service: http://localhost:5055
  - service: http_status:404

:: 5) سجل DNS (CNAME تلقائي إلى <UUID>.cfargotunnel.com)
cloudflared.exe tunnel route dns aali aali.yourdomain.com

:: 6) تشغيل كخدمة 24/7 (كمسؤول)
cloudflared.exe service install
net start cloudflared
```

---

## الأمان مع الرابط العام

الرابط الآن عام فعلاً — هذه طبقات الحماية المبنية في آلي:

- **مفاتيح API**: كل مستخدم يحتاج مفتاحه (من `/signup` أو لوحة `/admin`)،
  والمفاتيح مخزّنة مجمّعة SHA-256.
- **إغلاق التسجيل الذاتي** إن أردت: شغّل الخادم مع `AALI_OPEN_SIGNUP=0`
  وصدّر المفاتيح بنفسك من لوحة التحكم.
- **بيئة عمل معزولة**: أدوات آلي محصورة داخل `D:\hwk-projects` فقط.
- **راجع من سجّل**: لوحة `/admin` تعرض كل مستخدم وتاريخ تسجيله واستخدامه.

> نصيحة: للنطاق العام، شغّل الخادم بمفتاح مدير دائم:
> `set AALI_API_KEY=مفتاح-قوي` قبل `start_app.bat` — لحماية لوحة `/admin`.

---

## إدارة الخدمة

```bat
net stop cloudflared      :: إيقاف
net start cloudflared     :: تشغيل
sc query cloudflared      :: الحالة
sc delete cloudflared     :: إزالة الخدمة (النفق يبقى في حسابك)
```

تغيير العنوان لاحقاً؟ عدّل `hostname` في
`%USERPROFILE%\.cloudflared\config.yml` ثم أعد `tunnel route dns` وأعد
تشغيل الخدمة.

---

## مشاكل شائعة

| المشكلة | الحل |
|---|---|
| `tunnel login` لا يفتح المتصفح | انسخ الرابط الظاهر في النافذة وافتحه يدوياً |
| بعد Authorize لا يوجد `cert.pem` | أعد الخطوة وتأكد من اختيار نطاقك في شاشة التصريح |
| `route dns` يقول الاسم مستخدم | احذف سجل CNAME القديم من لوحة Cloudflare ثم `-f` يفرضه |
| الرابط يعمل داخلياً وليس من الخارج | تأكد أن الخدمة تعمل: `sc query cloudflared`، وأن خادم آلي يعمل على 5055 |
| خطأ 502 من Cloudflare | خادم آلي متوقف — شغّل `start_app.bat` |
| نسيان العنوان المربوط | `cloudflared.exe tunnel list` ثم اقرأ `config.yml` |

---

## البدائل

- **رابط مؤقت سريع** (بدون حساب ولا نطاق): `scripts\aali_tunnel.bat`
  — يعطي `trycloudflare.com` عشوياً مؤقتاً للتجارب.
- **VPS سحابي** + دومين: انقل الخادم عبر `Dockerfile` الموجود في
  المستودع واربط النطاق مباشرة (خارج نطاق هذا الدليل).
