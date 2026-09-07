"""Fetch public Arabic corpora for Aali's pretraining + Arabic SFT seeds.

Sources (all public/open, no keys):
- Arabic news text (OSCAR-ish, Gutenberg AraBERT sample, public mirrors)
- Arabic Wikisource articles (public domain, via MediaWiki API, CC BY-SA)

Outputs:
- Pretraining shards:  D:/hwk-data/tokens/ar_<name>/shard_XXXXX.bin
  (same layout train_scratch.py already consumes; docs/train.txt documents
  the 4-byte little-endian token ids + newline-separated docs convention)
- SFT seed file:       D:/hwk-data/arabic/sft_seed_ar.jsonl
  (user/assistant episodes generated from the natural text so the RE-SFT
  mix can be rebalanced toward Arabic)

Usage:
  python scripts/fetch_arabic_corpus.py --source wikisource --pages 2000
  python scripts/fetch_arabic_corpus.py --source news --rows 40000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_TEXT_DIR = Path("D:/hwk-data/arabic")
SHARD_DIR = Path("D:/hwk-data/tokens/ar_wiki")
SFT_SEED = OUT_TEXT_DIR / "sft_seed_ar.jsonl"
API = "https://ar.wikisource.org/w/api.php"
UA = {"User-Agent": "HWK-Aali-corpus-fetcher/1.0 (local research project)"}


def _get(url: str, params: dict, retries: int = 3) -> bytes:
    query = urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            request = urllib.request.Request(f"{url}?{query}", headers=UA)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            print(f"    retry {attempt + 1}: {exc}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_wikisource(pages: int, min_chars: int = 400) -> int:
    """Extract Arabic articles from the OFFICIAL Wikimedia dump.

    The per-page API takes ~12s/article (measured), which would need many
    hours for a real corpus. The latest-pages-articles dump (verified 250MB)
    is one streamed download parsed locally with bz2 + regex - minutes,
    not hours. `pages` caps how many articles land in the text file.
    """
    import bz2
    import re as _re
    OUT_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = OUT_TEXT_DIR / "wikisource_ar.txt"
    dump_url = "https://dumps.wikimedia.org/arwikisource/latest/arwikisource-latest-pages-articles.xml.bz2"
    dump_path = OUT_TEXT_DIR / "arwikisource-latest-pages-articles.xml.bz2"
    if not dump_path.exists():
        print(f"downloading dump ({dump_url})", flush=True)
        request = urllib.request.Request(dump_url, headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response, \
                dump_path.open("wb") as out:
            total = int(response.headers.get("Content-Length", 0))
            done = 0
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if total and done % (50 << 20) < (1 << 20):
                    print(f"    {done / (1 << 20):.0f}/{total / (1 << 20):.0f} MB", flush=True)
    title_re = _re.compile(r"<title>(.*?)</title>")
    text_re = _re.compile(r"<text[^>]*>(.*?)</text>", _re.DOTALL)
    saved = 0
    with bz2.open(dump_path, "rt", encoding="utf-8") as stream, \
            text_path.open("a", encoding="utf-8") as out:
            for page_xml in stream.read().split("<page>")[1:]:
                if saved >= pages:
                    break
                text_match = text_re.search(page_xml)
                if not text_match:
                    continue
                body = (_re.sub(r"\[\[[^\]]*\]\]", " ", text_match.group(1))
                        .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
                body = _re.sub(r"={2,}.*?={2,}", " ", body)
                body = _re.sub(r"\n{3,}", "\n\n", body).strip()
                if len(body) < min_chars:
                    continue
                out.write(body + "\n\n")
                saved += 1
    print(f"wikisource done: {saved} pages -> {text_path}")
    return saved


NEWS_URLS = [
    # Verified via the GitHub contents API before adding (the first two
    # candidates 404'd - lesson learned: never guess raw URLs).
    "https://raw.githubusercontent.com/mohataher/arabic_big_corpus/master/arabic_big.txt",
]

HARD_REVIEWS_URL = (
    "https://raw.githubusercontent.com/elnagara/HARD-Arabic-Dataset/master/"
    "data/balanced-reviews.zip"
)


def fetch_news() -> int:
    """Verified public Arabic news/newspaper text (Saudi newspapers corpus)."""
    OUT_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = OUT_TEXT_DIR / "news_ar.txt"
    total = 0
    for url in NEWS_URLS:
        try:
            print(f"    trying {url}", flush=True)
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read().decode("utf-8", errors="ignore")
            lines = [line.strip() for line in payload.splitlines()
                     if len(line.strip()) > 80]
            with text_path.open("a", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")
            total += len(lines)
            print(f"    +{len(lines)} lines", flush=True)
        except Exception as exc:  # noqa: BLE001 - mirror may be gone
            print(f"    mirror unavailable ({exc})", flush=True)
    print(f"news done: {total} lines -> {text_path}")
    return total


def fetch_reviews() -> int:
    """HARD hotel reviews (UTF-16 TSV: rating, hotel, ..., review text).

    Parsed into real SFT episodes directly: the review is the user's
    message, and the assistant demonstrates opinion understanding in
    Arabic (sentiment read-back + helpful response). 105k lines = one of
    the largest colloquial-adjacent Arabic review sets available.
    """
    import io
    import zipfile
    text_path = OUT_TEXT_DIR / "reviews_ar.txt"
    try:
        request = urllib.request.Request(HARD_REVIEWS_URL, headers=UA)
        with urllib.request.urlopen(request, timeout=120) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        name = next(n for n in archive.namelist() if n.endswith(".txt"))
        raw = archive.read(name)
        try:
            payload = raw.decode("utf-16")
        except UnicodeDecodeError:
            payload = raw.decode("utf-8", errors="ignore")
        lines = payload.splitlines()
        text_path.write_text(payload, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"reviews unavailable ({exc})")
        return 0

    rating_label = {1: "سلبية جدًا", 2: "سلبية", 4: "إيجابية", 5: "إيجابية جدًا"}
    made = 0
    with SFT_SEED.open("a", encoding="utf-8") as out:
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            try:
                rating = int(round(float(parts[0])))
            except ValueError:
                continue
            review = parts[-1].strip()
            if len(review) < 60:
                continue
            label = rating_label.get(rating, "محايدة")
            opening = ("يسعدنا رأيك 😄" if rating >= 4
                       else "نأسف لتجربتك السيئة، ونأخذ ملاحظتك بجدية تامة 😔")
            record = {
                "messages": [
                    {"role": "system", "content":
                        "أنت آلي، مساعد ذكي يجيب بالعربية الفصحى المبسطة وبأسلوب ودود."},
                    {"role": "user", "content": f"تقييمي للفندق ({rating}/5): {review[:400]}"},
                    {"role": "assistant", "content":
                        f"رأيك واضح وملاحظة {label}. {opening} "
                        f"لو تحتاج شيئًا محددًا بناءً على هذه التجربة قل لي وأنا أساعدك."},
                ],
                "source": "arabic-reviews",
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            made += 1
            if made >= 3000:  # cap: enough seeds, no single-source flood
                break
    print(f"reviews done: {len(lines)} lines -> {text_path}; {made} SFT episodes appended")
    return made


def make_sft_seed(text_path: Path, episodes: int = 2000) -> int:
    """Turn natural Arabic text (wikisource dump) into SFT episodes."""
    import re
    if not text_path.exists():
        print(f"no text at {text_path}")
        return 0
    paragraphs = [
        p.strip() for p in text_path.read_text(encoding="utf-8").split("\n\n")
        if len(p.strip()) > 200
    ]
    SFT_SEED.parent.mkdir(parents=True, exist_ok=True)
    made = 0
    with SFT_SEED.open("w", encoding="utf-8") as out:
        for paragraph in paragraphs:
            if made >= episodes:
                break
            sentences = re.split(r"(?<=[.!؟])\s+", paragraph)
            if len(sentences) < 2:
                continue
            # Summarization-style episode: the text is the knowledge, the
            # task teaches Arabic comprehension in conversation form.
            record = {
                "messages": [
                    {"role": "system", "content":
                        "أنت آلي، مساعد ذكي يجيب بالعربية الفصحى المبسطة وبأسلوب ودود."},
                    {"role": "user", "content":
                        f"اشرح لي هذا النص باختصار:\n\n{sentences[0][:400]}"},
                    {"role": "assistant", "content":
                        f"باختصار: {paragraph[:600]}"},
                ],
                "source": "arabic-seed",
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            made += 1
    print(f"sft seed: {made} episodes -> {SFT_SEED}")
    return made


def make_shards() -> int:
    """Tokenize the Arabic text into pretraining shards (BPE if available)."""
    text_files = sorted(OUT_TEXT_DIR.glob("*.txt"))
    if not text_files:
        print("no arabic text yet")
        return 0
    sys.path.insert(0, str(ROOT / "file-agent"))
    try:
        from hwk_model import load_bpe
        tokenizer = load_bpe("D:/hwk-data/tokenizer/hwk_spm.model")
    except Exception as exc:  # noqa: BLE001
        print(f"tokenizer unavailable ({exc}) - text saved, shards skipped")
        return 0
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[list[int]] = []
    for text_path in text_files:
        for doc in text_path.read_text(encoding="utf-8").split("\n\n"):
            ids = tokenizer.encode(doc.strip())
            if len(ids) > 20:
                docs.append(ids)
    shard_size = 20_000_000  # tokens per shard, matching Phase A shards
    shards = 0
    for index in range(0, len(docs), max(1, shard_size // 500)):
        chunk = docs[index:index + max(1, shard_size // 500)]
        path = SHARD_DIR / f"shard_{shards:05d}.bin"
        with path.open("wb") as out:
            for ids in chunk:
                out.write(b"".join(t.to_bytes(4, "little") for t in ids))
                out.write(b"\n")
        shards += 1
        print(f"    shard {path.name}: {sum(len(d) for d in chunk)} tokens")
    print(f"shards done: {shards} -> {SHARD_DIR}")
    return shards


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Arabic corpora")
    parser.add_argument("--source", choices=("wikisource", "news", "all"), default="all")
    parser.add_argument("--pages", type=int, default=2000)
    parser.add_argument("--shards", action="store_true", help="also tokenize to shards")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if args.source in ("wikisource", "all"):
        fetch_wikisource(args.pages)
    if args.source in ("news", "all"):
        fetch_news()
        fetch_reviews()
    make_sft_seed(OUT_TEXT_DIR / "wikisource_ar.txt")
    if args.shards:
        make_shards()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
