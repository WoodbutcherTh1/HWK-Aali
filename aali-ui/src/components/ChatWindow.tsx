import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  draft: string;
  liveNote: string;
  notice: string;
  surfaceActive: boolean;
  onDraft: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onNewChat: () => void;
  onClearNotice: () => void;
}

const STARTERS = [
  "اقرأ بنية المشروع ولخّص لي أهم الملفات",
  "أصلح الخطأ في آخر اختبار ثم أعد تشغيله",
  "اعرض لي ما تغيّر في مساحة العمل",
];

export default function ChatWindow({
  messages,
  busy,
  draft,
  liveNote,
  notice,
  surfaceActive,
  onDraft,
  onSend,
  onStop,
  onNewChat,
  onClearNotice,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, liveNote, busy]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 190)}px`;
  }, [draft]);

  return (
    <section className="chat-pane" aria-label="المحادثة">
      <header className="panel-head chat-head">
        <div>
          <span className="eyebrow">AALI ENGINE · LOCAL</span>
          <h2>المحادثة</h2>
        </div>
        <button className="ghost-btn compact" type="button" onClick={onNewChat}>
          ＋ جلسة جديدة
        </button>
      </header>

      <div className="messages" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-orb" aria-hidden="true">
              <span className="orb-core">آ</span>
              <span className="orb-ring orb-ring-a" />
              <span className="orb-ring orb-ring-b" />
            </div>
            <span className="eyebrow">ONE BRAIN · MANY SURFACES</span>
            <h1>آلي جاهز على جهازك</h1>
            <p>
              اطلب تعديل ملف، تشغيل اختبار، أو قراءة مشروع كامل. سترى كل خطوة
              في مساحة العمل الحية — من الهاتف أيضاً.
            </p>
            <div className="starter-grid">
              {STARTERS.map((starter) => (
                <button
                  className="starter-card"
                  key={starter}
                  type="button"
                  onClick={() => onDraft(starter)}
                >
                  <span className="starter-arrow">↗</span>
                  <span>{starter}</span>
                </button>
              ))}
            </div>
            <div className="context-note">
              <span className="context-dot" />
              سياق كبير مدعوم · repo map · بث SSE مباشر
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <article className={`message message-${message.role}`} key={message.id}>
              <div className="message-avatar" aria-hidden="true">
                {message.role === "user" ? "أنت" : "آ"}
              </div>
              <div className={`message-bubble ${message.error ? "message-error" : ""}`}>
                <div className="message-meta">
                  <strong>{message.role === "user" ? "أنت" : "آلي"}</strong>
                  <time dateTime={new Date(message.ts).toISOString()}>
                    {new Date(message.ts).toLocaleTimeString("ar", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
                {message.pending ? (
                  <div className="thinking-block">
                    <div className="thinking-row">
                      <span className="thinking-orb" />
                      <span>آلي يعمل…</span>
                      {busy && (
                        <button className="stop-inline" type="button" onClick={onStop}>
                          إيقاف
                        </button>
                      )}
                    </div>
                    {liveNote && <pre className="live-note">{liveNote}</pre>}
                  </div>
                ) : (
                  <div className="message-text">{message.text}</div>
                )}
              </div>
            </article>
          ))
        )}
      </div>

      {notice && (
        <div className="notice-bar" role="status">
          <span>{notice}</span>
          <button className="notice-close" type="button" onClick={onClearNotice}>
            ✕
          </button>
        </div>
      )}

      <div className={`composer ${surfaceActive ? "composer-surfaced" : ""}`}>
        <textarea
          ref={inputRef}
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder="اكتب طلبك… اطلب قراءة ملف، تعديل كود، أو تشغيل أمر"
          rows={1}
          dir="auto"
          aria-label="رسالة إلى آلي"
        />
        <div className="composer-actions">
          <span className="composer-hint">
            {draft.length > 0 ? `${draft.length.toLocaleString("en-US")} حرف` : "Enter للإرسال · Shift+Enter لسطر جديد"}
          </span>
          {busy ? (
            <button className="stop-btn" type="button" onClick={onStop}>
              إيقاف
            </button>
          ) : (
            <button className="send-btn" type="button" onClick={onSend} disabled={!draft.trim()}>
              إرسال <span aria-hidden="true">↑</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
