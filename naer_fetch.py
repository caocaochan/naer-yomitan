"""Catalog and download the NAER 樂詞網 term files (https://terms.naer.edu.tw/download/)."""

import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
CATALOG = ROOT / "data" / "catalog.json"
SOURCE_CATALOG = ROOT / "source_catalog.tsv"
SITE = "https://terms.naer.edu.tw"
USER_AGENT = "naer-yomitan-builder/1.0"

# Download categories on the site: id -> label
CATEGORIES = {1: "學術名詞", 3: "雙語詞彙", 4: "教科書名詞", 10: "兩岸對照"}

RE_ROW = re.compile(r'<div role="row" class="tr">(.*?)(?=<div role="row"|</div>\s*</div>\s*</div>\s*$|\Z)', re.S)
RE_CELL = re.compile(r'<div role="cell" class="td" aria-label="([^"]+)">(.*?)</div>', re.S)
RE_HREF = re.compile(r'href="(/media/[^"]+)"')
RE_TAG = re.compile(r"<[^>]+>")


# The site's certificate chain (Taiwan government CA) lacks a Subject Key Identifier, which
# Python 3.13+ rejects under VERIFY_X509_STRICT. Keep normal chain and hostname checks, drop
# only the strict flag.
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.verify_flags &= ~ssl.VERIFY_X509_STRICT


def _open(url, start=0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if start:
        req.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT)


def _quote_url(path):
    return SITE + urllib.parse.quote(path, safe="/%_-.")


def text(cell):
    return re.sub(r"\s+", " ", html.unescape(RE_TAG.sub(" ", cell))).strip()


def catalog():
    """List every downloadable file: category, title, file path, update date."""
    files = []
    for cat, label in CATEGORIES.items():
        url = f"{SITE}/download/{cat}/?refine=1&query_term=&page=1&page_size=1000&order_by=content"
        with _open(url) as r:
            page = r.read().decode("utf-8")
        n = 0
        for row in RE_ROW.findall(page):
            m = RE_HREF.search(row)
            if not m:
                continue
            cells = {k: text(v) for k, v in RE_CELL.findall(row)}
            path = urllib.parse.unquote(html.unescape(m.group(1)))
            files.append({
                "category": cat,
                "category_label": label,
                "title": cells.get("領域類別") or Path(path).stem,
                "updated": cells.get("更新日期", ""),
                "path": path,
            })
            n += 1
        print(f"category {cat} {label}: {n} files", flush=True)
    if len(files) < 100:
        sys.exit(f"only {len(files)} files listed; the download page layout may have changed")
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(files, ensure_ascii=False, indent=1), encoding="utf-8")
    # Committed copy: CI rebuilds when it changes, and its diff shows which lists were updated.
    lines = sorted(f"{e['title']}\t{e['updated']}\t{e['path']}" for e in files)
    SOURCE_CATALOG.write_text("title\tupdated\tpath\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return files


def local_path(entry):
    # /media/terms_data/<dir>/<name>; keep <dir> so equal names in different dirs don't clash
    parts = entry["path"].split("/")
    return RAW_DIR / parts[-2] / parts[-1]


def download(files, force=False):
    # an updated list gets a new file name on the site; drop files no longer listed
    wanted = {local_path(e) for e in files}
    if RAW_DIR.exists():
        for old in RAW_DIR.rglob("*"):
            if old.is_file() and old not in wanted:
                print(f"removing {old.relative_to(RAW_DIR)} (no longer listed)", flush=True)
                old.unlink()
    for e in files:
        dest = local_path(e)
        if dest.exists() and dest.stat().st_size and not force:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(1, 6):
            try:
                with _open(_quote_url(e["path"])) as r, open(tmp, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                tmp.replace(dest)
                print(f"got {dest.relative_to(RAW_DIR)} ({dest.stat().st_size:,} B)", flush=True)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as ex:
                print(f"  {e['path']}: attempt {attempt} failed: {ex!r}", flush=True)
                time.sleep(5 * attempt)
        else:
            sys.exit(f"could not download {e['path']}")
        time.sleep(0.5)  # be gentle with the server


def load_catalog():
    if not CATALOG.exists():
        sys.exit("no data/catalog.json; run the fetch step first")
    return json.loads(CATALOG.read_text(encoding="utf-8"))
