import type { FeedStatus, WorkspaceEntry } from "../types";

interface Props {
  entries: WorkspaceEntry[];
  feedStatus: FeedStatus;
  pendingCount: number;
  onOpenProposal: () => void;
  onClear: () => void;
}

const ICONS: Record<WorkspaceEntry["kind"], string> = {
  file: "▤",
  terminal: "›_",
  proposal: "◆",
  surface: "◇",
  brain: "✦",
  system: "·",
};

const STATUS_LABELS: Record<WorkspaceEntry["status"], string> = {
  running: "قيد التنفيذ",
  done: "تم",
  error: "خطأ",
  pending: "بانتظار المراجعة",
};

function timeOf(ts: number): string {
  return new Date(ts).toLocaleTimeString("ar", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function feedLabel(status: FeedStatus): string {
  if (status === "live") return "بث عام مباشر · الكمبيوتر متصل";
  if (status === "connecting") return "جارٍ الاتصال بالبث المباشر…";
  return "بث الجلسة · البث العام غير متاح";
}

export default function WorkspaceSidebar({
  entries,
  feedStatus,
  pendingCount,
  onOpenProposal,
  onClear,
}: Props) {
  return (
    <aside className="workspace-pane" aria-label="مساحة العمل الحية">
      <header className="panel-head">
        <div>
          <span className="eyebrow">LIVE WORKSPACE</span>
          <h2>ما يفعله آلي الآن</h2>
        </div>
        <button className="ghost-btn compact" type="button" onClick={onClear}>
          مسح
        </button>
      </header>

      <div className={`feed-status feed-${feedStatus}`} role="status">
        <span className="status-dot" />
        <span>{feedLabel(feedStatus)}</span>
        <span className="feed-pulse" aria-hidden="true" />
      </div>

      {pendingCount > 0 && (
        <button className="proposal-strip" type="button" onClick={onOpenProposal}>
          <span className="proposal-strip-icon">◆</span>
          <span>
            <b>{pendingCount} اقتراح تعديل بانتظارك</b>
            <small>راجع الفرق قبل أن يُكتب أي ملف</small>
          </span>
          <span className="proposal-strip-arrow">←</span>
        </button>
      )}

      <div className="workspace-feed" aria-live="polite">
        {entries.length === 0 ? (
          <div className="feed-empty">
            <div className="feed-empty-orbit" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <b>لا نشاط بعد</b>
            <p>
              أرسل طلباً من المحادثة. ستظهر هنا الملفات التي يقرأها أو يعدّلها آلي،
              والأوامر التي يشغّلها على الكمبيوتر — لحظة حدوثها.
            </p>
          </div>
        ) : (
          entries.map((entry) => (
            <article
              className={`feed-entry feed-${entry.kind} is-${entry.status}`}
              key={entry.id}
            >
              <div className="feed-entry-icon" aria-hidden="true">
                {ICONS[entry.kind]}
              </div>
              <div className="feed-entry-body">
                <div className="feed-entry-top">
                  <strong>{entry.label}</strong>
                  <time dateTime={new Date(entry.ts).toISOString()}>{timeOf(entry.ts)}</time>
                </div>
                {entry.detail && (
                  <code className="feed-entry-detail" dir="ltr">
                    {entry.detail}
                  </code>
                )}
                <span className={`entry-state state-${entry.status}`}>
                  <span className="status-dot" />
                  {STATUS_LABELS[entry.status]}
                </span>
              </div>
            </article>
          ))
        )}
      </div>

      <footer className="workspace-foot">
        <span className="foot-key">SSE</span>
        <span>أحداث حقيقية من المحرك، لا محاكاة</span>
      </footer>
    </aside>
  );
}
