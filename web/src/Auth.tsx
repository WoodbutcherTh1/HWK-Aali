import { useState } from "react";
import { motion } from "framer-motion";
import {
  authLogin,
  authResetConfirm,
  authResetRequest,
  authSignup,
  authVerify,
} from "./api";

type Mode = "login" | "signup" | "verify" | "reset-request" | "reset-confirm";

const spring = { type: "spring" as const, stiffness: 380, damping: 30 };

export default function AuthDialog({
  onClose,
  onSignedIn,
}: {
  onClose: () => void;
  onSignedIn: (email: string, role: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  const submit = () =>
    run(async () => {
      if (mode === "login") {
        const r = await authLogin(email.trim(), password);
        if (r.ok && r.token) {
          onSignedIn(email.trim(), r.role || "user");
          onClose();
        } else setError(r.error || "فشل تسجيل الدخول");
      } else if (mode === "signup") {
        const r = await authSignup(email.trim(), password);
        if (r.ok) {
          setMode("verify");
          setInfo("أدخل رمز التحقق المكوّن من 6 أرقام." + (r.dev_code ? ` (وضع التجربة: ${r.dev_code})` : ""));
        } else setError(r.error || "فشل التسجيل");
      } else if (mode === "verify") {
        const r = await authVerify(email.trim(), code.trim());
        if (r.ok) {
          const l = await authLogin(email.trim(), password);
          if (l.ok) {
            onSignedIn(email.trim(), l.role || "user");
            onClose();
          } else {
            setMode("login");
            setInfo("تم التحقق — سجّل دخولك الآن");
          }
        } else setError(r.error || "رمز خاطئ");
      } else if (mode === "reset-request") {
        const r = await authResetRequest(email.trim());
        if (r.ok) {
          setMode("reset-confirm");
          setInfo(r.dev_code ? `رمز إعادة التعيين: ${r.dev_code}` : "إن كان البريد مسجلاً فسيصل رمز لإعادة التعيين");
        } else setError(r.error || "تعذر إرسال الرمز");
      } else {
        const r = await authResetConfirm(email.trim(), code.trim(), password);
        if (r.ok) {
          setMode("login");
          setInfo("تم تعيين كلمة السر الجديدة — سجّل دخولك");
        } else setError(r.error || "تعذر إعادة التعيين");
      }
    });

  const titles: Record<Mode, string> = {
    login: "تسجيل الدخول إلى آلي",
    signup: "حساب جديد",
    verify: "تحقق من بريدك",
    "reset-request": "استعادة كلمة السر",
    "reset-confirm": "رمز إعادة التعيين",
  };

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <motion.dialog
        open
        initial={{ opacity: 0, scale: 0.94, y: 14 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={spring}
        onClick={(e) => e.stopPropagation()}
        className="auth-dialog"
        style={{ minWidth: 320, maxWidth: 380 }}
      >
        <h3 style={{ margin: "0 0 14px", textAlign: "center" }}>{titles[mode]}</h3>

        {mode === "verify" || mode === "reset-confirm" ? (
          <input
            dir="ltr"
            inputMode="numeric"
            placeholder="••••••"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className="auth-input"
            style={{ textAlign: "center", letterSpacing: 8, fontSize: 20 }}
          />
        ) : (
          <input
            dir="ltr"
            type="email"
            placeholder="email@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="auth-input"
          />
        )}

        {mode !== "verify" && mode !== "reset-request" && (
          <input
            dir="ltr"
            type="password"
            placeholder={mode === "reset-confirm" ? "كلمة السر الجديدة" : "كلمة السر"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            className="auth-input"
          />
        )}

        {error && <p style={{ color: "#e5484d", fontSize: 13, margin: "8px 0 0" }}>{error}</p>}
        {info && <p style={{ color: "#8a8f98", fontSize: 12.5, margin: "8px 0 0" }}>{info}</p>}

        <button className="auth-submit" disabled={busy || !email || (mode !== "reset-request" && (!password || (mode === "verify" && !code)))} onClick={submit}>
          {busy ? "…" : mode === "login" ? "دخول" : mode === "signup" ? "إنشاء الحساب" : mode === "verify" ? "تحقق" : mode === "reset-request" ? "أرسل الرمز" : "تعيين كلمة السر"}
        </button>

        <div className="auth-links">
          {mode === "login" && (
            <>
              <button onClick={() => { setMode("signup"); setError(""); setInfo(""); }}>حساب جديد</button>
              <button onClick={() => { setMode("reset-request"); setError(""); setInfo(""); }}>نسيت كلمة السر؟</button>
            </>
          )}
          {mode === "signup" && (
            <button onClick={() => { setMode("login"); setError(""); setInfo(""); }}>لدي حساب بالفعل</button>
          )}
          {(mode === "verify" || mode === "reset-confirm" || mode === "reset-request") && (
            <button onClick={() => { setMode("login"); setError(""); setInfo(""); }}>رجوع</button>
          )}
        </div>
      </motion.dialog>
    </div>
  );
}
