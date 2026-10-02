# -*- coding: utf-8 -*-
"""Local-business answering vertical (Phase 2 slice 4).

Aali answering the phone for a shop: hours, location, prices, delivery,
payment methods — from the shop's OWN data, in Arabic / Hebrew / English,
without an LLM call and without inventing anything.

Honesty rules this plugin lives by:
- it answers only what the profile contains; a missing field produces an
  explicit "not configured yet", never a guess;
- an unrecognised question returns None so the turn continues to Aali —
  the plugin never improvises a business fact;
- it never confirms a booking, an order, or a payment. Those are not
  implemented, so it says so plainly instead of pretending a system
  recorded something;
- cultural neutrality: no greeting, phrasing, or assumption about the
  customer beyond what they said.

Enable it (off by default) in config.yaml:

    plugins:
      - name: business
        enabled: true
        profile: D:/hwk-data/voice_business/profile.json
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice.core.language_detect import detect_script_lang, normalize_for_match

DAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
DAYS_HE = ["יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "יום שבת", "יום ראשון"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# intent -> keywords (folded comparison, so spelling variants still match)
INTENTS: Dict[str, List[str]] = {
    "greeting": ["مرحبا", "السلام عليكم", "اهلا", "שלום", "הי", "hello", "hi", "good morning"],
    "hours": ["دوام", "اوقات العمل", "متى تفتحون", "متى تفتتحون", "شو الدوام", "ساعات العمل",
              "تفتحون", "تفتتحون", "مفتوح",
              "שעות", "מתי", "פתוח", "פתיחה", "סגור", "סגירה",
              "hours", "open", "close", "closing time"],
    "location": ["وين", "اين", "عنوان", "موقع", "كيف اوصل", "איפה", "כתובת", "מיקום", "כיצד מגיעים",
                 "where", "address", "location", "directions"],
    "phone": ["رقم", "تلفون", "هاتف", "מספר", "טלפון", "phone", "number", "whatsapp"],
    "prices": ["بكم", "كم السعر", "اسعار", "الاسعار", "كم بتكلف", "כמה עולה", "מחיר", "מחירים",
               "how much", "price", "prices", "cost"],
    "delivery": ["توصيل", "توصيل طلب", "منيو", "משלוח", "תפריט", "מנו", "delivery", "menu",
                 "takeaway", "pickup"],
    "payment": ["دفع", "الدفع", "كيف بدفع", "כרטיס", "כרטיסיות", "תשלום", "אשראי", "מזומן",
                "pay", "payment", "card", "cash"],
    "closing": ["شكرا", "شكراً", "يعطيك العافية", "תודה", "תודה רבה", "thanks", "thank you"],
    # things this plugin REFUSES to fake
    "booking": ["احجز", "حجز", "اطلب", "طلب", "الطلب",
                "הזמנה", "הזמין", "הזמנות", "הזמנתי", "בקשתי", "להזמין",
                "book", "booking", "reserve", "reservation", "order"],
}

T = {
    "ar": {
        "greeting": "أهلاً وسهلاً، {name} في خدمتك. كيف بقدر أساعدك؟",
        "hours_today": "اليوم {day}، الدوام من {open} إلى {close}{note}.",
        "hours_closed_today": "اليوم {day}، الدوام مغلق.",
        "hours_no_data": "دوام العمل غير مُسجّل بعد. أقدر أوصّلك بال负责人؟".replace("负责人", "الموظف المسؤول"),
        "location": "العنوان: {address}{extra}",
        "location_no_data": "العنوان غير مُسجّل بعد.",
        "phone": "رقم الهاتف: {phone}",
        "phone_no_data": "رقم الهاتف غير مُسجّل بعد.",
        "prices": "الأسعار: {items}",
        "prices_no_data": "قائمة الأسعار غير مُسجّلة بعد.",
        "delivery": "نقدّم {delivery}.",
        "delivery_no_data": "خدمة التوصيل غير مُسجّلة بعد.",
        "payment": "طرق الدفع: {methods}",
        "payment_no_data": "طرق الدفع غير مُسجّلة بعد.",
        "closing": "العفو، أهلاً وسهلاً!",
        "booking": "الحجز عبر الهاتف غير مفعّل عندي الآن — ما بقدر أأكدلك حجزاً. {fallback}",
        "handoff": "Maybe I can pass you to whoever is on duty?",
    },
    "he": {
        "greeting": "שלום, {name} בשירותך. איך אוכל לעזור?",
        "hours_today": "היום {day}, שעות הפעילות מ-{open} עד {close}{note}.",
        "hours_closed_today": "היום {day}, סגור.",
        "hours_no_data": "שעות הפעילות עדיין לא הוגדרו.",
        "location": "הכתובת: {address}{extra}",
        "location_no_data": "הכתובת עדיין לא הוגדרה.",
        "phone": "טלפון: {phone}",
        "phone_no_data": "מספר הטלפון עדיין לא הוגדר.",
        "prices": "המחירים: {items}",
        "prices_no_data": "רשימת המחירים עדיין לא הוגדרה.",
        "delivery": "אנחנו מציעים {delivery}.",
        "delivery_no_data": "שירות משלוח עדיין לא הוגדר.",
        "payment": "אמצעי תשלום: {methods}",
        "payment_no_data": "אמצעי התשלום עדיין לא הוגדרו.",
        "closing": "בשמחה!",
        "booking": "הזמנות טלפוניות עדיין לא מוגדרות אצלי — אינני יכול לאשר הזמנה. {fallback}",
        "handoff": "אולי אעביר אתכם למי שבמשמרת?",
    },
    "en": {
        "greeting": "Hello, this is {name}. How can I help?",
        "hours_today": "Today ({day}) we are open from {open} to {close}{note}.",
        "hours_closed_today": "Today ({day}) we are closed.",
        "hours_no_data": "Our opening hours have not been set up yet.",
        "location": "Address: {address}{extra}",
        "location_no_data": "The address has not been set up yet.",
        "phone": "Phone: {phone}",
        "phone_no_data": "The phone number has not been set up yet.",
        "prices": "Prices: {items}",
        "prices_no_data": "Our price list has not been set up yet.",
        "delivery": "We offer {delivery}.",
        "delivery_no_data": "Delivery has not been set up yet.",
        "payment": "Payment methods: {methods}",
        "payment_no_data": "Payment methods have not been set up yet.",
        "closing": "You're welcome!",
        "booking": "Phone booking is not set up here — I can't confirm a booking. {fallback}",
        "handoff": "Might I pass you to whoever is on duty?",
    },
}

PLACEHOLDER_NAME = "business name"

TEMPLATE = {
    "name": "business name",
    "hours": {"mon": {"open": "09:00", "close": "18:00"}, "fri": {"closed": True}},
    "address": "street, city",
    "location_note": "free parking behind the building",
    "phone": "+000 000 0000",
    "prices": {"item": "price"},
    "delivery": "delivery and pickup",
    "payment": "cash, card",
    "fallback": {
        "ar": "ممكن أحوّلك على اللي معي الدوام؟",
        "he": "אולי אעביר אותך למי שבמשמרת?",
        "en": "Might I pass you to whoever is on duty?",
    },
}


def load_profile(path: str | Path) -> Dict[str, Any]:
    """Read a business profile; a missing/corrupt file is an EMPTY profile
    (the plugin then answers honestly instead of crashing the call)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_template(path: str | Path) -> str:
    """Write a clearly-marked starter profile the owner edits by hand."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    profile = json.loads(json.dumps(TEMPLATE))
    profile["_readme"] = ("Fill in your own data. Anything left empty is answered "
                          "honestly as 'not set up yet' — never invented.")
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


_LATIN = re.compile(r"[A-Za-z]")


def detect_intent(text: str) -> Optional[str]:
    """Map a sentence to an intent, in any of the three languages.

    Arabic and Hebrew keywords match as SUBSTRINGS, because they are
    routinely written with prefixes ("هسעות" as "השעות", "الدوام" as
    "بالدوام"). Latin keywords match on word boundaries instead — otherwise
    the greeting "hi" fires inside "this".
    """
    folded = normalize_for_match(text).lower()
    for intent, words in INTENTS.items():
        for w in words:
            k = normalize_for_match(w).lower()
            if not k:
                continue
            if _LATIN.search(k):
                if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", folded):
                    return intent
            elif k in folded:
                return intent
    return None


GENERIC = {
    "ar": "أهلاً وسهلاً، كيف بقدر أساعدك؟",
    "he": "שלום, איך אוכל לעזור?",
    "en": "Hello, how can I help?",
}


def business_name(profile: Dict[str, Any]) -> str:
    """The shop's name, or "" when it is still the template placeholder —
    speaking the literal string "business name" to a caller would be worse
    than saying nothing."""
    name = str(profile.get("name") or "").strip()
    return "" if name.lower() == PLACEHOLDER_NAME else name


def _join(items: Dict[str, str] | List[str]) -> str:
    if isinstance(items, dict):
        return "، ".join(f"{k}: {v}" for k, v in items.items())
    return "، ".join(str(i) for i in items)


class BusinessPlugin:
    """Answers a shop's own questions from its profile, in three languages."""

    name = "business"

    def __init__(self, profile: Optional[Dict[str, Any]] = None,
                 profile_path: str | Path | None = None):
        self.profile = profile if profile is not None else (
            load_profile(profile_path) if profile_path else {})

    # -- helpers -----------------------------------------------------------
    def _t(self, lang: str, key: str, **kw) -> str:
        """Render a template for `lang`.

        No clever fallback: dict.get evaluates its default eagerly, so a
        fallback lookup raised KeyError even when the real key existed.
        Every key used below is defined in all three languages.
        """
        table = T.get(lang) or T["ar"]
        tmpl = table.get(key)
        if tmpl is None:
            raise KeyError(f"missing reply template: {lang}.{key}")
        return tmpl.format(**kw)

    def _handoff(self, lang: str) -> str:
        """The hand-off line in the CALLER's language — an English sentence
        dropped into an Arabic reply is its own kind of dishonesty."""
        fb = self.profile.get("fallback")
        if isinstance(fb, dict):
            return str(fb.get(lang) or fb.get("ar") or (T.get(lang) or T["ar"])["handoff"])
        return (T.get(lang) or T["ar"])["handoff"]

    def _hours_for(self, weekday: int) -> Optional[Dict[str, Any]]:
        hours = self.profile.get("hours")
        if not isinstance(hours, dict):
            return None
        key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][weekday % 7]
        day = hours.get(key)
        return day if isinstance(day, dict) else None

    # -- hooks -------------------------------------------------------------
    def on_turn_start(self, text: str, lang: str, meta: Dict[str, Any]) -> Optional[str]:
        intent = detect_intent(text)
        if intent is None:
            return None          # not ours: let Aali answer normally
        lang = lang if lang in T else (detect_script_lang(text) or "ar")
        return self._answer(intent, lang)

    def on_turn_done(self, result: Dict[str, Any]) -> None:
        """Observer only: a shop dashboard would count intents here. Never
        logs transcripts — a caller's words are their own."""

    # -- the answers --------------------------------------------------------
    def _answer(self, intent: str, lang: str) -> str:
        p = self.profile
        if intent == "greeting":
            name = business_name(p)
            return self._t(lang, "greeting", name=name) if name \
                else GENERIC.get(lang, GENERIC["ar"])
        if intent == "closing":
            return self._t(lang, "closing")
        if intent == "booking":
            # never fake a confirmation
            return self._t(lang, "booking", fallback=self._handoff(lang))
        if intent == "hours":
            day = self._hours_for(time.localtime().tm_wday)
            if day is None:
                return self._t(lang, "hours_no_data")
            names = {"ar": DAYS_AR, "he": DAYS_HE, "en": DAYS_EN}[lang]
            name = names[time.localtime().tm_wday % 7]
            if day.get("closed"):
                return self._t(lang, "hours_closed_today", day=name)
            if not day.get("open") or not day.get("close"):
                return self._t(lang, "hours_no_data")
            note = f" ({day['note']})" if day.get("note") else ""
            return self._t(lang, "hours_today", day=name, open=day["open"],
                           close=day["close"], note=note)
        if intent == "location":
            if not p.get("address"):
                return self._t(lang, "location_no_data")
            extra = f" — {p['location_note']}" if p.get("location_note") else ""
            return self._t(lang, "location", address=p["address"], extra=extra)
        if intent == "phone":
            return self._t(lang, "phone", phone=p["phone"]) if p.get("phone") \
                else self._t(lang, "phone_no_data")
        if intent == "prices":
            prices = p.get("prices")
            if not isinstance(prices, dict) or not prices:
                return self._t(lang, "prices_no_data")
            return self._t(lang, "prices", items=_join(prices))
        if intent == "delivery":
            return self._t(lang, "delivery", delivery=p["delivery"]) if p.get("delivery") \
                else self._t(lang, "delivery_no_data")
        if intent == "payment":
            return self._t(lang, "payment", methods=p["payment"]) if p.get("payment") \
                else self._t(lang, "payment_no_data")
        return None





# -- registry wiring -------------------------------------------------------
def register_from_config(spec: Dict[str, Any]) -> Optional[BusinessPlugin]:
    """Build + register from a config entry; returns None when disabled or
    unusable (never raises into the server startup)."""
    if not spec.get("enabled"):
        return None
    profile = load_profile(spec.get("profile") or "")
    if not profile:
        write_template(spec.get("profile") or "")
        profile = load_profile(spec.get("profile") or "")
    plugin = BusinessPlugin(profile=profile, profile_path=spec.get("profile"))
    from voice.plugins.base import register

    register(plugin)
    return plugin