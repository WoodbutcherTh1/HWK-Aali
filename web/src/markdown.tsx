/* Tiny dependency-free markdown renderer for Aali's replies.
   React elements only (no dangerouslySetInnerHTML) — everything is escaped
   by construction. Supports what a chat reply actually contains: fenced code
   with a language label + copy button, inline code, bold/italic, links,
   headings, lists, blockquotes and horizontal rules. */
import { useState, type ReactElement } from "react";

function CopyButton({ getText }: { getText: () => string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="codeblock-copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(getText());
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        } catch {
          /* clipboard unavailable — do nothing */
        }
      }}
    >
      {done ? "✓ تم النسخ" : "نسخ"}
    </button>
  );
}

/* inline formatting: `code`, **bold**, *italic*, [text](url) */
function inline(text: string, keyBase: string) {
  const parts: (string | ReactElement)[] = [];
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\n]+)\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[1] !== undefined) parts.push(<code key={`${keyBase}-${i}`}>{m[1]}</code>);
    else if (m[2] !== undefined) parts.push(<b key={`${keyBase}-${i}`}>{m[2]}</b>);
    else if (m[3] !== undefined) parts.push(<i key={`${keyBase}-${i}`}>{m[3]}</i>);
    else if (m[4] !== undefined)
      parts.push(
        <a key={`${keyBase}-${i}`} href={m[5]} target="_blank" rel="noreferrer">
          {m[4]}
        </a>
      );
    last = re.lastIndex;
    i += 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span>{lang || "code"}</span>
        <CopyButton getText={() => code} />
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function Markdown({ text }: { text: string }) {
  const blocks: ReactElement[] = [];
  // split on fenced code first
  const fence = /```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let bi = 0;
  const pushProse = (chunk: string) => {
    const lines = chunk.split("\n");
    const out: ReactElement[] = [];
    let list: { ordered: boolean; items: string[] } | null = null;
    const flushList = (key: string) => {
      if (!list) return;
      const L = list;
      out.push(
        L.ordered ? (
          <ol key={key}>{L.items.map((it, k) => <li key={k}>{inline(it, `${key}-${k}`)}</li>)}</ol>
        ) : (
          <ul key={key}>{L.items.map((it, k) => <li key={k}>{inline(it, `${key}-${k}`)}</li>)}</ul>
        )
      );
      list = null;
    };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      const h = /^(#{1,3})\s+(.*)$/.exec(line);
      const ul = /^\s*[-•*]\s+(.*)$/.exec(line);
      const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      const quote = /^>\s?(.*)$/.exec(line);
      if (h) {
        flushList(`l-${bi}-${out.length}`);
        const Tag = (h[1].length === 1 ? "h1" : h[1].length === 2 ? "h2" : "h3") as "h1";
        out.push(<Tag key={`h-${bi}-${out.length}`}>{inline(h[2], `h-${bi}-${out.length}`)}</Tag>);
      } else if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
        flushList(`l-${bi}-${out.length}`);
        out.push(<hr key={`hr-${bi}-${out.length}`} />);
      } else if (ul) {
        if (!list || list.ordered) { flushList(`l-${bi}-${out.length}`); list = { ordered: false, items: [] }; }
        list.items.push(ul[1]);
      } else if (ol) {
        if (!list || !list.ordered) { flushList(`l-${bi}-${out.length}`); list = { ordered: true, items: [] }; }
        list.items.push(ol[1]);
      } else if (quote) {
        flushList(`l-${bi}-${out.length}`);
        out.push(<blockquote key={`q-${bi}-${out.length}`}>{inline(quote[1], `q-${bi}-${out.length}`)}</blockquote>);
      } else if (line.trim() === "") {
        flushList(`l-${bi}-${out.length}`);
      } else {
        flushList(`l-${bi}-${out.length}`);
        out.push(<p key={`p-${bi}-${out.length}`}>{inline(line, `p-${bi}-${out.length}`)}</p>);
      }
    }
    flushList(`l-${bi}-end`);
    if (out.length) blocks.push(<div className="md" key={`prose-${bi}`}>{out}</div>);
  };

  while ((m = fence.exec(text)) !== null) {
    if (m.index > last) pushProse(text.slice(last, m.index));
    blocks.push(<CodeBlock key={`code-${bi}`} lang={m[1]} code={m[2].replace(/\n$/, "")} />);
    last = fence.lastIndex;
    bi += 1;
  }
  if (last < text.length) pushProse(text.slice(last));
  return <>{blocks}</>;
}
