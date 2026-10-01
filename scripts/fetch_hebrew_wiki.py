"""Resume-safe Wikimedia dump fetcher for the Hebrew data pipeline.

Phase 2 of the Hebrew collection (tasks/hebrew-data-collection.md):
downloads the pages-articles dump parts for a wiki (default hewiki),
verifies each part's SHA1 against the official dumpstatus.json manifest,
and logs content-free progress lines to D:/hwk-data/hebrew/DOWNLOAD_LOG.md.

Design (owner rules): license-first (dumps only, verified CC BY-SA in
LICENSE_REPORT.md), resume-safe (HTTP Range + partial-file continuation),
checksum-verification MANDATORY before a part counts as downloaded,
CPU-only, stdlib-only, content-free logging (file names + sizes, never
page content).

Usage:
  python scripts/fetch_hebrew_wiki.py --wiki hewiki --date 20261001
  python scripts/fetch_hebrew_wiki.py --wiki hewikisource --date 20261001
Exit codes: 0 = all parts downloaded AND sha1-verified; 1 = verification
or download failure (safe to re-run: it resumes).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

OUT_ROOT = Path("D:/hwk-data/hebrew/raw")
LOG_FILE = Path("D:/hwk-data/hebrew/DOWNLOAD_LOG.md")
STATUS_URL = "https://dumps.wikimedia.org/{wiki}/{date}/dumpstatus.json"
FILE_URL = "https://dumps.wikimedia.org/{wiki}/{date}/{fname}"
CHUNK = 1 << 20  # 1 MiB
# Wikimedia dumps block the default python UA (403) - their UA policy
# wants an identified client. Content-free UA: tool + purpose, no PII.
USER_AGENT = "HWK-Aali-hebrew-corpus/1.0 (local ML training; stdlib urllib)"


def _open(url: str, timeout: int, range_header: str = ""):
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    if range_header:
        request.add_header("Range", range_header)
    return urllib.request.urlopen(request, timeout=timeout)


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def dump_parts(wiki: str, date: str) -> list[dict]:
    """The pages-articles dump parts + official sha1s from dumpstatus.json."""
    url = STATUS_URL.format(wiki=wiki, date=date)
    with _open(url, timeout=60) as response:
        status = json.loads(response.read().decode("utf-8"))
    manifest_path = OUT_ROOT / f"{wiki}-{date}-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(status, indent=1), encoding="utf-8")
    job = status["jobs"].get("articlesdump", {})
    if job.get("status") != "done":
        raise SystemExit(f"articlesdump status is {job.get('status')!r}, not done - wait for the dump to finish")
    parts = []
    for name, info in sorted(job.get("files", {}).items()):
        if not name.endswith(".bz2") or "multistream" in name:
            continue
        parts.append({
            "name": name,
            "size": int(info["size"]),
            "sha1": info["sha1"],
            "url": FILE_URL.format(wiki=wiki, date=date, fname=name),
        })
    if not parts:
        raise SystemExit("no pages-articles .bz2 parts found in the manifest")
    return parts


def fetch_part(part: dict, wiki: str, date: str) -> Path:
    """Download one part with resume; returns the verified local path."""
    target = OUT_ROOT / wiki / part["name"]
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_size, expected_sha1 = part["size"], part["sha1"]

    # Already complete AND verified? Skip fast (idempotent re-runs).
    if target.exists() and target.stat().st_size == expected_size:
        if sha1_of(target) == expected_sha1:
            log(f"{wiki}/{part['name']}: already present, sha1 OK ({expected_size} bytes)")
            return target
        log(f"{wiki}/{part['name']}: size matches but sha1 MISMATCH - redownloading")
        target.unlink()

    while True:
        have = target.stat().st_size if target.exists() else 0
        if have >= expected_size and target.exists():
            break
        try:
            with _open(part["url"], timeout=120,
                       range_header=(f"bytes={have}-" if have else "")) as response, \
                    target.open("ab") as handle:
                while chunk := response.read(CHUNK):
                    handle.write(chunk)
        except Exception as exc:  # noqa: BLE001 - resume on the next loop
            log(f"{wiki}/{part['name']}: connection issue ({exc}) - resuming")
            time.sleep(5)
        have = target.stat().st_size if target.exists() else 0
        log(f"{wiki}/{part['name']}: {have}/{expected_size} bytes")
        if have >= expected_size:
            break

    digest = sha1_of(target)
    if digest != expected_sha1 or target.stat().st_size != expected_size:
        target.unlink()
        raise SystemExit(f"SHA1/SIZE verification FAILED for {part['name']} - deleted; re-run to retry")
    log(f"{wiki}/{part['name']}: VERIFIED sha1={digest} ({expected_size} bytes)")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default="hewiki")
    parser.add_argument("--date", default="",
                        help="dump date YYYYMMDD (default: today UTC)")
    args = parser.parse_args()
    date = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")

    parts = dump_parts(args.wiki, date)
    total = sum(p["size"] for p in parts)
    log(f"fetch start: {args.wiki} {date}: {len(parts)} parts, {total} bytes total")
    for part in parts:
        fetch_part(part, args.wiki, date)
    log(f"fetch DONE: {args.wiki} {date}: all {len(parts)} parts sha1-verified")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
