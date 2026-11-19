import type { ReactNode } from "react";

function lineClass(line: string): string {
  if (line.startsWith("@@")) return "diff-line diff-hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line diff-file";
  if (line.startsWith("+")) return "diff-line diff-add";
  if (line.startsWith("-")) return "diff-line diff-remove";
  return "diff-line";
}

export default function DiffViewer({ diff }: { diff?: string }) {
  const lines = (diff || "").split(/\r?\n/);
  const nodes: ReactNode[] = lines.map((line, index) => (
    <div className={lineClass(line)} key={`${index}-${line.slice(0, 8)}`}>
      <span className="diff-gutter">{String(index + 1).padStart(3, " ")}</span>
      <code>{line || " "}</code>
    </div>
  ));

  return (
    <div className="diff-viewer" dir="ltr" aria-label="فرق الملف">
      <div className="diff-toolbar">
        <span className="diff-dot diff-dot-red" />
        <span className="diff-dot diff-dot-amber" />
        <span className="diff-dot diff-dot-green" />
        <span className="diff-toolbar-label">unified diff</span>
      </div>
      <div className="diff-scroll">
        {nodes.length ? nodes : <div className="diff-empty">لا يوجد فرق نصي لعرضه.</div>}
      </div>
    </div>
  );
}
