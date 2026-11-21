import { useCallback, useEffect, useRef, useState } from "react";
import {
  FeedUnavailableError,
  clearSid,
  fetchPendingProposals,
  fetchProposal,
  health,
  initialSid,
  openWorkspaceFeed,
  resolveProposal,
  streamAsk,
} from "./api";
import ChatWindow from "./components/ChatWindow";
import ProposalModal from "./components/ProposalModal";
import SurfaceBanner from "./components/SurfaceBanner";
import WorkspaceSidebar from "./components/WorkspaceSidebar";
import type {
  ActivityEvent,
  ChatMessage,
  DoneResult,
  FeedStatus,
  Proposal,
  Surface,
  SurfaceEvent,
  WorkspaceEntry,
} from "./types";

type Tab = "chat" | "workspace";

let sequence = 0;
function uid(prefix: string): string {
  sequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${sequence}`;
}

function asSurface(value: unknown): Surface {
  return value === "studio" || value === "graphics" || value === "control" || value === "chat"
    ? value
    : "chat";
}

function surfaceFrom(value: unknown): SurfaceEvent | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { surface?: unknown; reason?: unknown };
  const surface = asSurface(candidate.surface);
  if (surface === "chat" && !candidate.reason) return null;
  return { surface, reason: String(candidate.reason || "") };
}

function proposalFrom(value: unknown): Proposal | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<Proposal>;
  if (!candidate.proposal_id || !candidate.path) return null;
  return {
    proposal_id: String(candidate.proposal_id),
    path: String(candidate.path),
    tool: String(candidate.tool || "edit"),
    status: candidate.status ? String(candidate.status) : "pending",
    diff: candidate.diff ? String(candidate.diff) : "",
    bytes: typeof candidate.bytes === "number" ? candidate.bytes : undefined,
    message: candidate.message ? String(candidate.message) : undefined,
    created: candidate.created ? String(candidate.created) : undefined,
  };
}

function toolCopy(name: string | undefined, args: Record<string, string> | undefined) {
  const tool = name || "";
  const detail =
    args?.path ||
    args?.command ||
    args?.query ||
    args?.url ||
    args?.pattern ||
    args?.name ||
    args?.input;

  const table: Record<string, { label: string; kind: WorkspaceEntry["kind"] }> = {
    read_file: { label: "قراءة ملف", kind: "file" },
    write_file: { label: "تعديل ملف", kind: "file" },
    append_file: { label: "إضافة إلى ملف", kind: "file" },
    replace_in_file: { label: "تعديل ملف", kind: "file" },
    list_files: { label: "استكشاف مجلد", kind: "file" },
    search_files: { label: "بحث في الملفات", kind: "file" },
    move_file: { label: "نقل ملف", kind: "file" },
    delete_file: { label: "حذف ملف", kind: "file" },
    run_command: { label: "تشغيل أمر", kind: "terminal" },
    machine_ops: { label: "عملية على النظام", kind: "terminal" },
    read_image: { label: "قراءة صورة", kind: "file" },
    generate_image: { label: "توليد صورة", kind: "surface" },
    edit_image: { label: "تحرير صورة", kind: "surface" },
    analyze_video: { label: "تحليل فيديو", kind: "file" },
    generate_video: { label: "توليد فيديو", kind: "surface" },
    fetch_url: { label: "قراءة رابط", kind: "file" },
    web_search: { label: "بحث في الويب", kind: "file" },
    memory: { label: "الذاكرة", kind: "brain" },
    switch_surface: { label: "توجيه الواجهة", kind: "surface" },
  };

  const entry = table[tool] || { label: "أداة", kind: "system" as const };
  return { ...entry, detail: detail ? String(detail) : undefined };
}

function isErrorResult(result: string | undefined): boolean {
  if (!result) return false;
  return /\[خطأ\]|traceback|exception|error:|failed|فشل/i.test(result);
}

function feedStatusLabel(status: FeedStatus): string {
  if (status === "live") return "بث مباشر";
  if (status === "connecting") return "اتصال…";
  return "بث الجلسة";
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveNote, setLiveNote] = useState("");
  const [workspace, setWorkspace] = useState<WorkspaceEntry[]>([]);
  const [feedStatus, setFeedStatus] = useState<FeedStatus>("connecting");
  const [surface, setSurface] = useState<SurfaceEvent | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");
  const [model, setModel] = useState<string>("local");
  const [tab, setTab] = useState<Tab>("chat");
  const [sid, setSid] = useState(initialSid);

  const abortRef = useRef<AbortController | null>(null);
  const feedStatusRef = useRef<FeedStatus>("connecting");
  const seenProposals = useRef<Set<string>>(new Set());
  const lastProposalRef = useRef<Proposal | null>(null);
  const sidRef = useRef(sid);

  useEffect(() => {
    sidRef.current = sid;
  }, [sid]);

  useEffect(() => {
    feedStatusRef.current = feedStatus;
  }, [feedStatus]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    let alive = true;
    const ping = () => {
      void health().then((ok) => {
        if (alive) setConnected(ok);
      });
    };
    ping();
    const timer = window.setInterval(ping, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const openProposal = useCallback(async (incoming: Proposal) => {
    lastProposalRef.current = incoming;
    setProposal(incoming);
    setProposalError("");
    if (!incoming.diff) {
      const full = await fetchProposal(incoming.proposal_id);
      if (full) {
        lastProposalRef.current = full;
        setProposal(full);
      }
    }
  }, []);

  const ingestProposal = useCallback(
    (incoming: Proposal | null) => {
      if (!incoming) return;
      const known = seenProposals.current.has(incoming.proposal_id);
      if (!known) {
        seenProposals.current.add(incoming.proposal_id);
        setPendingCount((count) => count + 1);
        setWorkspace((entries) => [
          ...entries.slice(-119),
          {
            id: uid("proposal"),
            kind: "proposal",
            label: "اقتراح تعديل جديد",
            detail: incoming.path,
            status: "pending",
            tool: incoming.tool,
            proposalId: incoming.proposal_id,
            ts: Date.now(),
          },
        ]);
      }
      void openProposal(incoming);
    },
    [openProposal],
  );

  const applyActivity = useCallback(
    (event: ActivityEvent) => {
      const name = event.event;
      if (name === "proposal_created") {
        ingestProposal(proposalFrom(event));
        return;
      }
      if (name === "surface_switch") {
        const next = surfaceFrom(event);
        if (next) {
          setSurface(next);
          setWorkspace((entries) => [
            ...entries.slice(-119),
            {
              id: uid("surface"),
              kind: "surface",
              label: "توجيه الواجهة",
              detail: next.reason || next.surface,
              status: "done",
              ts: Date.now(),
            },
          ]);
        }
        return;
      }
      if (name === "provider_selected") {
        setWorkspace((entries) => [
          ...entries.slice(-119),
          {
            id: uid("brain"),
            kind: "brain",
            label: "عقل آلي يعمل",
            status: "done",
            requestId: event.request_id,
            ts: Date.now(),
          },
        ]);
        return;
      }
      if (name !== "tool_requested" && name !== "tool_result") return;

      setWorkspace((entries) => {
        if (name === "tool_requested") {
          const copy = toolCopy(event.tool, event.arguments);
          return [
            ...entries.slice(-119),
            {
              id: uid("tool"),
              kind: copy.kind,
              label: copy.label,
              detail: copy.detail,
              status: "running",
              tool: event.tool,
              requestId: event.request_id,
              ts: Date.now(),
            },
          ];
        }

        const result = String(event.result || "");
        const error = isErrorResult(result);
        let index = -1;
        for (let i = entries.length - 1; i >= 0; i -= 1) {
          const entry = entries[i];
          if (
            entry.status === "running" &&
            (!event.tool || entry.tool === event.tool) &&
            (!event.request_id || !entry.requestId || entry.requestId === event.request_id)
          ) {
            index = i;
            break;
          }
        }
        if (index === -1) {
          const copy = toolCopy(event.tool, event.arguments);
          return [
            ...entries.slice(-119),
            {
              id: uid("tool"),
              kind: copy.kind,
              label: copy.label,
              detail: copy.detail,
              status: error ? "error" : "done",
              tool: event.tool,
              requestId: event.request_id,
              ts: Date.now(),
            },
          ];
        }
        const next = [...entries];
        next[index] = { ...next[index], status: error ? "error" : "done" };
        return next;
      });
    },
    [ingestProposal],
  );

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (cancelled) return;
      controller = new AbortController();
      setFeedStatus("connecting");
      void openWorkspaceFeed(
        controller.signal,
        (event) => applyActivity(event),
        () => setFeedStatus("live"),
      )
        .then(() => {
          if (cancelled) return;
          setFeedStatus("turn");
          retry = setTimeout(connect, 8000);
        })
        .catch((error: unknown) => {
          if (cancelled || controller?.signal.aborted) return;
          setFeedStatus("turn");
          const status = error instanceof FeedUnavailableError ? error.status : 0;
          // 401/403/404 is a stable authorization answer; retrying would only
          // spam the engine. A network/HTTP 5xx failure is retried.
          if (status !== 401 && status !== 403 && status !== 404) {
            retry = setTimeout(connect, 5000);
          }
        });
    };

    connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retry) clearTimeout(retry);
    };
  }, [applyActivity]);

  useEffect(() => {
    let alive = true;
    void fetchPendingProposals().then((proposals) => {
      if (!alive) return;
      setPendingCount(proposals.length);
      const latest = proposals[0];
      if (latest) {
        seenProposals.current.add(latest.proposal_id);
        void openProposal(latest);
      }
    });
    return () => {
      alive = false;
    };
  }, [openProposal]);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      setDraft("");
      setLiveNote("");
      setBusy(true);
      const userId = uid("user");
      const assistantId = uid("assistant");
      setMessages((current) => [
        ...current,
        { id: userId, role: "user", text: message, ts: Date.now() },
        { id: assistantId, role: "assistant", text: "", ts: Date.now(), pending: true },
      ]);
      setTab("chat");

      const controller = new AbortController();
      abortRef.current = controller;

      const updateAssistant = (patch: Partial<ChatMessage>) => {
        setMessages((current) =>
          current.map((item) => (item.id === assistantId ? { ...item, ...patch } : item)),
        );
      };

      try {
        const done: DoneResult = await streamAsk(message, {
          sid: sidRef.current || undefined,
          signal: controller.signal,
          model: model,
          onEvent: (eventName, payload) => {
            if (eventName === "activity") {
              if (feedStatusRef.current !== "live") {
                applyActivity(payload as ActivityEvent);
              }
            } else if (eventName === "proposal_created") {
              ingestProposal(proposalFrom(payload));
            } else if (eventName === "SURFACE_SWITCH") {
              const next = surfaceFrom(payload);
              if (next) {
                setSurface(next);
                applyActivity({
                  event: "surface_switch",
                  surface: next.surface,
                  reason: next.reason,
                });
              }
            } else if (eventName === "scratchpad") {
              const note = (payload as { content?: string }).content;
              if (note) setLiveNote(String(note));
            }
          },
        });

        if (done.sid) setSid(done.sid);
        if (done.proposal) ingestProposal(proposalFrom(done.proposal));
        const doneSurface = surfaceFrom(done.surface);
        if (doneSurface) setSurface(doneSurface);
        const reply = done.reply || done.error || "…";
        updateAssistant({
          pending: false,
          error: !done.ok,
          text: reply,
        });
        setConnected(done.ok);
        if (done.needs_confirm) {
          setNotice("طلب آلي تأكيداً قبل تنفيذ إجراء حساس.");
        }
        void fetchPendingProposals().then((proposals) => setPendingCount(proposals.length));
      } catch (error: unknown) {
        const aborted = controller.signal.aborted;
        const status = (error as { status?: number }).status;
        const serverError = (error as { serverError?: string }).serverError;
        updateAssistant({
          pending: false,
          error: !aborted,
          text: aborted
            ? "أوقفت الطلب."
            : serverError ||
              (status === 429
                ? "بلغت حد الرسائل المسموح لهذا المتصفح. أنشئ حساباً أو سجّل الدخول للمتابعة."
                : "تعذّر الاتصال بمحرك آلي. تأكد أن المحرك يعمل على http://localhost:5000."),
        });
        setConnected(false);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setBusy(false);
      }
    },
    [applyActivity, busy, ingestProposal],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setBusy(false);
  }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    clearSid();
    setSid("");
    setMessages([]);
    setDraft("");
    setLiveNote("");
    setSurface(null);
    setProposal(null);
    setProposalError("");
    setPendingCount(0);
    setWorkspace([]);
    setNotice("");
    setTab("chat");
  }, []);

  const copySessionLink = useCallback(async () => {
    const url = new URL(window.location.href);
    if (sidRef.current) url.searchParams.set("sid", sidRef.current);
    try {
      await navigator.clipboard.writeText(url.toString());
      setNotice("نُسخ رابط الجلسة — افتحه على هاتفك لمتابعة نفس المساحة.");
    } catch {
      setNotice("انسخ رابط الصفحة من شريط المتصفح لمشاركة الجلسة.");
    }
  }, []);

  const approve = useCallback(async () => {
    const current = lastProposalRef.current;
    if (!current || proposalBusy) return;
    setProposalBusy(true);
    setProposalError("");
    const result = await resolveProposal(current.proposal_id, true);
    setProposalBusy(false);
    if (!result.ok) {
      if (result.status === 409) {
        setProposalError("الملف تغيّر منذ إنشاء الاقتراح. لم يُكتب شيء — أعد الطلب على النسخة الحالية.");
      } else if (result.status === 404) {
        setProposalError("الموافقة متاحة من الجهاز المحلي أو بحساب إداري فقط.");
      } else {
        setProposalError(result.error || "تعذّر تطبيق الاقتراح.");
      }
      return;
    }
    setPendingCount((count) => Math.max(0, count - 1));
    setProposal(null);
    setNotice("تم تطبيق التعديل بعد فحص التعارض.");
  }, [proposalBusy]);

  const reject = useCallback(async () => {
    const current = lastProposalRef.current;
    if (!current || proposalBusy) return;
    setProposalBusy(true);
    setProposalError("");
    const result = await resolveProposal(current.proposal_id, false);
    setProposalBusy(false);
    if (!result.ok) {
      setProposalError(result.error || "تعذّر رفض الاقتراح.");
      return;
    }
    setPendingCount((count) => Math.max(0, count - 1));
    setProposal(null);
    setNotice("رُفض الاقتراح، ولم يُكتب أي ملف.");
  }, [proposalBusy]);

  return (
    <div className="app-shell" data-tab={tab}>
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">آ</div>
          <div>
            <div className="brand-name">آلي</div>
            <div className="brand-sub">AALI · LIVE ENGINE</div>
          </div>
        </div>

        <div className="topbar-status">
          <span className={`connection-pill ${connected ? "is-online" : connected === false ? "is-offline" : ""}`}>
            <span className="status-dot" />
            {connected === null ? "جارٍ الفحص…" : connected ? "المحرك متصل" : "المحرك غير متصل"}
          </span>
          <span className={`feed-pill feed-pill-${feedStatus}`}>
            <span className="status-dot" />
            {feedStatusLabel(feedStatus)}
          </span>
          <select
              style={{minWidth: 140, padding: '4px 8px', borderRadius: 6, border: '1px solid #e2e8f0', background: '#1e293b', color: '#f1f5f9', fontSize: '11px'}}
              onChange={(e) => setModel(e.target.value)}>
                <option value="local">Aali Local (HWK)</option>
                <option value="free">Aali Free (No Key)</option>
              </select>
          <button className="ghost-btn compact hide-small" type="button" onClick={copySessionLink}>
            نسخ رابط الجلسة
          </button>
          <button className="ghost-btn compact" type="button" onClick={newChat}>
            ＋ جديد
          </button>
        </div>
      </header>

      <SurfaceBanner surface={surface} onDismiss={() => setSurface(null)} />

      <nav className="mobile-tabs" aria-label="التنقل بين المحادثة ومساحة العمل">
        <button
          className={tab === "chat" ? "is-active" : ""}
          type="button"
          onClick={() => setTab("chat")}
        >
          <span aria-hidden="true">◌</span> المحادثة
        </button>
        <button
          className={tab === "workspace" ? "is-active" : ""}
          type="button"
          onClick={() => setTab("workspace")}
        >
          <span aria-hidden="true">▤</span> مساحة العمل
          {pendingCount > 0 && <em>{pendingCount}</em>}
        </button>
      </nav>

      <main className="content">
        <ChatWindow
          messages={messages}
          busy={busy}
          draft={draft}
          liveNote={liveNote}
          notice={notice}
          surfaceActive={!!surface && surface.surface !== "chat"}
          onDraft={setDraft}
          onSend={() => void send(draft)}
          onStop={stop}
          onNewChat={newChat}
          onClearNotice={() => setNotice("")}
        />
        <WorkspaceSidebar
          entries={workspace}
          feedStatus={feedStatus}
          pendingCount={pendingCount}
          onOpenProposal={() => {
            if (lastProposalRef.current) void openProposal(lastProposalRef.current);
          }}
          onClear={() => setWorkspace([])}
        />
      </main>

      {proposal && (
        <ProposalModal
          proposal={proposal}
          busy={proposalBusy}
          error={proposalError}
          onApprove={() => void approve()}
          onReject={() => void reject()}
          onClose={() => {
            if (!proposalBusy) setProposal(null);
          }}
        />
      )}
    </div>
  );
}
