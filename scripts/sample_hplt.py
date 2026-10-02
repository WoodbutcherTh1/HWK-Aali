"""Sample + assess the HPLT v2 CLEANED Hebrew shard (Phase 3.5, source 3.5.4).

HPLT v2 monolingual datasets: packaging licensed CC0 ("no rights
reserved", hplt-project.org/datasets/v2.0 terms, verified live
2026-10-02) — CC0 is in the owner's allowed list. Source: Common Crawl +
Internet Archive, text-extracted with Trafilatura, language-ID with
OpenLID, deduplicated and cleaned by the HPLT pipeline.

This script:
  1. Range-downloads the first --bytes of the cleaned heb_Hebr shard
     (single 28.8 GB .jsonl.zst) into raw/hplt/ — the partial file IS the
     start of the full file, so a later full fetch just resumes from it.
  2. Streams-decompresses (zstandard) exactly what was downloaded,
     parsing whole JSONL lines (one JSON doc per line in this format).
  3. Aggregates CONTENT-FREE quality stats: docs, chars, Hebrew-script
     share, lang/prob fields, doc_scores, pii presence counter, robotstxt
     value counter — never any text.

Stdlib + zstandard (already in .venv). No cleaning (Phase 4), no
tokenization (Phase 6).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import zstandard

UA = "HWK-Aali-HebrewCorpus/0.1 (research; +https://github.com/HWK-Aali)"
URL = "https://data.hplt-project.org/two/cleaned/heb_Hebr/1.jsonl.zst"
MD5_URL = URL + ".md5"
RAW = Path("D:/hwk-data/hebrew/raw/hplt")
HEB = range(0x0590, 0x05FF + 1)
LATIN = range(0x0041, 0x007A + 1)


class LimitedReader(io.RawIOBase):
    """Read-only wrapper exposing at most `limit` bytes of an underlying stream."""

    def __init__(self, stream, limit: int):
        self.stream = stream
        self.remaining = limit

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if self.remaining <= 0:
            return 0
        want = min(len(b), self.remaining)
        got = self.stream.read(want)
        if not got:
            return 0
        self.remaining -= len(got)
        b[: len(got)] = got
        return len(got)


def utcnow() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def log(message: str) -> None:
    print(f"[{utcnow()} UTC] {message}", flush=True)


def fetch_md5() -> str:
    req = urllib.request.Request(MD5_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode().strip()


def range_download(target_bytes: int) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "heb_Hebr_cleaned_1.jsonl.zst"
    have = out.stat().st_size if out.exists() else 0
    if have >= target_bytes:
        log(f"already have {have:,} bytes >= target {target_bytes:,}")
        return out
    end = target_bytes - 1
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Range": f"bytes={have}-{end}"})
    log(f"downloading bytes {have:,}-{end:,} ...")
    with urllib.request.urlopen(req, timeout=600) as r, out.open("ab") as f:
        total = have
        t0 = time.time()
        marker = total // (200 << 20)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            if total // (200 << 20) != marker:
                marker = total // (200 << 20)
                rate = total * 8 / 1e6 / max(time.time() - t0, 0.1)
                log(f"  {total / 1e9:.2f} GB downloaded ({rate:.0f} Mb/s)")
    log(f"downloaded: {out} ({out.stat().st_size:,} bytes)")
    return out


def stream_docs(zst_path: Path, limit_bytes: int) -> None:
    """Decompress only the first `limit_bytes` of the .zst and parse whole lines."""
    docs = bad_lines = chars = hebrew = latin = keep = drop = pii_docs = 0
    robots: dict = {}
    lang_top: dict = {}
    probs: list[float] = []
    with zst_path.open("rb") as raw_f:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(LimitedReader(raw_f, limit_bytes)) as zr:
            text_stream = io.TextIOWrapper(zr, encoding="utf-8", errors="replace")
            for line in text_stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                docs += 1
                t = doc.get("text") or ""
                chars += len(t)
                hebrew += sum(1 for c in t if ord(c) in HEB)
                latin += sum(1 for c in t if ord(c) in LATIN)
                if doc.get("filter") == "keep":
                    keep += 1
                else:
                    drop += 1
                if doc.get("pii"):
                    pii_docs += 1
                rb = doc.get("robotstxt")
                robots[rb] = robots.get(rb, 0) + 1
                lp = doc.get("lang") or []
                if lp:
                    lang_top[lp[0]] = lang_top.get(lp[0], 0) + 1
                pr = doc.get("prob") or []
                if pr:
                    probs.append(pr[0])
                if docs % 20000 == 0:
                    log(f"parsed {docs:,} docs, {chars / 1e6:.1f}M chars")
    he_share = hebrew / max(chars, 1)
    log(f"SAMPLE RESULT: docs={docs:,} chars={chars:,} ({chars / 1e9:.2f} GB)")
    log(f"  Hebrew-script share: {he_share:.1%} | Latin: {latin / max(chars, 1):.1%}")
    log(f"  filter keep/drop: {keep:,}/{drop:,}")
    log(f"  docs with pii[] spans: {pii_docs:,}")
    log(f"  robotstxt values: {robots}")
    log(f"  top lang labels: {dict(sorted(lang_top.items(), key=lambda kv: -kv[1])[:5])}")
    if probs:
        probs.sort()
        n = len(probs)
        log(f"  top-1 lang prob: p10={probs[n // 10]:.2f} p50={probs[n // 2]:.2f} p90={probs[9 * n // 10]:.2f}")
    log(f"  bad/unparseable lines: {bad_lines:,}")
    log(f"  tokens estimate (chars/4): ~{chars // 4:,}")
    log("NOTE: pii spans exist as offsets only in this format; masking is a Phase 4/5 decision.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=1_000_000_000,
                        help="how many bytes of the .zst to download (default 1 GB)")
    args = parser.parse_args()
    md5 = fetch_md5()
    log(f"official md5 (FULL file): {md5}")
    path = range_download(args.bytes)
    stream_docs(path, min(args.bytes, path.stat().st_size))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
