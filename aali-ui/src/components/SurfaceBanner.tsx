import type { Surface, SurfaceEvent } from "../types";

const LABELS: Record<Exclude<Surface, "chat">, { ar: string; en: string; icon: string; hint: string }> = {
  studio: {
    ar: "وضع الاستوديو",
    en: "STUDIO MODE",
    icon: "⌘",
    hint: "المحرك وجّه هذه الجلسة إلى العمل على الكود والملفات والفروقات.",
  },
  graphics: {
    ar: "وضع الرسومات",
    en: "GRAPHICS MODE",
    icon: "◈",
    hint: "المحرك وجّه هذه الجلسة إلى الصور والتصميم والوسائط.",
  },
  control: {
    ar: "وضع التحكم",
    en: "CONTROL MODE",
    icon: "⌁",
    hint: "المحرك وجّه هذه الجلسة إلى التحكم بالجهاز والعمليات والنظام.",
  },
};

interface Props {
  surface: SurfaceEvent | null;
  onDismiss: () => void;
}

export default function SurfaceBanner({ surface, onDismiss }: Props) {
  if (!surface || surface.surface === "chat") return null;
  const meta = LABELS[surface.surface];
  if (!meta) return null;

  return (
    <section className={`surface-banner surface-${surface.surface}`} aria-live="polite">
      <div className="surface-icon" aria-hidden="true">{meta.icon}</div>
      <div className="surface-copy">
        <div className="surface-title-row">
          <strong>{meta.ar}</strong>
          <span>{meta.en}</span>
        </div>
        <p>{surface.reason || meta.hint}</p>
      </div>
      <button className="surface-dismiss" type="button" onClick={onDismiss}>
        العودة للمحادثة
      </button>
    </section>
  );
}
