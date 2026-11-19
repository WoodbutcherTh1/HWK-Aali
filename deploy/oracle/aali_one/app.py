#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
آلي وان — أول واجهة عميل للخادم المُنشر.

ترسل العميل رسائله إلى نهاية البوابة المتوافقة مع موديلات OpenAI:
  https://commons-involving-executive-toner.trycloudflare.com/gw/v1/chat/completions

يحتاج التشغيل على هذا الحاسوب إلى متغيّر بيئة واحد (يُعيّنه صاحب الآلة):
  AALI_API_KEY  — المفتاح الرئيسي للخادم المُنشر، الذي تُضرّبه البوابة على
                  الطلبات القادمة.  بدون المفتاح تعيد البوابة رفضًا (401/403)
                  وهنا تظهر رسالة ودية في المحادثة بدلاً من صمت.

أمر التشغيل:
  pip install -r deploy/oracle/aali_one/requirements.txt   # مرة واحدة إذا لم يكن مثبّتاً
  AALI_API_KEY=<hub-master-key> streamlit run deploy/oracle/aali_one/app.py
  # يفتح نافذة في المتصفح على http://localhost:8501
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# إعدادات (عنوان البوابة الوحيد، وخيارات النموذج)
# ---------------------------------------------------------------------------

النموذج_الافتراضي: str = "Local HWK-AZiZA"
خيارات_النموذج: list[str] = [
    "Local HWK-AZiZA",
    "Groq",
    "DeepSeek",
]

عنوان_البوابة: str = "https://commons-involving-executive-toner.trycloudflare.com"
مسار_البوابة: str = "/gw/v1/chat/completions"

مهلة_الطلب_بالثواني: int = 90


# ---------------------------------------------------------------------------
# مساعدات صغيرة
# ---------------------------------------------------------------------------


def _مفتاح_مطلوب() -> str | None:
    """إرجاع المفتاح المُعيَّن، أو None إذا لم يُعيَّن بعد."""
    import os as _os
    return _os.environ.get("AALI_API_KEY")


def _زرع_تحية_أول_تشغيل() -> None:
    """تحية ودية عند أول تشغيل — لا ت execute أيّ طلب شبكة."""
    if "تم_الزرع" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "أهلاً بك في آلي وان — العميل الأول للخادم المُنشر.\n"
                    "اكتب رسالة وأرسلها، وسيُرسل إلى الخادم عبر البوابة."
                ),
            },
        ]
        st.session_state.تم_الزرع = True


def _تحويل_النموذج_للمطلب(النموذج: str) -> str:
    """البوابة تقبل حقل `model`، ولدينا نمرر تسمية القائمة كما هي."""
    return النموذج


# ---------------------------------------------------------------------------
# طلب HTTP (غير متدفِّق — أبسط وأ more robust للواجهة)
# ---------------------------------------------------------------------------


def _طلب_إلى_البوابة(
    الرسائل: list[dict[str, str]],
    النموذج: str,
    *,
    المفتاح: str,
) -> tuple[int, Any]:
    """
    يُرسِل المحادثة إلى البوابة ويُعيد (رمز_الحالة، الجسد).

    أيّ رد خارج 2xx يُعامل كخطأ تعرضه الواجهة بكل ودّية.
    """

    حمولة: dict[str, Any] = {
        "model": _تحويل_النموذج_للمطلب(النموذج),
        "messages": الرسائل,
        "stream": False,
    }
    بايتات_الجسد = json.dumps(حمولة).encode("utf-8")

    import http.client
    import ssl as _sslmod

    parsed = عنوان_البوابة.replace("https://", "")
    المضيف = parsed.split("/", 1)[0]
    المسار = مسار_البوابة
    السياق = _sslmod.create_default_context()

    اتصال = http.client.HTTPSConnection(
        المضيف, timeout=مهلة_الطلب_بالثواني, context=السياق
    )
    رؤوس: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {المفتاح}",
        "User-Agent": "AaliOne/1.0",
    }
    try:
        اتصال.request("POST", المسار, body=بايتات_الجسد, headers=رؤوس)
        رد = اتصال.getresponse()
        خام = رد.read()
        الحالة = رد.status
        try:
            المفسّر: Any = json.loads(خام.decode("utf-8"))
        except Exception:
            المفسّر = {"تفصيل": خام.decode("utf-8", "replace")[:400]}
    except Exception as e:  # noqa: BLE001 — شبكة، DNS، TLS، مهلة…
        المفسّر = {"تفصيل": f"تعذّر الاتصال بالبوابة: {e}"}
        الحالة = 0
    finally:
        try:
            اتصال.close()
        except Exception:
            pass

    return الحالة, المفسّر


def _تَرْسيل_خطأ_البوابة(الحالة: int, الجسد: Any) -> str:
    """تحويل رد الخطأ إلى رسالة ودية قصيرة بالعربية."""

    تفصيل: str = ""
    if isinstance(الجسد, dict):
        تفصيل = (
            الجسد.get("detail")
            or (الجسد.get("error") or {}).get("message")
            or ""
        )
    elif isinstance(الجسد, str):
        تفصيل = الجسد

    نص_التفصيل = تفصيل.strip().replace("\n", " ")

    if الحالة == 0:
        return (
            "تعذّر الاتصال بالبوابة. تحقَّق من اتصالك بالإنترنت أو أن الخادم "
            "مُشرَخ بعد إعادة التشغيل."
        )

    if الحالة == 401:
        return (
            "البوابة ترفض طلبك — المفتاح غير معروف أو يجب تعيينه على هذا "
            "الحاسوب (متغيّر البيئة AALI_API_KEY)."
        )

    if الحالة == 403:
        return (
            "البوابة ترفض الوصول إلى هذا النموذج أو هذا الطلب. قد يكون "
            "المفتاح صحيحاً لكنه لا يملك إذناً للاستخدام."
        )

    if الحالة >= 500:
        return (
            "البوابة تواجه مشكلة مؤقتة. أعد المحاولة بعد لحظات، أو تحقَّق من "
            "حالة الخادم المُنشر."
        )

    return نص_التفصيل or f"رد غير متوقَّع من البوابة (رقم {الحالة})."


def _استخرج_الرد(الجسد: Any) -> str:
    """استخرج نص الرد من رد OpenAI-compatible ناجح."""
    الخيارات: list[dict[str, Any]] | None = الجسد.get("choices")
    if not الخيارات:
        return "رد فارغ من الخادم."

    الرسالة = الخيارات[0].get("message", {})
    النص = الرسالة.get("content")
    if النص is None:
        return "رد فارغ من الخادم."

    return str(النص)


# ---------------------------------------------------------------------------
# واجهة Streamlit
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="آلي وان",
        page_icon="🤖",
        layout="centered",
    )

    st.title("آلي وان")
    st.caption("عميل خفيف للخادم المُنشر — تحدّث مع آلي عبر البوابة.")

    المفتاح = _مفتاح_مطلوب()

    # -- راية حالة المفتاح ---------------------------------------------------
    if المفتاح is None:
        st.warning(
            "لم أجد مفتاح الوصول (AALI_API_KEY) على هذا الحاسوب. "
            "الخادم المُنشر يحتاج مفتاحاً خاصاً به — اسأل صاحب الآلة ليُعيّنها لك، "
            "أو عدّل طريقة التشغيل لتمرير المفتاح."
        )
    else:
        # لا نعرض المفتاح أبدًا — المستخدم يرى فقط أننا مستعدون.
        st.success("مفتاح الوصول مُعيَّن — جاهز للإرسال.")

    # -- منتقي النموذج -----------------------------------------------------
    النموذج_المحدد: str = st.selectbox(
        "النموذج",
        options=خيارات_النموذج,
        index=خيارات_النموذج.index(النموذج_الافتراضي),
        help=(
            "البوابة تُحدِّد التوزيع الفعلي للنموذج. "
            "اختيارك هنا يُقدَّم كتفضيل للبوابة."
        ),
    )

    # -- منطقة المحادثة ---------------------------------------------------------
    if "messages" not in st.session_state:
        _زرع_تحية_أول_تشغيل()
    else:
        _زرع_تحية_أول_تشغيل()

    for رسالة in st.session_state.messages:
        with st.chat_message(رسالة["role"]):
            st.markdown(رسالة["content"])

    # -- الإدخال / الإرسال ------------------------------------------------------
    إدخال_المستخدم: str = st.chat_input("اكتب رسالتك هنا…")

    if إدخال_المستخدم:
        # إضافة رسالة المستخدم
        st.session_state.messages.append(
            {"role": "user", "content": إدخال_المستخدم}
        )
        with st.chat_message("user"):
            st.markdown(إدخال_المستخدم)

        # إذا لم يكن هناك مفتاح على الإطلاق، نرفض مسبقًا (لا طلب شبكة).
        if المفتاح is None:
            نص_الخطأ = (
                "لا يمكن إرسال الرسالة — مفتاح الوصول غير مُعيَّن على هذا الحاسوب. "
                "تحقَّق من تعيين AALI_API_KEY أو اسأل صاحب الآلة."
            )
            with st.chat_message("assistant"):
                st.markdown(نص_الخطأ)
            st.session_state.messages.append(
                {"role": "assistant", "content": نص_الخطأ}
            )
            st.rerun()

        # طلب إلى البوابة
        with st.chat_message("assistant"):
            موضع_الرسالة = st.empty()
            موضع_الرسالة.markdown("جارٍ الإرسال…")

            الحالة, الجسد = _طلب_إلى_البوابة(
                st.session_state.messages,
                النموذج_المحدد,
                المفتاح=المفتاح,
            )

            if الحالة == 200:
                الرد = _استخرج_الرد(الجسد)
            else:
                الرد = _تَرْسيل_خطأ_البوابة(الحالة, الجسد)

            موضع_الرسالة.markdown(الرد)

        st.session_state.messages.append(
            {"role": "assistant", "content": الرد}
        )

        # تثبيت التمرير عند آخر رسالة.
        st.rerun()

    # -- زر التنظيف ----------------------------------------------------
    if st.sidebar.button("مسح المحادثة", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pop("تم_الزرع", None)
        st.rerun()

    # -- تذييل صغير ---------------------------------------------------------
    st.sidebar.caption(
        f"البوابة: {عنوان_البوابة}\n"
        "الإصدار 1.0 — لا يُخزِّن محادثاتك."
    )


if __name__ == "__main__":
    main()

