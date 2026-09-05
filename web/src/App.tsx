import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ask, getApiBase, getSid, health, setApiBase } from "./api";
import { msgIn, orbPulse, riseIn, spring, stagger } from "./theme";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  thinking?: boolean;
}

const ACTIONS: { icon: string; title: string; sub: string; prompt: string }[] = [
  { icon: "🛠️", title: "ابنِ تطبيقاً", sub: "كود + تشغيل + إصلاح أخطاء", prompt: "أنشئ تطبيق ويب بسيط لعرض الأذكار اليومية ثم شغّله" },
  { icon: "🧩", title: "اصنع سير عمل n8n", sub: "من وصف عربي إلى JSON جاهز", prompt: "اصنع لي سير عمل n8n: عند وصول بريد جديد أرسل ملخصه إلى تيليجرام" },
  { icon: "🖼️", title: "اقرأ صورة", sub: "استخراج النص من الصور", prompt: "اقرأ الصورة الموجودة في مجلد العمل واستخرج النص منها" },
  { icon: "🎬", title: "حلّل فيديو", sub: "إطارات + تعليق صوتي", prompt: "لخّص لي فيديو في مجلد العمل: ما النص الظاهر وما المحتوى المنطوق؟" },
  { icon: "🎨", title: "حرّر صورة", sub: "توليد وتعديل بالذكاء الاصطناعي", prompt: "حرّر صورة: صحّح الألوان واجعلها بأسلوب غروب دافئ" },
  { icon: "📄", title: "اقرأ مستنداً", sub: "PDF / Word / Excel", prompt: "اقرأ ملف PDF في مجلد العمل ولخّص أهم النقاط بالعربية" },
];

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [apiInput, setApiInput] = useState(getApiBase());
  const chatRef = useRef<HTMLDivElement>(null);

  const chatMode = messages.length > 0;

  const ping = useCallback(async () => setConnected(await health()), []);
  useEffect(() => {
    ping();
    const t = setInterval(ping, 15000);
    return () => clearInterval(t);
  }, [ping]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = useCallback(
    async (text: string) => {
      const clean = text.trim();
      if (!clean || waiting) return;
      setDraft("");
      setWaiting(true);
      const userMsg: Msg = { id: Date.now(), role: "user", text: clean };
      const thinkMsg: Msg = { id: Date.now() + 1, role: "assistant", text: "", thinking: true };
      setMessages((m) => [...m, userMsg, thinkMsg]);
      try {
        const data = await ask(clean);
        const reply = data.reply || data.error || "…";
        setMessages((m) =>
          m.map((x) => (x.id === thinkMsg.id ? { ...x, thinking: false, text: reply } : x))
        );
        setConnected(data.ok);
      } catch {
        setMessages((m) =>
          m.map((x) =>
            x.id === thinkMsg.id
              ? { ...x, thinking: false, text: "تعذّر الاتصال بالخادم — تحقق من ⚙︎ الإعدادات" }
              : x
          )
        );
        setConnected(false);
      } finally {
        setWaiting(false);
      }
    },
    [waiting]
  );

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  };

  const newChat = () => {
    localStorage.removeItem("aali_sid");
    setMessages([]);
  };

  const saveSettings = () => {
    setApiBase(apiInput.trim() || "http://127.0.0.1:5055");
    localStorage.removeItem("aali_sid");
    setShowSettings(false);
    void ping();
  };

  return (
    <div className="app">
      <div className="stars" aria-hidden="true" />

      <motion.header className="topbar" initial={{ y: -30, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={spring}>
        <div className="brand">
          <motion.span className="brand-star" animate={orbPulse} aria-hidden="true">✦</motion.span>
          <div className="brand-text">
            <strong>آلي</strong>
            <small className={connected === null ? "" : connected ? "ok" : "bad"}>
              {connected === null ? "…" : connected ? "متصل" : "غير متصل"}
            </small>
          </div>
        </div>
        <div className="status-pills">
          <span className={`pill ${connected ? "good" : connected === false ? "warn" : ""}`}>
            🧠 <b>العقل</b> <span>{connected ? "يعمل" : "—"}</span>
          </span>
          <span className="pill pill-wide">📁 <span>المساحة: D:\hwk-projects</span></span>
          {getSid() && <span className="pill">🔗 <span>جلسة مستمرة</span></span>}
        </div>
        <div className="top-actions">
          <button className="ghost-btn" title="الإعدادات" onClick={() => setShowSettings(true)}>⚙︎</button>
          <button className="ghost-btn" title="محادثة جديدة" onClick={newChat}>＋</button>
        </div>
      </motion.header>

      {/* HOME — hero, only before the first message */}
      <AnimatePresence mode="wait">
        {!chatMode ? (
          <motion.section
            key="home"
            className="home"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, scale: 0.985, transition: { duration: 0.18 } }}
          >
            <div className="hero">
              <motion.div className="hero-orb" animate={orbPulse} aria-hidden="true">✦</motion.div>
              <motion.h1 initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
                كيف أساعدك <span className="gold">اليوم</span>؟
              </motion.h1>
              <motion.p className="tagline" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.06 }}>
                عقل يعمل على حاسوبك: يبني التطبيقات، يقرأ صورك ومستنداتك وفيديوهاتك،
                وينفّذ سير عملك — بعربي أولاً.
              </motion.p>

              <motion.form
                className="hero-form"
                initial={{ opacity: 0, y: 18, scale: 0.99 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ ...spring, delay: 0.12 }}
                onSubmit={(e) => { e.preventDefault(); void send(draft); }}
              >
                <textarea
                  id="heroInput"
                  rows={1}
                  placeholder="اطلب أي شيء… مثال: ابنِ لي تطبيق مهام بسيط"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onKey}
                />
                <button type="submit" disabled={waiting || !draft.trim()} aria-label="إرسال">
                  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13" /><path d="M22 2 15 22 11 13 2 9Z" /></svg>
                </button>
              </motion.form>

              <motion.div className="actions" variants={stagger} initial="hidden" animate="show">
                {ACTIONS.map((a) => (
                  <motion.button
                    key={a.title}
                    className="action"
                    variants={riseIn}
                    whileHover={{ y: -3, borderColor: "rgba(232,179,75,.5)" }}
                    whileTap={{ scale: 0.97 }}
                    onClick={() => void send(a.prompt)}
                  >
                    <span className="action-ico">{a.icon}</span>
                    <b>{a.title}</b>
                    <small>{a.sub}</small>
                  </motion.button>
                ))}
              </motion.div>
            </div>
          </motion.section>
        ) : (
          <motion.main
            key="chat"
            ref={chatRef}
            className="chat"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
          >
            {messages.map((m) => (
              <motion.div
                key={m.id}
                className={`msg ${m.role}`}
                initial={msgIn.initial}
                animate={msgIn.animate}
              >
                <span className="who">{m.role === "user" ? "أنت" : "آلي"}</span>
                {m.thinking ? <ThinkingOrbit /> : m.text}
              </motion.div>
            ))}
          </motion.main>
        )}
      </AnimatePresence>

      {/* DOCKED COMPOSER — only in chat mode */}
      <AnimatePresence>
        {chatMode && (
          <motion.footer
            className="composer"
            initial={{ y: 60, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 60, opacity: 0 }}
            transition={spring}
          >
            <form
              onSubmit={(e) => { e.preventDefault(); void send(draft); }}
            >
              <textarea
                rows={1}
                placeholder="تابع الحديث مع آلي… (Enter للإرسال)"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKey}
              />
              <button type="submit" disabled={waiting || !draft.trim()} aria-label="إرسال">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13" /><path d="M22 2 15 22 11 13 2 9Z" /></svg>
              </button>
            </form>
            <p className="hint">
              يتصل بـ <code>{getApiBase()}</code> — غيّره من ⚙︎ الإعدادات
            </p>
          </motion.footer>
        )}
      </AnimatePresence>

      {/* SETTINGS */}
      {showSettings && (
        <div className="dialog-backdrop" onClick={() => setShowSettings(false)}>
          <motion.dialog
            open
            initial={{ opacity: 0, scale: 0.94, y: 14 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={spring}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>⚙︎ الإعدادات</h2>
            <label>
              عنوان الخادم (API)
              <input
                type="url"
                dir="ltr"
                value={apiInput}
                onChange={(e) => setApiInput(e.target.value)}
                placeholder="http://127.0.0.1:5055"
              />
            </label>
            <small>
              من الجوال أدخل عنوان حاسوبك على الشبكة، مثال: <code dir="ltr">http://192.168.1.10:5055</code>
            </small>
            <div className="dialog-actions">
              <button className="primary" onClick={saveSettings}>حفظ</button>
              <button onClick={() => setShowSettings(false)}>إغلاق</button>
            </div>
          </motion.dialog>
        </div>
      )}
    </div>
  );
}

function ThinkingOrbit() {
  return (
    <span className="thinking">
      <motion.span
        className="orbit"
        animate={{ rotate: 360 }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }}
      >
        <span className="orbit-sat">✦</span>
      </motion.span>
      <span>آلي يفكّر…</span>
    </span>
  );
}
