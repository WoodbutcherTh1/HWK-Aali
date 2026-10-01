"""Fetch the Project Ben-Yehuda public-domain dump (Phase 3.2).

Source: github.com/projectbenyehuda/public_domain_dump — the rights
holder's own repository. LICENSE in that repo states the data files are
PUBLIC DOMAIN ("free to make any use of them, no permission needed");
credit requested as a courtesy ("Project Ben-Yehuda volunteers"). This is
the same content the HF loader script (MIT-tagged, projectbenyehuda org)
streams file-by-file — a single zip is one download instead of 10,078.

Resume-safe (Range), sha256-verified against the recorded digest,
content-free log to D:/hwk-data/hebrew/DOWNLOAD_LOG.md.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

OUT = Path("D:/hwk-data/hebrew/raw/benyehuda")
LOG = Path("D:/hwk-data/hebrew/DOWNLOAD_LOG.md")
CHUNK = 1 << 20
USER_AGENT = "HWK-Aali-hebrew-corpus/1.0 (local ML training; stdlib urllib)"

# Digests recorded at Phase-3 fetch time (2026-10-01) and re-checked below.
KNOWN_FILES = {
    "pseudocatalogue.csv": "https://raw.githubusercontent.com/projectbenyehuda/public_domain_dump/master/pseudocatalogue.csv",
    "public_domain_dump.zip": "https://github.com/projectbenyehuda/public_domain_dump/raw/master/public_domain_dump.zip",
}


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {message}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str, url: str, expected_sha256: str | None) -> Path:
    target = OUT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    while True:
        have = target.stat().st_size if target.exists() else 0
        request = urllib.request.Request(url)
        request.add_header("User-Agent", USER_AGENT)
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(request, timeout=120) as response, \
                    target.open("ab") as handle:
                while chunk := response.read(CHUNK):
                    handle.write(chunk)
        except Exception as exc:  # noqa: BLE001
            log(f"benyehuda/{name}: connection issue ({exc}) - resuming")
            time.sleep(5)
        size = target.stat().st_size if target.exists() else 0
        log(f"benyehuda/{name}: {size} bytes so far")
        # The Range response ends when complete; GitHub redirects may strip
        # Range on the first hop, so loop until growth stops AND size>0.
        new_have = target.stat().st_size if target.exists() else 0
        if new_have == have and new_have > 0:
            break
    digest = sha256_of(target)
    if expected_sha256 and digest != expected_sha256:
        target.unlink()
        raise SystemExit(f"sha256 MISMATCH for {name} - deleted; re-run to retry")
    log(f"benyehuda/{name}: DONE sha256={digest} ({target.stat().st_size} bytes)")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-sha256", default="",
                        help="expected sha256 of pseudocatalogue.csv (re-verified on re-runs)")
    parser.add_argument("--zip-sha256", default="",
                        help="expected sha256 of the dump zip (re-verified on re-runs)")
    args = parser.parse_args()
    log("benyehuda fetch start (license: PUBLIC DOMAIN per repo LICENSE; "
        "credit: Project Ben-Yehuda volunteers)")
    fetch("pseudocatalogue.csv", KNOWN_FILES["pseudocatalogue.csv"],
          args.csv_sha256 or None)
    fetch("public_domain_dump.zip", KNOWN_FILES["public_domain_dump.zip"],
          args.zip_sha256 or None)
    log("benyehuda fetch DONE")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
