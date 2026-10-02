"""Fetch Sefaria-Export Hebrew texts (Hebrew Phase 3.5, source 3.5.1).

The Sefaria-Export GitHub repo is a lightweight index; the actual texts
(~26 GB) live in the public GCS bucket gs://sefaria-export/ (README
verified live 2026-10-02). books.json indexes every text version with a
json_url; each text JSON carries its own `license` field near the top of
the file (LICENSE.md: "Each text is licensed separately..."). This script:

  probe   — Range-fetch the first 8 KB of every Hebrew text JSON and read
            its `license` field (values observed: "Public Domain", "CC0",
            "CC-BY", "CC-BY-SA", "CC-BY-NC"). Writes license_probe.jsonl.
  fetch   — pick ONE allowed-license version per title (largest file,
            non-merged preferred) and download it; sha256 every file;
            append manifest.jsonl rows (path, bytes, sha256, license).
  extract — JSON -> JSONL rows {title, heTitle, versionTitle, license,
            categories, source_url, text} under extracted/sefaria/.
  report  — content-free summary to stdout / log tail.

License policy (owner brief): Public Domain / CC0 / CC-BY / CC-BY-SA only;
everything else (CC-BY-NC included) and every missing/unknown license is
EXCLUDED and counted, never downloaded in the fetch stage.

Resume-safe at every stage. Content-free counters only in logs. Stdlib
only, CPU-only. No cleaning, no tokenization (Phase 3.5 rules).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

UA = "HWK-Aali-HebrewCorpus/0.1 (research; +https://github.com/HWK-Aali)"
RAW_BOOKS = "https://raw.githubusercontent.com/Sefaria/Sefaria-Export/master/books.json"
BUCKET = "https://storage.googleapis.com/sefaria-export/"
SEFARIA = Path("D:/hwk-data/hebrew/raw/sefaria")
EXTRACT = Path("D:/hwk-data/hebrew/extracted/sefaria")
DOWNLOAD_LOG = Path("D:/hwk-data/hebrew/DOWNLOAD_LOG.md")
EXTRACT_LOG = Path("D:/hwk-data/hebrew/EXTRACT_LOG.md")

HEAD_BYTES = 8192
ALLOWED = {"public domain", "pd", "cc0", "cc-by", "cc-by-sa"}
LICENSE_RE = re.compile(r'"license"\s*:\s*"([^"]*)"')
# Windows reserved device names + forbidden path characters (raw/ hygiene)
_FORBIDDEN_RE = re.compile(r'[<>:"\\|?*]')
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{utcnow()} UTC] {message}"
    print(line, flush=True)


def fetch(url: str, timeout: int = 120, headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def head_range(url: str, size: int = HEAD_BYTES) -> tuple[bytes, int]:
    """Range-fetch the first `size` bytes; return (body, total file size)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes=0-{size - 1}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        cr = r.headers.get("Content-Range", "")
    total = 0
    m = re.search(r"/(\d+)$", cr)
    if m:
        total = int(m.group(1))
    return body, total


def probe_license(head: bytes) -> tuple[str | None, bool]:
    """Return (license value or None, value_is_known) from raw head bytes."""
    matches = LICENSE_RE.findall(head.decode("utf-8", "replace"))
    for v in matches:
        if v.strip().lower() in ALLOWED:
            return v.strip(), True
    if matches:
        return matches[0].strip(), False
    return None, False


def load_entries() -> list[dict]:
    bpath = SEFARIA / "books.json"
    if not bpath.exists():
        bpath.parent.mkdir(parents=True, exist_ok=True)
        raw = fetch(RAW_BOOKS, timeout=300)
        bpath.write_bytes(raw)
        log(f"books.json downloaded: {len(raw):,} bytes sha256={hashlib.sha256(raw).hexdigest()}")
    else:
        raw = bpath.read_bytes()
    books = json.loads(raw)
    entries = books["books"] if isinstance(books, dict) else books
    return [e for e in entries if e.get("language") in ("he", "Hebrew")]


def stage_probe(limit: int, workers: int) -> None:
    entries = load_entries()
    if limit:
        entries = entries[:limit]
    probe_path = SEFARIA / "license_probe.jsonl"
    done: set[str] = set()
    kept: list[str] = []
    if probe_path.exists():
        with probe_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("ok"):
                    done.add(row["url"])
                    kept.append(line if line.endswith("\n") else line + "\n")
        with probe_path.open("w", encoding="utf-8", newline="\n") as f:
            f.writelines(kept)  # compact: failed probes are retried, never kept
    todo = [e for e in entries if e.get("json_url") not in done]
    log(f"probe: {len(entries):,} hebrew entries, {len(done):,} already probed, {len(todo):,} to go")

    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    licenses: dict[str, int] = {}
    t0 = time.time()

    def probe_one(entry: dict) -> dict:
        url = entry.get("json_url", "")
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                body, total = head_range(url)
                lic, known = probe_license(body)
                return {
                    "url": url,
                    "title": entry.get("title", ""),
                    "versionTitle": entry.get("versionTitle", ""),
                    "categories": entry.get("categories", []),
                    "license": lic,
                    "known": known,
                    "total_size": total,
                    "ok": True,
                }
            except Exception as exc:  # noqa: BLE001 - retry, count at end
                last_err = exc
                time.sleep(1.0 * (attempt + 1))
        return {"url": url, "title": entry.get("title", ""), "versionTitle": entry.get("versionTitle", ""),
                "categories": entry.get("categories", []), "license": None, "known": False,
                "total_size": 0, "ok": False, "error": f"{type(last_err).__name__}: {last_err}"}

    with probe_path.open("a", encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(probe_one, e) for e in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                with lock:
                    if row["ok"]:
                        counters["ok"] += 1
                        licenses[row["license"] or "(missing)"] = licenses.get(row["license"] or "(missing)", 0) + 1
                    else:
                        counters["fail"] += 1
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    if i % 500 == 0:
                        out.flush()
                        log(f"probe: {i:,}/{len(todo):,} ok={counters['ok']:,} fail={counters['fail']:,}")
    log(f"probe DONE in {time.time() - t0:.0f}s: ok={counters['ok']:,} fail={counters['fail']:,}")
    log(f"license distribution: {dict(sorted(licenses.items(), key=lambda kv: -kv[1]))}")


def load_probe_rows() -> list[dict]:
    probe_path = SEFARIA / "license_probe.jsonl"
    rows = []
    with probe_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def safe_local_path(bucket_path: str, base: Path) -> Path | None:
    """Map a bucket object path to a local path; None if unsafe on Windows."""
    rel = urllib.parse.unquote(bucket_path)
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    clean: list[str] = []
    for p in parts:
        if p.lower().rstrip(".") in _RESERVED:
            return None
        p2 = _FORBIDDEN_RE.sub("_", p).rstrip(" .")
        if not p2:
            return None
        clean.append(p2)
    return base.joinpath(*clean)


def choose_versions(rows: list[dict], allow_merged_fallback: bool) -> tuple[list[dict], dict]:
    """One best allowed-license version per title. Returns (choices, stats)."""
    by_title: dict[str, list[dict]] = {}
    stats = {"rows": len(rows), "failed_probe": 0, "no_license": 0, "not_allowed": 0,
             "allowed_nonmerged": 0, "allowed_merged_only_titles": 0}
    for r in rows:
        if not r.get("ok"):
            stats["failed_probe"] += 1
            continue
        lic = (r.get("license") or "").strip()
        if not lic:
            stats["no_license"] += 1
            continue
        if lic.lower() not in ALLOWED:
            stats["not_allowed"] += 1
            continue
        by_title.setdefault(r["title"], []).append(r)
    choices: list[dict] = []
    for title, cands in by_title.items():
        nonmerged = [c for c in cands if c.get("versionTitle") != "merged"]
        pool = nonmerged if nonmerged else ([c for c in cands if c.get("versionTitle") == "merged"]
                                           if allow_merged_fallback else [])
        if pool and not nonmerged and pool[0].get("versionTitle") == "merged":
            stats["allowed_merged_only_titles"] += 1
        if not pool:
            continue
        stats["allowed_nonmerged"] += len(nonmerged)
        best = max(pool, key=lambda c: (c.get("total_size") or 0))
        choices.append(best)
    return choices, stats


def stage_fetch(workers: int, allow_merged_fallback: bool) -> None:
    rows = load_probe_rows()
    choices, stats = choose_versions(rows, allow_merged_fallback)
    log(f"fetch: probe rows={stats['rows']:,} failed={stats['failed_probe']:,} "
        f"no_license={stats['no_license']:,} not_allowed={stats['not_allowed']:,}")
    log(f"fetch: titles with an allowed version={len(choices):,} "
        f"(merged-only titles: {stats['allowed_merged_only_titles']:,})")

    manifest_path = SEFARIA / "manifest.jsonl"
    done_paths: set[str] = set()
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    done_paths.add(json.loads(line)["path"])
                except (json.JSONDecodeError, KeyError):
                    continue
    todo = []
    for c in choices:
        bucket_path = urllib.parse.urlparse(c["url"]).path.lstrip("/")
        bucket_path = bucket_path.split("sefaria-export/", 1)[-1]
        if bucket_path not in done_paths:
            todo.append(c)
    log(f"fetch: {len(todo):,} files to download "
        f"({sum(c.get('total_size') or 0 for c in todo) / 1e9:.2f} GB expected)")

    bytes_total = 0
    ok = failed = 0
    lock = threading.Lock()

    def download_one(c: dict) -> dict:
        url = c["url"]
        bucket_path = urllib.parse.urlparse(url).path.lstrip("/")
        bucket_path = bucket_path.split("sefaria-export/", 1)[-1]
        local = safe_local_path(bucket_path, SEFARIA)
        if local is None:
            return {"path": bucket_path, "skip": "unsafe-path"}
        expected = c.get("total_size") or 0
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                tmp = local.with_suffix(local.suffix + ".part")
                sha = hashlib.sha256()
                size = 0
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=600) as r, tmp.open("wb") as w:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        sha.update(chunk)
                        size += len(chunk)
                        w.write(chunk)
                if expected and size != expected:
                    raise IOError(f"size mismatch: got {size}, expected {expected}")
                os.replace(tmp, local)
                return {"path": bucket_path, "bytes": size, "sha256": sha.hexdigest(),
                        "license": c.get("license"), "title": c.get("title"),
                        "versionTitle": c.get("versionTitle"), "categories": c.get("categories", []),
                        "url": url}
            except Exception as exc:  # noqa: BLE001 - retry then fail loudly
                last_err = exc
                time.sleep(2.0 * (attempt + 1))
        return {"path": bucket_path, "error": f"{type(last_err).__name__}: {last_err}"}

    with manifest_path.open("a", encoding="utf-8") as mf:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(download_one, c) for c in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                with lock:
                    if "error" in row or row.get("skip"):
                        failed += 1
                    else:
                        ok += 1
                        bytes_total += row["bytes"]
                        mf.write(json.dumps(row, ensure_ascii=False) + "\n")
                    if i % 200 == 0:
                        mf.flush()
                        log(f"fetch: {i:,}/{len(todo):,} ok={ok:,} fail={failed:,} "
                            f"{bytes_total / 1e9:.2f} GB")
    log(f"fetch DONE: ok={ok:,} failed={failed:,} bytes={bytes_total:,}")
    _log_download_summary(ok, failed, bytes_total)


def _log_download_summary(ok: int, failed: int, bytes_total: int) -> None:
    bpath = SEFARIA / "books.json"
    section = (
        f"\n## Sefaria-Export Hebrew fetch ({utcnow()[:10]})\n"
        f"- index: books.json {bpath.stat().st_size:,} bytes "
        f"(sha256 {hashlib.sha256(bpath.read_bytes()).hexdigest()}, repo master a44f49f at probe time)\n"
        f"- bucket: gs://sefaria-export/ (public GCS; verified live)\n"
        f"- files fetched: {ok:,} (license-gated BEFORE download; one version per title)\n"
        f"- failures: {failed:,}\n"
        f"- total bytes: {bytes_total:,}\n"
        f"- per-file sha256 + license + url: raw/sefaria/manifest.jsonl\n"
    )
    print(section, flush=True)
    try:
        with DOWNLOAD_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


def flatten_text(node: object) -> list[str]:
    out: list[str] = []
    if isinstance(node, str):
        s = node.strip()
        if s:
            out.append(s)
    elif isinstance(node, list):
        for item in node:
            out.extend(flatten_text(item))
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(flatten_text(v))
    return out


def stage_extract() -> None:
    manifest_path = SEFARIA / "manifest.jsonl"
    out_path = EXTRACT / "sefaria_versions.jsonl"
    EXTRACT.mkdir(parents=True, exist_ok=True)
    docs = skipped_bad = skipped_lic = skipped_empty = 0
    chars_total = 0
    by_license: dict[str, int] = {}
    with manifest_path.open("r", encoding="utf-8") as mf, out_path.open("w", encoding="utf-8", newline="\n") as out:
        for line in mf:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                skipped_bad += 1
                continue
            lic = (m.get("license") or "").strip()
            if lic.lower() not in ALLOWED:
                skipped_lic += 1
                continue
            local = safe_local_path(m["path"], SEFARIA)
            if local is None or not local.exists():
                skipped_bad += 1
                continue
            try:
                obj = json.loads(local.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                skipped_bad += 1
                continue
            leaves = flatten_text(obj.get("text"))
            text = "\n".join(leaves)
            if not text:
                skipped_empty += 1
                continue
            out.write(json.dumps({
                "title": obj.get("title") or m.get("title", ""),
                "heTitle": obj.get("heTitle", ""),
                "versionTitle": obj.get("versionTitle") or m.get("versionTitle", ""),
                "license": lic,
                "categories": m.get("categories", []),
                "source_url": m.get("url", ""),
                "text": text,
            }, ensure_ascii=False) + "\n")
            docs += 1
            chars_total += len(text)
            by_license[lic] = by_license.get(lic, 0) + 1
    log(f"extract DONE: docs={docs:,} chars={chars_total:,} "
        f"bad={skipped_bad:,} lic-gate={skipped_lic:,} empty={skipped_empty:,}")
    section = (
        f"\n## Sefaria extraction ({utcnow()})\n"
        f"- docs written: {docs:,} -> extracted/sefaria/sefaria_versions.jsonl\n"
        f"- text chars: {chars_total:,} ({chars_total / 1e9:.2f} GB)\n"
        f"- docs per license: {dict(sorted(by_license.items(), key=lambda kv: -kv[1]))}\n"
        f"- skipped: bad/unreadable {skipped_bad:,}, license-gate {skipped_lic:,}, empty {skipped_empty:,}\n"
    )
    print(section, flush=True)
    try:
        with EXTRACT_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["probe", "fetch", "extract", "all"], required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="probe only first N entries (testing)")
    parser.add_argument("--allow-merged-fallback", action="store_true",
                        help="allow 'merged' versions when a title has no licensed specific version")
    args = parser.parse_args()
    SEFARIA.mkdir(parents=True, exist_ok=True)
    if args.stage in ("probe", "all"):
        stage_probe(args.limit, args.workers)
    if args.stage in ("fetch", "all"):
        stage_fetch(args.workers, args.allow_merged_fallback)
    if args.stage in ("extract", "all"):
        stage_extract()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
