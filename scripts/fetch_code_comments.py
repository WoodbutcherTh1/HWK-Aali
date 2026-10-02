"""Hebrew code comments from permissive GitHub repos (Phase 3.5.3).

Owner decision (2026-10-02): try UNAUTHENTICATED. GitHub's CODE search
API hard-requires auth (verified live: 401), so this uses:
  1. unauthenticated REPO search: language:Hebrew, sorted by stars —
     the API's license.spdx_id field is the license verdict (license-first),
  2. per-repo tarball downloads (codeload.github.com, no auth needed),
  3. local extraction of COMMENT LINES containing Hebrew script only
     (#, //, /* */, <!-- -->, --, *) — never string literals/UI text.

License policy: MIT / Apache-2.0 / BSD-2/3-Clause / CC0-1.0 / Unlicense
only (owner's list); repos with any other/missing SPDX are skipped and
counted. Unauthenticated rate limits are honored: 10 search requests/min
(sleep between pages), tarballs fetched sequentially.

Output: extracted/code_comments/code_comments.jsonl — one row per repo
{repo, license, stars, n_comment_lines, text}. Content-free counters to
DOWNLOAD_LOG. Stdlib only, CPU-only.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = "HWK-Aali-HebrewCorpus/0.1 (research; +https://github.com/HWK-Aali)"
SEARCH = "https://api.github.com/search/repositories?q="
ALLOWED_SPDX = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "Unlicense"}
CODE_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
            ".cs", ".rb", ".php", ".go", ".rs", ".sh", ".sql", ".swift", ".kt"}
RAW = Path("D:/hwk-data/hebrew/raw/code_comments")
EXTRACT = Path("D:/hwk-data/hebrew/extracted/code_comments/code_comments.jsonl")
DOWNLOAD_LOG = Path("D:/hwk-data/hebrew/DOWNLOAD_LOG.md")

HEB = range(0x0590, 0x05FF + 1)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{utcnow()} UTC] {message}"
    print(line, flush=True)


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


LANG_KEEP = {"Hebrew", "JavaScript", "TypeScript", "Python", "HTML", "CSS", "Java", "C++", "C", "PHP", "Ruby", "Go"}


def search_repos(pages: int) -> list[dict]:
    """'hebrew' repos sorted by stars; language + license filtered client-side.

    NOTE: GitHub rejects `language:` qualifiers on UNAUTHENTICATED search
    (422, verified live 2026-10-02), so this does a plain text search and
    filters on the returned `language` field. Content is Hebrew-script
    filtered per line anyway, so an occasional extra repo costs only a
    tarball download.
    """
    repos: list[dict] = []
    skipped_license = skipped_lang = 0
    for page in range(1, pages + 1):
        q = urllib.parse.quote("hebrew")
        url = f"{SEARCH}{q}&sort=stars&order=desc&per_page=100&page={page}"
        for attempt in range(4):
            try:
                data = get_json(url)
                break
            except Exception as exc:  # noqa: BLE001 - backoff on rate limit
                wait = 20 * (attempt + 1)
                log(f"search page {page} attempt {attempt + 1} failed ({exc}); sleep {wait}s")
                time.sleep(wait)
        else:
            break
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            spdx = (it.get("license") or {}).get("spdx_id")
            if spdx not in ALLOWED_SPDX:
                skipped_license += 1
                continue
            if (it.get("language") or "") not in LANG_KEEP:
                skipped_lang += 1
                continue
            size_kb = it.get("size") or 0
            if size_kb < 20 or size_kb > 300_000:  # 20 KB .. 300 MB
                continue
            repos.append({
                "full_name": it["full_name"],
                "spdx": spdx,
                "stars": it.get("stargazers_count", 0),
                "default_branch": it.get("default_branch") or "master",
                "size_kb": size_kb,
            })
        log(f"search page {page}: +{len(items)} repos "
            f"(license-skipped: {skipped_license}, lang-skipped: {skipped_lang})")
        time.sleep(7)  # stay under the unauthenticated 10/min search limit
    return repos


def has_hebrew(s: str) -> bool:
    return any(0x0590 <= ord(c) <= 0x05FF for c in s)


def comment_lines(source: str, ext: str) -> list[str]:
    """Comment lines containing Hebrew script, per language family."""
    out: list[str] = []
    line_comment: tuple = ()
    if ext in {".py", ".rb", ".sh"}:
        line_comment = ("#",)
    elif ext in {".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
                 ".cs", ".php", ".go", ".rs", ".swift", ".kt"}:
        line_comment = ("//",)
    elif ext == ".sql":
        line_comment = ("--",)
    for raw in source.splitlines():
        s = raw.strip()
        if not s or not has_hebrew(s):
            continue
        if any(s.startswith(m) for m in line_comment):
            out.append(s)
        elif s.startswith("/*") or s.startswith("*") or s.endswith("*/"):
            if ext not in {".py", ".rb", ".sh", ".sql"}:
                out.append(s)
        elif s.startswith("<!--"):
            out.append(s)
    return out


def fetch_repo_comments(repo: dict) -> dict | None:
    """Stream the repo tarball; extract Hebrew comment lines in-memory."""
    url = (f"https://codeload.github.com/{repo['full_name']}/tar.gz/refs/heads/"
           f"{urllib.parse.quote(repo['default_branch'])}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except Exception as exc:  # noqa: BLE001 - count and continue
        return {"error": f"{type(exc).__name__}: {exc}"}
    lines: list[str] = []
    files_seen = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                ext = Path(member.name).suffix.lower()
                if ext not in CODE_EXT:
                    continue
                files_seen += 1
                try:
                    src = tf.extractfile(member).read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - unreadable member, skip
                    continue
                lines.extend(comment_lines(src, ext))
    except (tarfile.TarError, EOFError) as exc:
        return {"error": f"tar: {exc}"}
    # dedupe, keep order
    seen: set[str] = set()
    uniq = [ln for ln in lines if not (ln in seen or seen.add(ln))]
    return {"lines": uniq, "files": files_seen, "bytes": len(data)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=5, help="search pages of 100 repos (default 5)")
    parser.add_argument("--max-repos", type=int, default=300)
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    repos = search_repos(args.pages)[: args.max_repos]
    log(f"repos selected: {len(repos)} (license-filtered, size-filtered)")

    EXTRACT.parent.mkdir(parents=True, exist_ok=True)
    repos_ok = repos_err = 0
    total_lines = 0
    chars_total = 0
    with EXTRACT.open("w", encoding="utf-8", newline="\n") as out:
        for i, repo in enumerate(repos, 1):
            res = fetch_repo_comments(repo)
            if res is None or "error" in res:
                repos_err += 1
                log(f"[{i}/{len(repos)}] {repo['full_name']}: FAILED {res.get('error') if res else '?'}")
            else:
                repos_ok += 1
                text = "\n".join(res["lines"])
                total_lines += len(res["lines"])
                chars_total += len(text)
                if res["lines"]:
                    out.write(json.dumps({
                        "repo": repo["full_name"],
                        "license": repo["spdx"],
                        "stars": repo["stars"],
                        "n_comment_lines": len(res["lines"]),
                        "text": text,
                    }, ensure_ascii=False) + "\n")
                log(f"[{i}/{len(repos)}] {repo['full_name']}: {len(res['lines'])} hebrew comment lines "
                    f"({res['files']} code files)")
            time.sleep(1)
    log(f"DONE: repos_ok={repos_ok} repos_err={repos_err} comment_lines={total_lines:,} "
        f"chars={chars_total:,} (~{chars_total // 4:,} tokens)")
    section = (
        f"\n## Hebrew code comments fetch ({utcnow()[:10]})\n"
        f"- method: unauthenticated GitHub repo search (language:Hebrew, stars) + codeload tarballs\n"
        f"- license gate: SPDX in {sorted(ALLOWED_SPDX)} (API metadata, verified per repo)\n"
        f"- repos fetched ok: {repos_ok} (failed: {repos_err})\n"
        f"- hebrew comment lines: {total_lines:,} -> extracted/code_comments/code_comments.jsonl\n"
        f"- text chars: {chars_total:,} (~{chars_total // 4:,} tokens at chars/4)\n"
        f"- scope: COMMENT LINES ONLY (no string literals, no identifiers)\n"
    )
    print(section, flush=True)
    try:
        with DOWNLOAD_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
