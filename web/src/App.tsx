import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  askStream,
  attach,
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
  type Attachment,
  type PendingAction,
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
  files?: Attachment[]; // attachments the user sent with this message
}

interface Activity {
  id: number;
  icon: string;
  label: string;
  detail?: string;
  done: boolean;
  tool?: string;
  term?: boolean;
}

/* ————— brand guard —————
   آلي هو المنتج الوحيد الذي يظهر للمستخدم. أي اسم مزود آخر يصل من الخادم
   (نشاط/أخطاء/اقتراحات) يُستبدل بـ "آلي" قبل العرض — خارج كتل الأكواد
   حتى لا يُفسد أمثلة البرمجة. */
const PROVIDER_RE =
  /\b(claude|anthropic|openai|chatgpt|gpt-?\d*(?:\.?\d*)?|gemini|bard|deepseek|qwen|ollama|llama|mistral|grok|copilot|cursor|replit|kimi|moonshot|openrouter)\b/gi;

function maskProviders(chunk: string) {
  return chunk.replace(PROVIDER_RE, "آلي");
}
function maskOutsideCode(text: string) {
  return text
    .split(/(```[\s\S]*?```|`[^`]*`)/g)
    .map((chunk, i) => (i % 2 === 1 ? chunk : maskProviders(chunk)))
    .join("");
}

function toolIcon(name?: string) {
  const TOOL_ICONS: Record<string, string> = {
    read_file: "📖", write_file: "✍️", append_file: "➕", search_files: "🔍",
    list_files: "🗂️", run_command: "⚙️", machine_ops: "🖥️", memory: "🧠",
    fetch_url: "🌐", web_search: "🌐", edit_image: "🖼️", edit_video: "🎬",
    analyze_video: "🎥", generate_emoji: "😊", read_image: "👁️",
    generate_image: "🎨", make_n8n_workflow: "🧩", default: "🔧",
  };
  return TOOL_ICONS[name ?? ""] ?? TOOL_ICONS.default;
}

/* Human-friendly Arabic tool names instead of raw identifiers. */
function toolLabel(name?: string) {
  const LABELS: Record<string, string> = {
    read_file: "قراءة ملف", write_file: "كتابة ملف", append_file: "إضافة إلى ملف",
    search_files: "بحث في الملفات", list_files: "عرض الملفات",
    make_directory: "إنشاء مجلد", move_file: "نقل ملف", delete_file: "حذف ملف",
    run_command: "تنفيذ أمر", machine_ops: "عملية نظام", memory: "الذاكرة",
    fetch_url: "قراءة صفحة", web_search: "بحث في الويب",
    generate_image: "توليد صورة", edit_image: "تحرير صورة",
    generate_emoji: "إنشاء إيموجي", analyze_video: "تحليل فيديو",
    edit_video: "تحرير فيديو", read_image: "قراءة صورة",
    make_n8n_workflow: "بناء سير عمل n8n",
  };
  return LABELS[name ?? ""] ?? name ?? "أداة";
}

function activityRow(ev: ActivityEvent): {
  icon: string; label: string; doneLabel: string; detail?: string;
  term?: boolean; tool?: string;
} {
  // provider/model names never surface — the brain is always "آلي"
  if (ev.event === "provider_selected") {
    return { icon: "🧠", label: "العقل: آلي جاهز", doneLabel: "آلي جاهز" };
  }
  const args = ev.arguments ?? {};
  const detail =
    args.command ?? args.path ?? args.query ?? args.url ?? args.action ?? args.file ??
    (args.input ? String(args.input) : undefined);
  const label = toolLabel(ev.tool);
  const term = ev.tool === "run_command"; // terminal-style row, like coding agents
  if (ev.event === "tool_requested") {
    return {
      icon: term ? ">_" : toolIcon(ev.tool),
      label: `${label}…`, doneLabel: `${label} ✓`,
      detail: detail ? String(detail) : undefined,
      term, tool: ev.tool,
    };
  }
  return {
    icon: "✓", label: `${label} ✓`, doneLabel: `${label} ✓`,
    detail: detail ? String(detail) : undefined, term, tool: ev.tool,
  };
}

/* Monotonic message ids — Date.now() can collide within the same millisecond,
   which once made the reply-update overwrite the user message. */
let _nextMsgId = 1;
function nextMsgId() {
  return _nextMsgId++;
}

/* ————— theme: dark (default) / sepia — per-browser via localStorage,
   applied as <html data-theme> so every color flows from CSS vars. ————— */
type ThemeName = "dark" | "sepia";
const THEME_KEY = "aali_theme";
const THEMES: { id: ThemeName; icon: string; label: string }[] = [
  { id: "dark", icon: "☾", label: "داكن" },
  { id: "sepia", icon: "◕", label: "سيبيا" },
];

function loadTheme(): ThemeName {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === "sepia" ? "sepia" : "dark";
}

/* greeting follows the clock: morning (5–12), afternoon (12–17), evening else */
function greetingFor(now = new Date()): string {
  const h = now.getHours();
  if (h >= 5 && h < 12) return "صباح الخير";
  if (h >= 12 && h < 17) return "نهارك سعيد";
  return "مساء الخير";
}

function applyTheme(t: ThemeName) {
  if (t === "sepia") document.documentElement.setAttribute("data-theme", "sepia");
  else document.documentElement.removeAttribute("data-theme");
}

/* ————— sidebar nav (Aali-flavored mirror of the reference layout) ————— */
type NavItem = {
  icon: string;
  label: string;
  prompt?: string;
  action?: "github" | "policy";
  badge?: string;
  children?: { icon: string; label: string; prompt: string }[];
};

const NAV_ITEMS: NavItem[] = [
  { icon: "📁", label: "الملفات", prompt: "اعرض ملفات مجلد العمل ولخّص لي محتواها وبنيتها" },
  { icon: "🛠️", label: "ابنِ تطبيقاً", prompt: "أنشئ تطبيق ويب بسيط لعرض الأذكار اليومية ثم شغّله" },
  { icon: "🌐", label: "بحث ويب", prompt: "ابحث في الويب عن آخر مستجدات الذكاء الاصطناعي ولخّصها" },
  { icon: "📄", label: "المستندات", prompt: "اقرأ ملف PDF في مجلد العمل ولخّص أهم النقاط بالعربية" },
  {
    icon: "🖼️", label: "الوسائط",
    children: [
      { icon: "👁️", label: "اقرأ صورة", prompt: "اقرأ الصورة الموجودة في مجلد العمل واستخرج النص منها" },
      { icon: "🎨", label: "حرّر صورة", prompt: "حرّر صورة: صحّح الألوان واجعلها بأسلوب غروب دافئ" },
      { icon: "🎬", label: "حلّل فيديو", prompt: "لخّص لي فيديو في مجلد العمل: ما النص الظاهر وما المحتوى المنطوق؟" },
    ],
  },
  { icon: "🧩", label: "سير عمل n8n", badge: "تجريبي", prompt: "اصنع لي سير عمل n8n: عند وصول بريد جديد أرسل ملخصه إلى تيليجرام" },
  { icon: "⬇️", label: "التنزيلات" },
  { icon: "🐙", label: "GitHub", action: "github" },
  { icon: "🛡️", label: "سياسة التنفيذ", action: "policy" },
];

const QUICK_ACTIONS = [
  { icon: "🛠️", label: "تطبيق", prompt: "أنشئ تطبيق ويب بسيط لعرض الأذكار اليومية ثم شغّله" },
  { icon: "🧩", label: "سير عمل", prompt: "اصنع لي سير عمل n8n: عند وصول بريد جديد أرسل ملخصه إلى تيليجرام" },
  { icon: "👁️", label: "اقرأ صورة", prompt: "اقرأ الصورة الموجودة في مجلد العمل واستخرج النص منها" },
  { icon: "🎬", label: "فيديو", prompt: "لخّص لي فيديو في مجلد العمل: ما النص الظاهر وما المحتوى المنطوق؟" },
  { icon: "🎨", label: "تحرير صورة", prompt: "حرّر صورة: صحّح الألوان واجعلها بأسلوب غروب دافئ" },
  { icon: "📄", label: "مستند", prompt: "اقرأ ملف PDF في مجلد العمل ولخّص أهم النقاط بالعربية" },
];

const POLICIES: { id: Policy; label: string; icon: string; hint: string }[] = [
  { id: "auto", label: "تلقائي", icon: "⚡", hint: "ينفّذ الطلبات مباشرة دون توقف" },
  { id: "aggressive", label: "قوي", icon: "🚀", hint: "ينجز أسرع طريقة ممكنة بأقل أسئلة" },
  { id: "always_ask", label: "اسأل دائماً", icon: "🛡️", hint: "يوقف أي إجراء خطير حتى تؤكد" },
];

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
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [activeSid, setActiveSid] = useState<string>(() => localStorage.getItem("aali_sid") || "");
  const [toast, setToast] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [listening, setListening] = useState(false);
  const [navOpen, setNavOpen] = useState(false); // sidebar as overlay on small screens
  const [railCollapsed, setRailCollapsed] = useState(() => localStorage.getItem("aali_rail") === "1");
  const [mediaOpen, setMediaOpen] = useState(false);
  const [files, setFiles] = useState<Attachment[]>([]); // composer attachments
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [theme, setTheme] = useState<ThemeName>(() => {
    const t = loadTheme();
    applyTheme(t);
    return t;
  });
  const chatRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
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
    localStorage.setItem("aali_rail", railCollapsed ? "1" : "0");
  }, [railCollapsed]);

  const switchTheme = useCallback((t: ThemeName) => {
    setTheme(t);
    applyTheme(t);
    localStorage.setItem(THEME_KEY, t);
  }, []);

  useEffect(() => {
    const saved = getGithubToken();
    if (!saved) return;
    fetchGithubUser(saved)
      .then((u) => setGhUser(u))
      .catch(() => setGithubToken(""));
  }, []);

  /* Ctrl/⌘ + K — focus the composer (matches the kbd hint on the New Chat button) */
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
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

  // auto-grow the composer textarea; scrollbar stays hidden until the cap
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const cap = Math.round(window.innerHeight * 0.35);
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, cap)}px`;
    el.style.overflowY = el.scrollHeight > cap ? "auto" : "hidden";
  }, [draft, chatMode]);

  // elapsed-seconds ticker while waiting
  useEffect(() => {
    if (!waiting) { setElapsed(0); return; }
    const started = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [waiting]);

  const send = useCallback(
    async (text: string, opts?: { confirm?: boolean; withFiles?: Attachment[] }) => {
      const clean = text.trim();
      const withFiles = opts?.withFiles;
      if ((!clean && !(withFiles && withFiles.length)) || waiting) return;
      const outgoing = withFiles && withFiles.length ? withFiles : undefined;
      const sendText = clean || (outgoing ? "حلّل الملفات المرفقة وحاول إفادتي منها." : "");
      lastUserMessage.current = sendText;
      setDraft("");
      setFiles([]);
      setSlashOpen(false);
      setWaiting(true);
      setActivities([]);
      const uid = nextMsgId();
      const thinkId = nextMsgId();
      const thinkMsg: Msg = { id: thinkId, role: "assistant", text: "", thinking: true, ts: Date.now() / 1000 };
      setMessages((m) => [
        ...(opts?.confirm ? m : [...m, { id: uid, role: "user", text: sendText, ts: Date.now() / 1000, files: outgoing } as Msg]),
        thinkMsg,
      ]);
      setAtBottom(true);
      const controller = new AbortController();
      abortRef.current = controller;
      let sawActivity = false;
      try {
        const data = await askStream(sendText, {
          policy,
          confirm: opts?.confirm,
          attachments: outgoing?.map((f) => f.stored),
          signal: controller.signal,
          onActivity: (ev) => {
            sawActivity = true;
            const row = activityRow(ev);
            setActivities((a) => {
              if (ev.event === "tool_result") {
                return a.map((x) =>
                  x.tool === ev.tool && !x.done
                    ? { ...x, done: true, label: maskProviders(row.doneLabel) }
                    : x
                );
              }
              return [
                ...a,
                {
                  id: nextMsgId() + Math.random(),
                  icon: row.icon,
                  label: maskProviders(row.label),
                  detail: row.detail ? maskProviders(row.detail) : undefined,
                  done: false,
                  tool: row.tool,
                  term: row.term,
                },
              ];
            });
            return undefined;
          },
        });
        const reply = maskOutsideCode(data.reply || data.error || "…");
        setMessages((m) =>
          m.map((x) =>
            x.id === thinkId
              ? {
                  ...x,
                  thinking: false,
                  text: reply,
                  pending: data.needs_confirm ? data.pending_action : undefined,
                  suggestions: data.suggestions?.slice(0, 3).map(maskProviders),
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

  /* — attachments: the picker opens the OS dialog (PC folders on desktop,
     phone files/camera on mobile); images preview locally, all kinds upload
     immediately and Aali analyzes them server-side (OCR/parser/Whisper). — */
  const pickFiles = () => fileRef.current?.click();
  const onFilesChosen = async (list: FileList | null) => {
    if (!list || !list.length) return;
    setUploading(true);
    try {
      for (const f of Array.from(list).slice(0, 4)) {
        const att: Attachment = await attach(f);
        if (f.type.startsWith("image/")) att.preview = URL.createObjectURL(f);
        setFiles((cur) => [...cur, att]);
      }
    } catch (err) {
      setToast(`فشل رفع الملف: ${err instanceof Error ? err.message : ""}`);
      setTimeout(() => setToast(""), 4000);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const removeFile = (stored: string) =>
    setFiles((cur) => cur.filter((f) => f.stored !== stored));

  const newChat = () => {
    localStorage.removeItem("aali_sid");
    setMessages([]);
    setActiveSid("");
    setNavOpen(false);
    setTimeout(() => inputRef.current?.focus(), 60);
  };

  const openSession = async (sid: string) => {
    try {
      const turns = await getSession(sid);
      const restored: Msg[] = turns
        .filter((t) => t.role === "user" || t.role === "assistant")
        .map((t) => ({
          id: nextMsgId(),
          role: t.role as "user" | "assistant",
          text: String(t.content ?? ""),
          ts: t.ts,
        }));
      setMessages(restored);
      setActiveSid(sid);
      localStorage.setItem("aali_sid", sid);
      setNavOpen(false);
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
      inputRef.current?.focus();
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
        const noteId = nextMsgId();
        setMessages((m) => [...m, { id: noteId, role: "assistant", text: "", thinking: true, ts: Date.now() / 1000 }]);
        void compactSession().then((res) => {
          const text = res.compacted
            ? `🗜️ تم اختصار المحادثة — بقيت ${res.kept_turns ?? ""} رسالة حديثة.\n\n${res.summary ?? ""}`
            : res.message || "لا حاجة للاختصار الآن.";
          setMessages((m) => m.map((x) => (x.id === noteId ? { ...x, thinking: false, text: maskOutsideCode(text) } : x)));
        });
        break;
      }
    }
  };

  const onDraftChange = (value: string) => {
    setDraft(value);
    setSlashOpen(value.startsWith("/"));
  };

  /* Deep link: /ui/?sid=<id> restores that conversation on load (used by
     the sessions sidebar "share link" and by screenshot automation). */
  const deepSid = useMemo(() => new URLSearchParams(window.location.search).get("sid") || "", []);
  useEffect(() => {
    if (!deepSid) return;
    localStorage.setItem("aali_sid", deepSid);
    void openSession(deepSid);
  }, [deepSid]);

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

  // suggestion chips belong only under the latest assistant reply
  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;

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

  /* ————— sidebar ————— */
  const Sidebar = (
    <aside className={`sidebar ${railCollapsed ? "rail" : ""} ${navOpen ? "open" : ""}`}>
      <div className="side-head">
        <div className="logo" title="آلي">HWK</div>
        <button
          type="button"
          className="side-toggle"
          title={railCollapsed ? "توسيع القائمة" : "تصغير القائمة"}
          onClick={() => setRailCollapsed((v) => !v)}
        >
          {railCollapsed ? "⇥" : "⇤"}
        </button>
      </div>

      {railCollapsed ? (
        <div className="rail-col">
          <button type="button" className="rail-btn" title="محادثة جديدة (Ctrl K)" onClick={newChat}>＋</button>
          <button type="button" className="rail-btn" title="الجلسات" onClick={() => setNavOpen(true)}>☰</button>
          <button type="button" className="rail-btn" title="GitHub" onClick={() => void openGithubPanel()}>🐙</button>
          <button type="button" className="rail-btn" title="الإعدادات" onClick={() => setShowSettings(true)}>⚙︎</button>
        </div>
      ) : (
        <>
          <button type="button" className="new-chat" onClick={newChat}>
            <span className="nc-plus">＋</span>
            <span className="nc-label">محادثة جديدة</span>
            <kbd>Ctrl K</kbd>
          </button>

          <nav className="side-nav">
            {NAV_ITEMS.map((item) =>
              item.children ? (
                <div key={item.label} className={`nav-group ${mediaOpen ? "open" : ""}`}>
                  <button
                    type="button"
                    className="nav-item"
                    onClick={() => setMediaOpen((v) => !v)}
                  >
                    <span className="nav-ico">{item.icon}</span>
                    <span className="nav-label">{item.label}</span>
                    <span className="nav-caret">{mediaOpen ? "⌃" : "⌄"}</span>
                  </button>
                  {mediaOpen && (
                    <div className="nav-sub">
                      {item.children.map((c) => (
                        <button key={c.label} type="button" className="nav-item sub" onClick={() => void send(c.prompt)}>
                          <span className="nav-ico">{c.icon}</span>
                          <span className="nav-label">{c.label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <button
                  key={item.label}
                  type="button"
                  className="nav-item"
                  title={item.prompt}
                  onClick={() => {
                    if (item.action === "github") void openGithubPanel();
                    else if (item.action === "policy") setPolicyOpen(true);
                    else if (item.label === "التنزيلات") window.open(getApiBase().replace(/\/+$/, "") + "/download", "_blank");
                    else if (item.prompt) void send(item.prompt);
                  }}
                >
                  <span className="nav-ico">{item.icon}</span>
                  <span className="nav-label">{item.label}</span>
                  {item.badge && <span className="beta-badge">{item.badge}</span>}
                </button>
              )
            )}
          </nav>

          <div className="side-section">
            <span>المحادثات</span>
            <button type="button" className="side-more" title="تحديث" onClick={() => void refreshSessions()}>⟳</button>
          </div>
          <div className="chat-list">
            {sessions.length === 0 && (
              <p className="side-empty">لا توجد جلسات محفوظة بعد.</p>
            )}
            {sessions.map((s) => (
              <div key={s.sid} className={`chat-row ${s.sid === activeSid ? "active" : ""}`}>
                <button type="button" className="chat-row-btn" onClick={() => void openSession(s.sid)} title={s.title || "محادثة"}>
                  <span className="chat-row-title">{s.title || "محادثة"}</span>
                  <span className="chat-row-meta">{s.turns} رسالة · {new Date(s.updated_at * 1000).toLocaleDateString("ar")}</span>
                </button>
                <button type="button" className="chat-row-del" title="حذف الجلسة" onClick={() => void removeSession(s.sid)}>🗑</button>
              </div>
            ))}
          </div>

          <button type="button" className="promo-card" onClick={() => showToast("قريباً — شارك آلي مع أصدقائك ✦")}>
            <span className="promo-text">
              <b>ادعُ صديقاً</b>
              <small>اربح شهراً من آلي+ لكل صديق</small>
            </span>
            <span className="promo-arrow">↗</span>
          </button>

          <div className="profile-row">
            <span className="profile-avatar">أ</span>
            <span className="profile-text">
              <b>مستخدم آلي</b>
              <small className={connected === null ? "" : connected ? "ok" : "bad"}>
                {connected === null ? "…" : connected ? "متصل" : "غير متصل"}
              </small>
            </span>
            <button type="button" className="upgrade-badge" onClick={() => showToast("آلي+ قريباً ✦")}>ترقية</button>
            <button
              type="button"
              className="profile-act"
              title="إنشاء مفتاح جديد"
              onClick={() => { setNavOpen(false); window.open(getApiBase().replace(/\/+$/, "") + "/signup", "_blank"); }}
            >
              🔑
            </button>
            <button type="button" className="profile-act" title="الإعدادات" onClick={() => setShowSettings(true)}>⚙︎</button>
          </div>
        </>
      )}
    </aside>
  );

  const backdrop = navOpen && (
    <motion.div
      className="sidebar-backdrop"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      onClick={() => setNavOpen(false)}
    />
  );

  return (
    <div className="app">
      <div className="stars" aria-hidden="true" />

      {Sidebar}
      <AnimatePresence>{backdrop}</AnimatePresence>

      <div className="main">
        {!chatMode ? (
          /* ————— HOME WORKSPACE ————— */
          <section className="home">
            <div className="home-top">
              <button type="button" className="rail-btn only-mobile" title="القائمة" onClick={() => setNavOpen(true)}>☰</button>
              <motion.button
                type="button"
                className="upgrade-pill"
                animate={orbPulse}
                onClick={() => showToast("آلي+ قريباً ✦")}
              >
                <span>✦</span> طوّر خطتك
              </motion.button>
              <span className="home-top-spacer" />
              <div className="theme-toggle" role="group" aria-label="المظهر">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={theme === t.id ? "active" : ""}
                    title={`مظهر ${t.label}`}
                    onClick={() => switchTheme(t.id)}
                  >
                    {t.icon}
                  </button>
                ))}
              </div>
            </div>

            <div className="hero">
              <motion.div className="empty-state" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...spring, delay: 0.02 }}>
                <span className="es-orb" aria-hidden="true">✦</span>
                <span className="es-title">{greetingFor()} — كيف أساعدك اليوم؟</span>
                <span className="es-sub">اكتب طلبك، أو جرّب أحد الاقتراحات بالأسفل</span>
              </motion.div>
              <motion.h1 className="wordmark" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
                آلي
              </motion.h1>
              <motion.p className="tagline" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...spring, delay: 0.06 }}>
                مساعدك الشخصي — <em>عربي أولاً</em>، بأدوات حقيقية وذاكرة دائمة
              </motion.p>

              <motion.form
                className="ask-box"
                initial={{ opacity: 0, y: 18, scale: 0.99 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ ...spring, delay: 0.08 }}
                onSubmit={(e) => { e.preventDefault(); void send(draft); }}
              >
                {SlashMenu}
                <textarea
                  id="heroInput"
                  ref={inputRef}
                  rows={2}
                  style={{ overflowY: "hidden" }}
                  placeholder="اسأل عن أي شيء، أو كلّف الوكيل بمهمة…"
                  value={draft}
                  onChange={(e) => onDraftChange(e.target.value)}
                  onKeyDown={onKey}
                />
                <div className="ask-row">
                  <button
                    type="button"
                    className="ask-plus"
                    title="أوامر سريعة (/)"
                    onClick={() => { setDraft("/"); setSlashOpen(true); inputRef.current?.focus(); }}
                  >
                    ＋
                  </button>
                  <div className="ask-right">
                    <div className="model-select-wrap">
                      <button type="button" className="model-select" onClick={() => setPolicyOpen((v) => !v)} title={currentPolicy.hint}>
                        <span>{currentPolicy.icon}</span>
                        <span>آلي {currentPolicy.label}</span>
                        <span className="ms-caret">▾</span>
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
                                <b>آلي {p.label}</b>
                                <small>{p.hint}</small>
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <button type="submit" className="send-round" disabled={waiting || !draft.trim()} aria-label="إرسال">
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
                    </button>
                  </div>
                </div>
              </motion.form>

              <motion.div className="quick-pills" variants={stagger} initial="hidden" animate="show">
                {QUICK_ACTIONS.map((a) => (
                  <motion.button
                    key={a.label}
                    type="button"
                    className="quick-pill"
                    variants={riseIn}
                    whileHover={{ y: -2, borderColor: "rgba(232,179,75,.45)" }}
                    whileTap={{ scale: 0.97 }}
                    onClick={() => void send(a.prompt)}
                  >
                    <span className="qp-ico">{a.icon}</span>
                    {a.label}
                  </motion.button>
                ))}
                <motion.button
                  type="button"
                  className="quick-pill"
                  variants={riseIn}
                  whileHover={{ y: -2, borderColor: "rgba(232,179,75,.45)" }}
                  onClick={() => startVoice()}
                  title="إدخال صوتي"
                >
                  <span className={`qp-ico ${listening ? "listening" : ""}`}>🎙</span>
                  صوت
                </motion.button>
              </motion.div>
            </div>

            <footer className="home-foot">
              <button type="button" className="foot-link" onClick={() => void send("اقترح لي أفكاراً مشاريع أستطيع بناءها معك اليوم")}>
                استكشف الإلهام
              </button>
              <span className="foot-indicator">
                مرّر للاستكشاف
                <span className="foot-arrows" aria-hidden="true">⌃<br />⌃</span>
              </span>
            </footer>
          </section>
        ) : (
          /* ————— CHAT WORKSPACE ————— */
          <motion.main
            className="chatpane"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <header className="chat-head">
              <button type="button" className="rail-btn only-mobile" title="القائمة" onClick={() => setNavOpen(true)}>☰</button>
              <span className="model-chip" title={currentPolicy.hint}>
                <span className="mc-star">✦</span> آلي
                <span className="mc-mode">{currentPolicy.label}</span>
              </span>
              <span className={`conn-dot ${connected === null ? "" : connected ? "ok" : "bad"}`} title={connected ? "متصل" : "غير متصل"} />
              <div className="chat-head-actions">
                <div className="theme-toggle" role="group" aria-label="المظهر">
                  {THEMES.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={theme === t.id ? "active" : ""}
                      title={`${t.label} — ${t.id === "dark" ? "الليل" : "ورق دافئ"}`}
                      onClick={() => switchTheme(t.id)}
                    >
                      {t.icon}
                    </button>
                  ))}
                </div>
                <button type="button" className="ghost-btn" title="الإعدادات" onClick={() => setShowSettings(true)}>⚙︎</button>
                <button type="button" className="ghost-btn" title="محادثة جديدة (Ctrl K)" onClick={newChat}>＋</button>
              </div>
            </header>

            <div ref={chatRef} className="chat" onScroll={onChatScroll}>
              {messages.map((m) => (
                <motion.div
                  key={m.id}
                  className={`msg ${m.role}`}
                  initial={msgIn.initial}
                  animate={msgIn.animate}
                >
                  <span className="avatar" aria-hidden="true">{m.role === "user" ? "👤" : <span className="avatar-hwk">HWK</span>}</span>
                  <div className="msg-main">
                    <div className="body">
                      <span className="who">
                        {m.role === "user" ? "أنت" : "آلي"}
                        {m.ts ? <span className="ts">{timeOf(m.ts)}</span> : null}
                      </span>
                      {m.files && m.files.length > 0 && (
                        <div className="msg-files">
                          {m.files.map((f) =>
                            f.preview ? (
                              <a key={f.stored} href={`${getApiBase()}/api/file/uploads/${encodeURIComponent(f.stored)}`} target="_blank" rel="noreferrer">
                                <img src={f.preview} alt={f.name} className="msg-img" />
                              </a>
                            ) : (
                              <a key={f.stored} className="attach-chip" href={`${getApiBase()}/api/file/uploads/${encodeURIComponent(f.stored)}`} target="_blank" rel="noreferrer">
                                <span className="attach-ico">{{ video: "🎬", audio: "🎵", document: "📄", text: "📝", binary: "📦" }[f.kind] ?? "📎"}</span>
                                <em>{f.name}</em>
                              </a>
                            )
                          )}
                        </div>
                      )}
                      {m.thinking ? <ThinkingOrbit /> : <Markdown text={m.text} />}
                      {m.thinking && <div style={{ marginTop: 10 }}>{ActivityPanel}</div>}
                      {!m.thinking && m.role === "assistant" && (
                        <div className="msg-actions">
                          <button
                            type="button"
                            title="نسخ الرد"
                            onClick={async () => {
                              try { await navigator.clipboard.writeText(m.text); showToast("تم النسخ ✓"); } catch { /* noop */ }
                            }}
                          >
                            ⧉
                          </button>
                          <button
                            type="button"
                            title="إعادة التوليد"
                            disabled={waiting}
                            onClick={() => {
                              const lastUser = [...messages].reverse().find((x) => x.role === "user");
                              if (lastUser) void send(lastUser.text);
                            }}
                          >
                            ↻
                          </button>
                        </div>
                      )}
                    </div>
                    {m.pending && (
                      <div className="confirm-bar" style={{ width: "100%" }}>
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
                    {!m.thinking && m.role === "assistant" && m.id === lastAssistantId && m.suggestions && m.suggestions.length > 0 && (
                      <div className="suggestions">
                        {m.suggestions.map((s) => (
                          <button key={s} type="button" className="chip" title={s} onClick={() => void send(s)}>
                            {s}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
              {!atBottom && (
                <button type="button" className="scroll-down" title="النزول للأسفل" onClick={() => chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" })}>
                  ↓
                </button>
              )}
            </div>

            <footer className="composer">
              <input
                ref={fileRef}
                type="file"
                multiple
                accept="image/*,video/*,audio/*,.pdf,.docx,.xlsx,.txt,.md,.csv,.json,.py,.js,.ts,.tsx,.html,.css,.zip"
                style={{ display: "none" }}
                onChange={(e) => void onFilesChosen(e.target.files)}
              />
              {(files.length > 0 || uploading) && (
                <div className="attach-row">
                  {uploading && <span className="attach-chip uploading">⏳ جارٍ الرفع والتحليل…</span>}
                  {files.map((f) => (
                    <span key={f.stored} className="attach-chip" title={f.name}>
                      {f.preview
                        ? <img src={f.preview} alt="" className="attach-thumb" />
                        : <span className="attach-ico">{{ video: "🎬", audio: "🎵", document: "📄", text: "📝", binary: "📦" }[f.kind] ?? "📎"}</span>}
                      <em>{f.name.length > 26 ? f.name.slice(0, 24) + "…" : f.name}</em>
                      <button type="button" className="attach-x" title="إزالة" onClick={() => removeFile(f.stored)}>×</button>
                    </span>
                  ))}
                </div>
              )}
              <form
                onSubmit={(e) => { e.preventDefault(); void send(draft, files.length ? { withFiles: files } : undefined); }}
                className="composer-form"
              >
                {SlashMenu}
                <button type="button" className="attach-btn" title="إرفاق ملف أو صورة أو فيديو" onClick={pickFiles} disabled={waiting || uploading}>📎</button>
                <textarea
                  ref={inputRef}
                  rows={1}
                  placeholder="تابع الحديث مع آلي… (Enter للإرسال، / للأوامر)"
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
                <button type="submit" className="send-btn" disabled={waiting || uploading || (!draft.trim() && !files.length)} aria-label="إرسال">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
                </button>
              </form>
              <p className="hint">
                يتصل بـ <code>{getApiBase()}</code> — غيّره من ⚙︎ الإعدادات
              </p>
            </footer>
          </motion.main>
        )}
      </div>

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
              المظهر
              <div className="theme-toggle" style={{ marginTop: 6 }} role="group" aria-label="المظهر">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={theme === t.id ? "active" : ""}
                    onClick={() => switchTheme(t.id)}
                  >
                    {t.icon} {t.label}
                  </button>
                ))}
              </div>
            </label>
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
