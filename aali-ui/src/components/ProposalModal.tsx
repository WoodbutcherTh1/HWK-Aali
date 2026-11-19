import type { Proposal } from "../types";
import DiffViewer from "./DiffViewer";

interface Props {
  proposal: Proposal;
  busy: boolean;
  error: string;
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}

export default function ProposalModal({
  proposal,
  busy,
  error,
  onApprove,
  onReject,
  onClose,
}: Props) {
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal proposal-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="proposal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <span className="eyebrow">PROPOSAL REVIEW</span>
            <h2 id="proposal-title">مراجعة تعديل آلي</h2>
            <p className="modal-path" dir="ltr">
              {proposal.path}
            </p>
          </div>
          <button className="icon-btn" type="button" aria-label="إغلاق" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="proposal-meta">
          <span className="meta-pill">
            <span className="meta-key">tool</span>
            <code dir="ltr">{proposal.tool}</code>
          </span>
          <span className="meta-pill">
            <span className="meta-key">state</span>
            <span className="pending-dot" />
            {proposal.status || "pending"}
          </span>
          {typeof proposal.bytes === "number" && (
            <span className="meta-pill">
              <span className="meta-key">bytes</span>
              {proposal.bytes.toLocaleString("en-US")}
            </span>
          )}
        </div>

        <DiffViewer diff={proposal.diff} />

        <p className="proposal-note">
          لا يُكتب الملف قبل موافقتك. يتحقق المحرك من أن الملف لم يتغيّر، وإذا تغيّر
          سيعيد <code dir="ltr">409</code> بدلاً من الكتابة فوق تعديلك.
        </p>

        {error && <div className="inline-error" role="alert">{error}</div>}

        <footer className="modal-actions">
          <button className="ghost-btn" type="button" onClick={onClose} disabled={busy}>
            لاحقاً
          </button>
          <div className="modal-actions-right">
            <button className="danger-btn" type="button" onClick={onReject} disabled={busy}>
              رفض
            </button>
            <button className="primary-btn" type="button" onClick={onApprove} disabled={busy}>
              {busy ? "جارٍ التنفيذ…" : "موافقة وتطبيق"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
