import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  askStream,
  compactSession,
  deleteSession,
  getApiBase,
  getSession,
  getSid,
  getToken,
  health,
  listSessions,
  setApiBase,
  setToken,
  type ActivityEvent,
  type Policy,
  type SessionRow,
} from "./api";
import {
  fetchGithubRepos,
  fetchGithubUser,
  getGithubToken,
  setGithubToken,
  type GithubRepo,
  type GithubUser,
} from "./github";
import Markdown from "./markdown";
import { msgIn, orbPulse, riseIn, spring, stagger } from "./theme";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  ts?: number;
  thinking?: boolean;
  pending?: PendingAction;
  suggestions?: string[];
}

interface Activity {
  id: number;
  icon: string;
  label: string;
  detail?: string;
  done: boolean;
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

const TOOL_ICONS: Record<string, string> = {
  read_file: "📖", write_file: "✍️", append_file: "➕", search_files: "🔍",
  list_files: "🗂️", run_command: "⚙️", machine_ops: "🖥️", memory: "🧠",
  fetch_url: "🌐", edit_image: "🖼️", edit_video: "🎬", analyze_video: "🎥",
  generate_emoji: "😊", read_image: "👁️", default: "🔧",
};

function toolIcon(name?: string) {
  return TOOL_ICONS[name ?? ""] ?? TOOL_ICONS.default;
}

function activityRow(ev: ActivityEvent): { icon: string; label: string; detail?: string } {
  if (ev.event === "provider_selected") {
    return { icon: "🧠", label: `العقل: ${ev.provider ?? ""} ${ev.model ?? ""}`.trim() };
  }
  const args = ev.arguments ?? {};
  const detail =
    args.path ?? args.command ?? args.query ?? args.url ?? args.action ?? args.file ??
    (args.input ? String(args.input) : undefined);
  if (ev.event === "tool_requested") {
    return { icon: toolIcon(ev.tool), label: `تشغيل ${ev.tool ?? "أداة"}…`, detail: detail ? String(detail) : undefined };
  }
  return { icon: "✓", label: `${ev.tool ?? "أداة"} تمّت`, detail: detail ? String(detail) : undefined };
}

type SlashCommand = {
  cmd: string; icon: string; label: string; hint: string;
  kind: "insert" | "action"; value: string;
};

const SLASH_COMMANDS: SlashCommand[] = [
  { cmd: "usage", icon: "📖", label: "الاستخدام", hint: "اشرح لي كل ما تستطيع فعله", kind: "insert", value: "اشرح لي كل ما تستطيع فعله وكيف أستخدمك بأفضل شكل" },
  { cmd: "files", icon: "📁", label: "أضف ملفات", hint: "اعمل على ملف أو مسار محدد", kind: "insert", value: "اعمل على الملف/المسار التالي في مساحة العمل: " },
  { cmd: "skills", icon: "🧩", label: "المهارات", hint: "اعرض المهارات المتاحة", kind: "insert", value: "اعرض لي المهارات المتاحة واشرح متى تُستخدم كل واحدة" },
  { cmd: "design", icon: "🎨", label: "تصميم", hint: "صمّم واجهة أو شعاراً", kind: "insert", value: "صمّم لي: " },
  { cmd: "extract", icon: "📄", label: "استخراج", hint: "استخرج نصاً من صورة أو مستند", kind: "insert", value: "استخرج النص/المحتوى من الملف التالي: " },
  { cmd: "web", icon: "🌐", label: "بحث في الويب", hint: "ابحث ثم اقرأ أفضل نتيجة", kind: "insert", value: "ابحث في الويب عن: " },
  { cmd: "github", icon: "🐙", label: "GitHub", hint: "افتح لوحة GitHub", kind: "action", value: "github" },
  { cmd: "policy", icon: "🛡️", label: "سياسة التنفيذ", hint: "تلقائي / قوي / اسأل دائماً", kind: "action", value: "policy" },
  { cmd: "compact", icon: "🗜️", label: "اختصار المحادثة", hint: "لخّص الأقدم لتوفير السياق", kind: "action", value: "compact" },
  { cmd: "new", icon: "＋", label: "محادثة جديدة", hint: "ابدأ من الصفر", kind: "action", value: "new" },
];

function timeOf(ts?: number) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString("ar", { hour: "2-digit", minute: "2-digit" });
}

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [apiInput, setApiInput] = useState(getApiBase());
  const [tokenInput, setTokenInput] = useState(getToken());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [activeSid, setActiveSid] = useState<string>(() => localStorage.getItem("aali_sid") || "");
  const [toast, setToast] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [listening, setListening] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);
  const lastUserMessage = useRef<string>("");
  const abortRef = useRef<AbortController | null>(null);

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
    if (atBottom) chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, activities, atBottom]);

  useEffect(() => {
    localStorage.setItem("aali_policy", policy);
  }, [policy]);

  useEffect(() => {
    const saved = getGithubToken();
    if (!saved) return;
    fetchGithubUser(saved)
      .then((u) => setGhUser(u))
      .catch(() => setGithubToken(""));
  }, []);

  const showToast = useCallback((text: string) => {
    setToast(text);
    setTimeout(() => setToast(""), 1800);
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch {
      /* sidebar is best-effort */
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
    const t = setInterval(() => void refreshSessions(), 20000);
    return () => clearInterval(t);
  }, [refreshSessions]);

  // elapsed-seconds ticker while waiting
  useEffect(() => {
    if (!waiting) { setElapsed(0); return; }
    const started = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [waiting]);

  const send = useCallback(
    async (text: string, opts?: { confirm?: boolean }) => {
      const clean = text.trim();
      if (!clean || waiting) return;
      lastUserMessage.current = clean;
      setDraft("");
      setSlashOpen(false);
      setWaiting(true);
      setActivities([]);
      const thinkId = Date.now() + 1;
      const thinkMsg: Msg = { id: thinkId, role: "assistant", text: "", thinking: true, ts: Date.now() / 1000 };
      setMessages((m) => [
        ...(opts?.confirm ? m : [...m, { id: Date.now(), role: "user", text: clean, ts: Date.now() / 1000 } as Msg]),
        thinkMsg,
      ]);
      setAtBottom(true);
      const controller = new AbortController();
      abortRef.current = controller;
      let sawActivity = false;
      try {
        const data = await askStream(clean, {
          policy,
          confirm: opts?.confirm,
          signal: controller.signal,
          onActivity: (ev) => {
            sawActivity = true;
            const row = activityRow(ev);
            setActivities((a) => {
              const isResult = ev.event === "tool_result";
              const next = isResult
                ? a.map((x) => (x.label.startsWith(`تشغيل ${ev.tool ?? ""}`) ? { ...x, done: true, label: `${ev.tool} تمّت` } : x))
                : [...a, { id: Date.now() + Math.random(), icon: row.icon, label: row.label, detail: row.detail, done: false }];
              return next.slice(-5);
            });
          },
        });
        const reply = data.reply || data.error || "…";
        setMessages((m) =>
          m.map((x) =>
            x.id === thinkId
              ? {
                  ...x,
                  thinking: false,
                  text: reply,
                  pending: data.needs_confirm ? data.pending_action : undefined,
                  suggestions: data.suggestions?.slice(0, 3),
                }
              : x
          )
        );
        setConnected(data.ok);
        void refreshSessions();
      } catch (err) {
        const aborted = controller.signal.aborted;
        setMessages((m) =>
          m.map((x) =>
            x.id === thinkId
              ? {
                  ...x,
                  thinking: false,
                  text: aborted
                    ? "⏹ أوقفتَ عرض الرد — قد يظل الطلب يعمل في الخلفية وسيظهر في سجل الجلسة."
                    : sawActivity
                      ? "انقطع الاتصال أثناء العمل — راجع الجلسة من القائمة الجانبية."
                      : "تعذّر الاتصال بالخادم — تحقق من ⚙︎ الإعدادات",
                }
              : x
          )
        );
        if (!aborted) setConnected(false);
      } finally {
        setActivities([]);
        setWaiting(false);
        abortRef.current = null;
      }
    },
    [waiting, policy, refreshSessions]
  );

  const confirmPending = useCallback(() => {
    void send(lastUserMessage.current, { confirm: true });
  }, [send]);

  const newChat = () => {
    localStorage.removeItem("aali_sid");
    setMessages([]);
    setActiveSid("");
    setSidebarOpen(false);
  };

  const openSession = async (sid: string) => {
    try {
      const turns = await getSession(sid);
      const restored: Msg[] = turns
        .filter((t) => t.role === "user" || t.role === "assistant")
        .map((t, i) => ({
          id: i + Date.now(),
          role: t.role as "user" | "assistant",
          text: String(t.content ?? ""),
          ts: t.ts,
        }));
      setMessages(restored);
      setActiveSid(sid);
      localStorage.setItem("aali_sid", sid);
      setSidebarOpen(false);
    } catch {
      showToast("تعذّر فتح الجلسة");
    }
  };

  const removeSession = async (sid: string) => {
    await deleteSession(sid);
    if (sid === activeSid) newChat();
    void refreshSessions();
    showToast("حُذفت الجلسة");
  };

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
        setMessages((m) => [...m, { id: noteId, role: "assistant", text: "", thinking: true, ts: Date.now() / 1000 }]);
        void compactSession().then((res) => {
          const text = res.compacted
            ? `🗜️ تم اختصار المحادثة — بقيت ${res.kept_turns ?? ""} رسالة حديثة.\n\n${res.summary ?? ""}`
            : res.message || "لا حاجة للاختصار الآن.";
          setMessages((m) => m.map((x) => (x.id === noteId ? { ...x, thinking: false, text } : x)));
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

  const saveSettings = () => {
    setApiBase(apiInput.trim() || "http://127.0.0.1:5055");
    setToken(tokenInput.trim());
    localStorage.removeItem("aali_sid");
    setActiveSid("");
    setShowSettings(false);
    void ping();
    void refreshSessions();
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

  const startVoice = () => {
    type SR = { new (): SpeechRecognitionLike };
    const w = window as unknown as { SpeechRecognition?: SR; webkitSpeechRecognition?: SR };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) {
      showToast("المتصفح لا يدعم الإدخال الصوتي");
      return;
    }
    const rec = new Ctor();
    rec.lang = "ar";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onstart = () => setListening(true);
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    rec.onresult = (e: SpeechEventLike) => {
      const text = e.results?.[0]?.[0]?.transcript;
      if (text) setDraft((d) => (d ? d + " " + text : text));
    };
    rec.start();
  };

  const currentPolicy = POLICIES.find((p) => p.id === policy) ?? POLICIES[0];
  const onChatScroll = () => {
    const el = chatRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  const SlashMenu = slashOpen && slashMatches.length > 0 && (
    <div className="slash-menu">
      {slashMatches.map((c) => (
        <button key={c.cmd} type="button" className="slash-item" onClick={() => runSlashCommand(c)}>
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
        <button type="button" className="control-btn" onClick={() => setPolicyOpen((v) => !v)} title={currentPolicy.hint}>
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
                onClick={() => { setPolicy(p.id); setPolicyOpen(false); }}
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

  const ActivityPanel = waiting && (
    <div className="activity">
      {activities.map((a) => (
        <div key={a.id} className="activity-row">
          <span className={a.done ? "done-ico" : "spin"}>{a.icon}</span>
          <span className="activity-tool">{a.label}</span>
          {a.detail && <span className="activity-detail">{a.detail}</span>}
        </div>
      ))}
      <div className="activity-row">
        <span className="spin">✦</span>
        <span>آلي يعمل… {elapsed > 0 ? `${elapsed} ثانية` : ""}</span>
      </div>
    </div>
  );

  return (
    <div className="app">
      <div className="stars" aria-hidden="true" />

      {/* SIDEBAR — sessions */}
      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            className="sidebar-backdrop"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>
      <AnimatePresence>
        {sidebarOpen && (
          <motion.aside
            className="sidebar"
            initial={{ x: "-100%" }}
            animate={{ x: 0 }}
            exit={{ x: "-100%" }}
            transition={spring}
          >
            <div className="sidebar-head">
              <b>الجلسات</b>
              <button type="button" className="sidebar-new" onClick={newChat}>＋ جديدة</button>
            </div>
            <div className="sidebar-list">
              {sessions.length === 0 && (
                <p className="sidebar-empty">لا توجد جلسات محفوظة بعد.<br />ابدأ محادثة وستظهر هنا.</p>
              )}
              {sessions.map((s) => (
                <div key={s.sid} className={`sidebar-item ${s.sid === activeSid ? "active" : ""}`}>
                  <button
                    type="button"
                    className="sidebar-item-text"
                    style={{ all: "unset", cursor: "pointer", flex: 1, minWidth: 0 }}
                    onClick={() => void openSession(s.sid)}
                  >
                    <b>{s.title || "محادثة"}</b>
                    <small>
                      {s.turns} رسالة · {new Date(s.updated_at * 1000).toLocaleDateString("ar")}
                    </small>
                  </button>
                  <button type="button" className="sidebar-del" title="حذف الجلسة" onClick={() => void removeSession(s.sid)}>
                    🗑
                  </button>
                </div>
              ))}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      <motion.header className="topbar" initial={{ y: -30, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={spring}>
        <div className="brand">
          <button type="button" className="ghost-btn" title="الجلسات" onClick={() => setSidebarOpen(true)}>☰</button>
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
          {getSid() && <span className="pill">🔗 <span>جلسة مستمرة</span></span>}
        </div>
        <div className="top-actions">
          <button className="ghost-btn" title="الإعدادات" onClick={() => setShowSettings(true)}>⚙︎</button>
          <button className="ghost-btn" title="محادثة جديدة" onClick={newChat}>＋</button>
        </div>
      </motion.header>

      {/* HOME — hero */}
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
                  <button type="button" className={`mic-btn ${listening ? "listening" : ""}`} title="إدخال صوتي" onClick={startVoice}>🎙</button>
                  <button type="submit" className="send-btn" disabled={waiting || !draft.trim()} aria-label="إرسال">
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
            className="chat-wrap"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div ref={chatRef} className="chat" onScroll={onChatScroll}>
              {messages.map((m) => (
                <motion.div
                  key={m.id}
                  className={`msg ${m.role}`}
                  initial={msgIn.initial}
                  animate={msgIn.animate}
                >
                  <span className="who">
                    {m.role === "user" ? "أنت" : "آلي"}
                    {m.ts ? <span className="ts">{timeOf(m.ts)}</span> : null}
                    {m.role === "assistant" && !m.thinking && (
                      <button
                        type="button"
                        className="copy-btn"
                        title="نسخ الرد"
                        onClick={async () => {
                          try { await navigator.clipboard.writeText(m.text); showToast("تم النسخ ✓"); } catch { /* noop */ }
                        }}
                      >
                        ⧉
                      </button>
                    )}
                  </span>
                  {m.thinking ? <ThinkingOrbit /> : <Markdown text={m.text} />}
                  {m.thinking && <div style={{ marginTop: 10 }}>{ActivityPanel}</div>}
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
                  {!m.thinking && m.suggestions && m.suggestions.length > 0 && (
                    <div className="suggestions">
                      {m.suggestions.map((s) => (
                        <button key={s} type="button" className="chip" title={s} onClick={() => void send(s)}>
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </motion.div>
              ))}
              {!atBottom && (
                <button type="button" className="scroll-down" title="النزول للأسفل" onClick={() => chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" })}>
                  ↓
                </button>
              )}
            </div>
          </motion.main>
        )}
      </AnimatePresence>

      {/* DOCKED COMPOSER — chat mode */}
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
              {waiting ? (
                <button type="button" className="stop-btn" title="إيقاف العرض" onClick={() => abortRef.current?.abort()}>
                  ⏹
                </button>
              ) : (
                <button type="button" className={`mic-btn ${listening ? "listening" : ""}`} title="إدخال صوتي" onClick={startVoice}>🎙</button>
              )}
              <button type="submit" className="send-btn" disabled={waiting || !draft.trim()} aria-label="إرسال">
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
              من الجوال أو جهاز آخر أدخل عنوان الخادم، مثال: <code dir="ltr">http://192.168.1.10:5055</code>.
              إذا كان الخادم يعمل بنمط متعدد المستخدمين أدخل مفتاح الوصول أدناه.
            </small>
            <label style={{ marginTop: 12 }}>
              مفتاح الوصول (اختياري)
              <input
                type="password"
                dir="ltr"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="X-API-Key"
              />
            </label>
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

      {toast && <div className="toast">{toast}</div>}
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

/* minimal structural types for the Web Speech API (not in TS DOM lib) */
interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onresult: ((e: SpeechEventLike) => void) | null;
}
interface SpeechEventLike {
  results?: { [index: number]: { [index: number]: { transcript: string } } };
}
