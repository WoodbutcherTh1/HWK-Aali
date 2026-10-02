"""Sefaria-Export probe (Hebrew Phase 3.5, source 3.5.1) — one-off recon.

Answers, before any real fetch:
  1. books.json index shape (entry count, keys, languages, license presence).
  2. Full GCS inventory of json/ objects (name+size) — Hebrew footprint and
     per-category sizes WITHOUT downloading any text.
  3. Where the per-text `license` field sits in a text JSON — head or tail?
     (Decides: cheap Range pre-filter vs download-then-filter.)

Writes only under D:/hwk-data/hebrew/raw/sefaria/. Content-free output.
Stdlib only. No cleaning, no tokenization (Phase 3.5 rules).
"""

import hashlib
import json
import pathlib
import urllib.parse
import urllib.request

UA = "HWK-Aali-HebrewCorpus/0.1 (research; +https://github.com/HWK-Aali)"
RAW_BOOKS = "https://raw.githubusercontent.com/Sefaria/Sefaria-Export/master/books.json"
GCS = "https://storage.googleapis.com/sefaria-export/"
OUT = pathlib.Path("D:/hwk-data/hebrew/raw/sefaria")


def fetch(url: str, timeout: int = 180, headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def range_fetch(url: str, first: int | None = None, last: int | None = None) -> tuple[bytes, str]:
    """Range request; returns (body, content-range header or '')."""
    if first is not None:
        rng = f"bytes=0-{first - 1}"
    else:
        rng = f"bytes=-{last}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": rng})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(), r.headers.get("Content-Range", "")


def inventory() -> list[dict]:
    """Page through the public bucket listing for all json/ objects."""
    items: list[dict] = []
    token = ""
    pages = 0
    while pages < 200:
        url = (
            "https://storage.googleapis.com/storage/v1/b/sefaria-export/o"
            "?prefix=json/&fields=items(name,size),nextPageToken&maxResults=1000"
        )
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        data = json.loads(fetch(url, timeout=120))
        items.extend(data.get("items", []))
        token = data.get("nextPageToken", "")
        pages += 1
        if not token:
            break
    return items


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. books.json (reuse if already downloaded)
    bpath = OUT / "books.json"
    if bpath.exists():
        raw = bpath.read_bytes()
    else:
        raw = fetch(RAW_BOOKS)
        bpath.write_bytes(raw)
    print(f"books.json: {len(raw):,} bytes sha256={hashlib.sha256(raw).hexdigest()[:16]}…")
    books = json.loads(raw)
    entries = books["books"] if isinstance(books, dict) else books
    he = [e for e in entries if e.get("language") in ("he", "Hebrew")]
    non_merged = [e for e in he if e.get("versionTitle") != "merged"]
    print(f"index: {len(entries):,} entries | hebrew {len(he):,} | hebrew non-merged {len(non_merged):,}")

    # 2. GCS inventory
    items = inventory()
    inv_path = OUT / "inventory.jsonl"
    with inv_path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps({"name": it.get("name"), "size": int(it.get("size", 0))}, ensure_ascii=False) + "\n")
    total = sum(int(it.get("size", 0)) for it in items)
    print(f"inventory: {len(items):,} json/ objects, {total / 1e9:.2f} GB total (all languages)")

    def seg(name: str, i: int) -> str:
        parts = name.split("/")
        return parts[i] if len(parts) > i else ""

    hebrew = [it for it in items if "/Hebrew/" in it["name"]]
    heb_bytes = sum(int(it.get("size", 0)) for it in hebrew)
    print(f"Hebrew files: {len(hebrew):,}  {heb_bytes / 1e9:.2f} GB")
    merged = [it for it in hebrew if seg(it["name"], -1) == "merged.json"]
    print(f"  merged.json among them: {len(merged):,}  {sum(int(i['size']) for i in merged) / 1e9:.2f} GB")
    cats: dict = {}
    for it in hebrew:
        cats[seg(it["name"], 1)] = cats.get(seg(it["name"], 1), 0) + int(it.get("size", 0))
    print("  Hebrew bytes by category:")
    for c, b in sorted(cats.items(), key=lambda kv: -kv[1])[:20]:
        print(f"    {c:<40} {b / 1e6:>10.1f} MB")

    # 3. License position probe: head vs tail on real files
    print("\nlicense position probe (Range head 3KB / tail 3KB):")
    samples: list[tuple[str, str]] = []
    genesis = [e for e in he if e.get("title") == "Genesis"]
    for e in genesis[:4]:
        samples.append((f"{e.get('title')} [{e.get('versionTitle')}]", e.get("json_url", "")))
    big = sorted(hebrew, key=lambda it: -int(it.get("size", 0)))[:2]
    for it in big:
        samples.append((f"BIG {it['name'].split('/')[3] if len(it['name'].split('/')) > 3 else '?'}", GCS + it["name"]))
    for label, url in samples:
        try:
            head, cr = range_fetch(url, first=3000)
            tail, _ = range_fetch(url, last=3000)
            in_head = b'"license"' in head
            in_tail = b'"license"' in tail
            print(f"  {label[:60]:<62} head={in_head} tail={in_tail}  ({cr})")
            blob = head if in_head else (tail if in_tail else b"")
            if blob:
                i = blob.find(b'"license"')
                print(f"     …{blob[max(0, i - 40):i + 80].decode('utf-8', 'replace')!r}…")
        except Exception as ex:  # noqa: BLE001
            print(f"  {label[:60]:<62} FAILED {type(ex).__name__}: {ex}")


if __name__ == "__main__":
    main()
