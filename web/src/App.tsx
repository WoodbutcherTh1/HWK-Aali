import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ask,
  compactSession,
  getApiBase,
  getSid,
  health,
  setApiBase,
  type Policy,
  type PendingAction,
} from "./api";
import {
  fetchGithubRepos,
  fetchGithubUser,
  getGithubToken,
  setGithubToken,
  type GithubRepo,
  type GithubUser,
} from "./github";
import { msgIn, orbPulse, riseIn, spring, stagger } from "./theme";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  thinking?: boolean;
  pending?: PendingAction;
}

const ACTIONS: { icon: string; title: string; sub: string; prompt: string }[] = [
  { icon: "🛠️", title: "ابنِ تطبيقاً", sub: "كود + تشغيل + إصلاح أخطاء", prompt: "أنشئ تطبيق ويب بسيط لعرض الأذكار اليومية ثم شغّله" },
  { icon: "🧩", title: "اصنع سير عمل n8n", sub: "من وصف عربي إلى JSON جاهز", prompt: "اصنع لي سير عمل n8n: عند وصول بريد جديد أرسل ملخصه إلى تيليجرام" },
  { icon: "🖼️", title: "اقرأ صورة", sub: "استخراج النص من الصور", prompt: "اقرأ الصورة الموجودة في مجلد العمل واستخرج النص منها" },
  { icon: "🎬", title: "حلّل فيديو", sub: "إطارات + تعليق صوتي", prompt: "لخّص لي فيديو في مجلد العمل: ما النص الظاهر وما المحتوى المنطوق؟" },
  { icon: "🎨", title: "حرّر صورة", sub: "توليد وتعديل بالذكاء الاصطناعي", prompt: "حرّر صورة: صحّح الألوان واجعلها بأسلوب غروب دافئ" },
  { icon: "📄", title: "اقرأ مستنداً", sub: "PDF / Word / Excel", prompt: "اقرأ ملف PDF في مجلد العمل ولخّص أهم النقاط بالعربية" },
];

const POLICIES: { id: Policy; label: string; icon: string; hint: string }[] = [
  { id: "auto", label: "تلقائي", icon: "⚡", hint: "ينفّذ الطلبات مباشرة دون توقف" },
  { id: "aggressive", label: "قوي", icon: "🚀", hint: "ينجز أسرع طريقة ممكنة بأقل أسئلة" },
  { id: "always_ask", label: "اسأل دائماً", icon: "🛡️", hint: "يوقف أي إجراء خطير حتى تؤكد" },
];

// Slash-command palette — typing "/" opens this, exactly like the composer
// menus Aali itself was modeled on. Each command either fills the draft with
// a ready prompt template (kind: "insert", left for the user to finish and
// send) or runs a UI action immediately (kind: "action").
type SlashCommand = {
  cmd: string;
  icon: string;
  label: string;
  hint: string;
  kind: "insert" | "action";
  value: string;
};

const SLASH_COMMANDS: SlashCommand[] = [
  {
    cmd: "usage", icon: "📖", label: "الاستخدام", hint: "اشرح لي كل ما تستطيع فعله",
    kind: "insert", value: "اشرح لي كل ما تستطيع فعله وكيف أستخدمك بأفضل شكل",
  },
  {
    cmd: "files", icon: "📁", label: "أضف ملفات", hint: "اعمل على ملف أو مسار محدد",
    kind: "insert", value: "اعمل على الملف/المسار التالي في مساحة العمل: ",
  },
  {
    cmd: "skills", icon: "🧩", label: "المهارات", hint: "اعرض المهارات المتاحة",
    kind: "insert", value: "اعرض لي المهارات المتاحة واشرح متى تُستخدم كل واحدة",
  },
  {
    cmd: "design", icon: "🎨", label: "تصميم", hint: "صمّم واجهة أو شعاراً",
    kind: "insert", value: "صمّم لي: ",
  },
  {
    cmd: "extract", icon: "📄", label: "استخراج", hint: "استخرج نصاً من صورة أو مستند",
    kind: "insert", value: "استخرج النص/المحتوى من الملف التالي: ",
  },
  {
    cmd: "web", icon: "🌐", label: "بحث في الويب", hint: "ابحث ثم اقرأ أفضل نتيجة",
    kind: "insert", value: "ابحث في الويب عن: ",
  },
  {
    cmd: "github", icon: "🐙", label: "GitHub", hint: "افتح لوحة GitHub",
    kind: "action", value: "github",
  },
  {
    cmd: "policy", icon: "🛡️", label: "سياسة التنفيذ", hint: "تلقائي / قوي / اسأل دائماً",
    kind: "action", value: "policy",
  },
  {
    cmd: "compact", icon: "🗜️", label: "اختصار المحادثة", hint: "لخّص الأقدم لتوفير السياق",
    kind: "action", value: "compact",
  },
  {
    cmd: "new", icon: "＋", label: "محادثة جديدة", hint: "ابدأ من الصفر",
    kind: "action", value: "new",
  },
];

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [apiInput, setApiInput] = useState(getApiBase());
  const chatRef = useRef<HTMLDivElement>(null);
  const lastUserMessage = useRef<string>("");

  const [policy, setPolicy] = useState<Policy>(
    (localStorage.getItem("aali_policy") as Policy) || "auto"
  );
  const [policyOpen, setPolicyOpen] = useState(false);

  const [ghOpen, setGhOpen] = useState(false);
  const [ghTokenInput, setGhTokenInput] = useState("");
  const [ghUser, setGhUser] = useState<GithubUser | null>(null);
  const [ghRepos, setGhRepos] = useState<GithubRepo[]>([]);
  const [ghLoading, setGhLoading] = useState(false);
  const [ghError, setGhError] = useState("");

  const [slashOpen, setSlashOpen] = useState(false);
  const slashQuery =
    slashOpen && draft.startsWith("/") && !draft.includes(" ")
      ? draft.slice(1).toLowerCase()
      : null;
  const slashMatches =
    slashQuery === null
      ? []
      : SLASH_COMMANDS.filter(
          (c) => c.cmd.startsWith(slashQuery) || c.label.includes(slashQuery)
        );

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

  useEffect(() => {
    localStorage.setItem("aali_policy", policy);
  }, [policy]);

  // Reconnect to GitHub silently if a token was saved from a previous visit.
  useEffect(() => {
    const saved = getGithubToken();
    if (!saved) return;
    fetchGithubUser(saved)
      .then((u) => setGhUser(u))
      .catch(() => setGithubToken(""));
  }, []);

  const send = useCallback(
    async (text: string, opts?: { confirm?: boolean }) => {
      const clean = text.trim();
      if (!clean || waiting) return;
      lastUserMessage.current = clean;
      setDraft("");
      setWaiting(true);
      const thinkMsg: Msg = { id: Date.now() + 1, role: "assistant", text: "", thinking: true };
      setMessages((m) => [
        ...(opts?.confirm ? m : [...m, { id: Date.now(), role: "user", text: clean } as Msg]),
        thinkMsg,
      ]);
      try {
        const data = await ask(clean, { policy, confirm: opts?.confirm });
        const reply = data.reply || data.error || "…";
        setMessages((m) =>
          m.map((x) =>
            x.id === thinkMsg.id
              ? { ...x, thinking: false, text: reply, pending: data.needs_confirm ? data.pending_action : undefined }
              : x
          )
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
    [waiting, policy]
  );

  const confirmPending = useCallback(() => {
    void send(lastUserMessage.current, { confirm: true });
  }, [send]);

  // Plain function (not useCallback): it closes over openGithubPanel/newChat,
  // which are declared further down this component — a dependency array here
  // would evaluate those bindings too early. Callback bodies only resolve
  // identifiers when actually invoked (after the full render has run), so a
  // forward reference in the body itself is safe.
  const runSlashCommand = (c: SlashCommand) => {
      setSlashOpen(false);
      if (c.kind === "insert") {
        setDraft(c.value);
        return;
      }
      switch (c.value) {
        case "github":
          void openGithubPanel();
          setDraft("");
          break;
        case "policy":
          setPolicyOpen(true);
          setDraft("");
          break;
        case "new":
          newChat();
          break;
        case "compact": {
          setDraft("");
          const noteId = Date.now();
          setMessages((m) => [
            ...m,
            { id: noteId, role: "assistant", text: "", thinking: true } as Msg,
          ]);
          void compactSession().then((res) => {
            const text = res.compacted
              ? `🗜️ تم اختصار المحادثة — بقيت ${res.kept_turns ?? ""} رسالة حديثة.\n\n${res.summary ?? ""}`
              : res.message || "لا حاجة للاختصار الآن.";
            setMessages((m) =>
              m.map((x) => (x.id === noteId ? { ...x, thinking: false, text } : x))
            );
          });
          break;
        }
      }
  };

  const onDraftChange = (value: string) => {
    setDraft(value);
    setSlashOpen(value.startsWith("/"));
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && slashOpen) {
      setSlashOpen(false);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      setSlashOpen(false);
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

  const connectGithub = async () => {
    const token = ghTokenInput.trim();
    if (!token) return;
    setGhLoading(true);
    setGhError("");
    try {
      const [user, repos] = await Promise.all([fetchGithubUser(token), fetchGithubRepos(token)]);
      setGithubToken(token);
      setGhUser(user);
      setGhRepos(repos);
      setGhTokenInput("");
    } catch (err) {
      setGhError(err instanceof Error ? err.message : "تعذّر الاتصال بـ GitHub");
    } finally {
      setGhLoading(false);
    }
  };

  const openGithubPanel = async () => {
    setGhOpen(true);
    const token = getGithubToken();
    if (token && ghRepos.length === 0 && !ghLoading) {
      setGhLoading(true);
      try {
        setGhRepos(await fetchGithubRepos(token));
      } catch (err) {
        setGhError(err instanceof Error ? err.message : "تعذّر جلب المستودعات");
      } finally {
        setGhLoading(false);
      }
    }
  };

  const disconnectGithub = () => {
    setGithubToken("");
    setGhUser(null);
    setGhRepos([]);
  };

  const reviewRepo = (repo: GithubRepo) => {
    setGhOpen(false);
    void send(
      `راجع مستودعي على GitHub: ${repo.full_name} (${repo.html_url})` +
        (repo.description ? `\nالوصف: ${repo.description}` : "") +
        "\nاطّلع على بنية المشروع وأهم الملفات وقدّم لي تقييماً موجزاً: نقاط القوة، المخاطر، وما يستحق الإصلاح أولاً."
    );
  };

  const currentPolicy = POLICIES.find((p) => p.id === policy) ?? POLICIES[0];

  const SlashMenu = slashOpen && slashMatches.length > 0 && (
    <div className="slash-menu">
      {slashMatches.map((c) => (
        <button
          key={c.cmd}
          type="button"
          className="slash-item"
          onClick={() => runSlashCommand(c)}
        >
          <span className="slash-icon">{c.icon}</span>
          <span className="slash-text">
            <b>/{c.cmd}</b>
            <small>{c.hint}</small>
          </span>
        </button>
      ))}
    </div>
  );

  const ControlBar = (
    <div className="control-bar">
      <div className="control-item policy-control">
        <button
          type="button"
          className="control-btn"
          onClick={() => setPolicyOpen((v) => !v)}
          title={currentPolicy.hint}
        >
          <span>{currentPolicy.icon}</span>
          <span>{currentPolicy.label}</span>
        </button>
        {policyOpen && (
          <div className="control-menu" onMouseLeave={() => setPolicyOpen(false)}>
            {POLICIES.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`control-menu-item ${p.id === policy ? "active" : ""}`}
                onClick={() => {
                  setPolicy(p.id);
                  setPolicyOpen(false);
                }}
              >
                <span className="cmi-icon">{p.icon}</span>
                <span className="cmi-text">
                  <b>{p.label}</b>
                  <small>{p.hint}</small>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <button type="button" className="control-btn" onClick={openGithubPanel}>
        <span>🐙</span>
        <span>{ghUser ? ghUser.login : "GitHub"}</span>
      </button>
    </div>
  );

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
                <div className="hero-form-row">
                  {SlashMenu}
                  <textarea
                    id="heroInput"
                    rows={1}
                    placeholder="اطلب أي شيء… أو اكتب / لرؤية الأوامر"
                    value={draft}
                    onChange={(e) => onDraftChange(e.target.value)}
                    onKeyDown={onKey}
                  />
                  <button type="submit" disabled={waiting || !draft.trim()} aria-label="إرسال">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13" /><path d="M22 2 15 22 11 13 2 9Z" /></svg>
                  </button>
                </div>
                {ControlBar}
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
                {m.pending && (
                  <div className="confirm-bar">
                    <span>
                      يريد آلي تنفيذ <code>{m.pending.tool}</code> — إجراء لا يمكن التراجع عنه بسهولة.
                    </span>
                    <div className="confirm-actions">
                      <button type="button" className="confirm-yes" onClick={confirmPending} disabled={waiting}>
                        تأكيد وتنفيذ
                      </button>
                      <button
                        type="button"
                        className="confirm-no"
                        onClick={() =>
                          setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, pending: undefined } : x)))
                        }
                      >
                        إلغاء
                      </button>
                    </div>
                  </div>
                )}
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
              className="composer-form"
            >
              {SlashMenu}
              <textarea
                rows={1}
                placeholder="تابع الحديث مع آلي… (Enter للإرسال، / لرؤية الأوامر)"
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                onKeyDown={onKey}
              />
              <button type="submit" disabled={waiting || !draft.trim()} aria-label="إرسال">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13" /><path d="M22 2 15 22 11 13 2 9Z" /></svg>
              </button>
            </form>
            {ControlBar}
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

      {/* GITHUB */}
      {ghOpen && (
        <div className="dialog-backdrop" onClick={() => setGhOpen(false)}>
          <motion.dialog
            open
            className="gh-dialog"
            initial={{ opacity: 0, scale: 0.94, y: 14 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={spring}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>🐙 GitHub</h2>
            {!ghUser ? (
              <>
                <label>
                  رمز وصول شخصي (Personal Access Token)
                  <input
                    type="password"
                    dir="ltr"
                    value={ghTokenInput}
                    onChange={(e) => setGhTokenInput(e.target.value)}
                    placeholder="ghp_…"
                  />
                </label>
                <small>
                  يُحفظ الرمز في متصفحك فقط ويُستخدم للاتصال المباشر بواجهة GitHub —
                  لا يمر عبر خادم آلي. أنشئه من <code dir="ltr">github.com/settings/tokens</code> بصلاحية{" "}
                  <code dir="ltr">repo</code> للقراءة.
                </small>
                {ghError && <p className="gh-error">{ghError}</p>}
                <div className="dialog-actions">
                  <button className="primary" onClick={connectGithub} disabled={ghLoading || !ghTokenInput.trim()}>
                    {ghLoading ? "جارٍ الاتصال…" : "اتصال"}
                  </button>
                  <button onClick={() => setGhOpen(false)}>إغلاق</button>
                </div>
              </>
            ) : (
              <>
                <div className="gh-user">
                  <img src={ghUser.avatar_url} alt="" />
                  <div>
                    <b>{ghUser.login}</b>
                    <small>متصل بـ GitHub</small>
                  </div>
                  <button className="ghost-btn" title="قطع الاتصال" onClick={disconnectGithub}>✕</button>
                </div>
                {ghError && <p className="gh-error">{ghError}</p>}
                <div className="gh-repos">
                  {ghLoading && ghRepos.length === 0 ? (
                    <p className="gh-loading">جارٍ جلب المستودعات…</p>
                  ) : (
                    ghRepos.map((repo) => (
                      <div key={repo.id} className="gh-repo">
                        <div className="gh-repo-info">
                          <b>{repo.full_name}</b>
                          <small>{repo.description || "بلا وصف"}</small>
                          <div className="gh-repo-meta">
                            {repo.language && <span>{repo.language}</span>}
                            <span>★ {repo.stargazers_count}</span>
                            {repo.private && <span>خاص</span>}
                          </div>
                        </div>
                        <button className="gh-review-btn" onClick={() => reviewRepo(repo)}>
                          مراجعة
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
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
