"""Build a Yomitan dictionary from the NAER 樂詞網 term lists (https://terms.naer.edu.tw/download/).

Steps:
  catalog  list every file on the download page into data/catalog.json and source_catalog.tsv
  fetch    catalog, then download the files not yet in data/raw
  build    read the term tables and write dist/naer_terms.zip, index.json and the release notes
  all      fetch and build

Example:
  python build_dictionary.py all
"""

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import opencc
from pypinyin import Style, lazy_pinyin

import naer_fetch
import naer_read

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
ZIP_NAME = "naer_terms.zip"
TITLE = "樂詞網"

# Yomitan's updater fetches INDEX_URL, offers an update when its revision is newer than the installed
# one, then downloads DOWNLOAD_URL. Both URLs stay the same between releases.
REPO = "caocaochan/naer-yomitan"
INDEX_URL = f"https://raw.githubusercontent.com/{REPO}/main/index.json"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/{ZIP_NAME}"

BANK_SIZE = 10000
MAX_HEADWORD_LEN = 16  # Yomitan's default scan length
MAX_SENSES = 12
MAX_LABELS = 4  # domain labels shown per sense; the rest go into the "+N" label's tooltip

# ---------------------------------------------------------------------------
# columns

# Header names, matched after removing spaces. Anything not listed is an extra column.
COL_EN = re.compile(r"^(英文(名稱|詞彙|名詞)?|English)$")
COL_TW = re.compile(r"^(中文(名稱|詞彙|名詞)?|[臺台]灣用語)$")
COL_CN = re.compile(r"^(中國)?大陸(譯名|用語)$")
# Extra columns worth showing, with the label used in the entry (None: value only).
EXTRA = {
    "科": "科", "化學式": None, "符號": "符号", "語言": "语言", "所在國": None, "國籍": None,
    "祖籍": "祖籍", "生歿年": None, "生卒年": None, "專長": None, "領域": None, "獲獎年份": None,
    "作曲家": "作曲家", "藥理分類": None, "德文": "德", "法文": "法", "日文": "日",
    "備註": None, "附註": None,
}
RE_TAG = re.compile(r"</?[A-Za-z][^<>]*>")  # <I>, <p>, <Sub>, <Font html:Color=…> in some cells

opencc_tw2s = opencc.OpenCC("tw2s")
opencc_s2t = opencc.OpenCC("s2t")
CONVERTERS = [opencc_s2t, opencc.OpenCC("s2tw"), opencc.OpenCC("s2hk")]

HAN = "㐀-鿿\U00020000-\U0002ffff"
RE_HAN = re.compile(f"[{HAN}]")
# Brackets in a term cell: notes (故障(測試)), optional characters (分佈群(組)), parts of speech (【动】)
RE_BRACKETS = re.compile(
    r"\([^()]*\)|（[^（）]*）|【[^【】]*】|\[[^\[\]]*\]|〔[^〔〕]*〕|<[^<>]*>|＜[^＜＞]*＞|\{[^{}]*\}|｛[^｛｝]*｝")
OPENING, CLOSING = "(（【[〔<＜{｛", ")）】]〕>＞}｝"
RE_SPACES = re.compile(r"\s+")


def to_simplified(s):
    return opencc_tw2s.convert(s)


# ﹐﹑﹔ are the small-form comma, enumeration comma and semicolon some lists use
SEPARATORS = "；;、，,﹐﹑﹔\n"
NAME_SEPARATORS = "；;﹔\n"  # name lists write 阿巴多，克勞迪歐 (surname, given name)
NAME_LISTS = re.compile(r"人名|音樂家|科學家|姓氏|諾貝爾")
# numbered alternatives: 1.高周波乾燥機 2.高頻率乾燥機, (1)… (2)…, ①… ②…
RE_NUMBERED = re.compile(
    r"(?:(?:^|(?<=\s)|(?<=[；;，,。]))\s*(?:\d{1,2}[.．、)）](?!\d)|[(（]\d{1,2}[)）])|[①-⑳])\s*")


def split_terms(cell, separators=SEPARATORS):
    """Split a cell of synonyms (打斷；放棄；故障(測試；發射)) at separators outside brackets.
    A comma between digits is part of a chemical name (1,2-二氯乙烷), not a separator."""
    if RE_NUMBERED.match(cell.lstrip()):
        cell = RE_NUMBERED.sub("\n", cell)
    out, buf, depth = [], [], 0
    for i, ch in enumerate(cell):
        if ch in OPENING:
            depth += 1
        elif ch in CLOSING:
            depth = max(0, depth - 1)
        if (depth == 0 and ch in separators
                and not (ch in ",，" and 0 < i < len(cell) - 1 and cell[i - 1].isdigit() and cell[i + 1].isdigit())):
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    terms = []
    for t in out:
        # 甘薯／白薯／地瓜 lists synonyms; 非同步／同步 inside a longer name does not
        parts = [p.strip() for p in t.split("／")]
        if len(parts) > 1 and all(RE_SLASH_PART.fullmatch(p) for p in parts):
            terms.extend(parts)
        else:
            terms.append(t.strip())
    return [t for t in terms if t]


RE_SLASH_PART = re.compile(f"[{HAN}]{{1,8}}")


def headword(term, name=False):
    """Lookup form of a term: brackets removed, spaces between Han characters dropped.
    For a name (阿巴多，克勞迪歐) it is the surname, the part before the comma."""
    t = RE_BRACKETS.sub("", term)
    if name:
        t = re.split(r"[，,]", t)[0]
    t = re.sub(f"(?<=[{HAN}])\\s+(?=[{HAN}])", "", t)
    t = RE_SPACES.sub(" ", t).strip(" -－—·．.。:：")
    return t


def usable(hw):
    # "_" marks a blank to fill in (___區戶政事務所)
    return bool(hw) and RE_HAN.search(hw) is not None and len(hw) <= MAX_HEADWORD_LEN and "_" not in hw


def clean_en(s):
    return RE_SPACES.sub(" ", s).strip().strip("；;,")


def en_key(en):
    """Grouping key for English equivalents: case, spacing, and {=H} / [= optical maser] notes ignored."""
    k = en.lower().replace("（", "(").replace("）", ")").replace("｛", "{").replace("｝", "}")
    k = re.sub(r"\{[^{}]*\}|\[=[^\[\]]*\]", "", k)
    k = re.sub(r"\s*([()/;,])\s*", r"\1", k)
    return RE_SPACES.sub(" ", k).strip() or en.lower()


def better_en(new, old):
    """Of two spellings that group together, prefer the one with fewer capitals (yield over Yield)."""
    return new.lower() == old.lower() and sum(c.isupper() for c in new) < sum(c.isupper() for c in old)


def display_zh(s):
    """Simplified display text with full-width brackets, so 应力(机械性) and 应力（机械性） merge."""
    s = to_simplified(s).replace("(", "（").replace(")", "）")
    s = re.sub(f",\\s*(?=[{HAN}])", "，", s)  # 阿巴多,克劳迪欧 -> 阿巴多，克劳迪欧
    s = re.sub(r"\s*([（）；，、：])\s*", r"\1", s)  # 氢 （ 气体元素；符号 H ） -> 氢（气体元素；符号H）
    return RE_SPACES.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# domains

# Labels for file titles the general rule below shortens badly; matched against the traditional title.
LABELS = [
    (r"^國防部新編國軍簡明美華軍語辭典$", "军语"),
    (r"^外國學者人名譯名-諾貝爾獎得主", "诺贝尔奖得主"),
    (r"^外國學者人名譯名-常見姓氏", "外国姓氏"),
    (r"^外國學者人名譯名-各領域學者人名", "外国学者"),
    (r"^外國地名譯名$", "外国地名"),
    (r"^中央研究院臺灣物種名錄$", "台湾物种"),
    (r"^化學名詞-常見萜類", "萜类"),
    (r"^化學名詞-常見生物鹼", "生物碱"),
    (r"^化學名詞-不飽和雜環化合物", "杂环化合物"),
    (r"^化學名詞-化學元素一覽表$", "化学元素"),
    (r"^化學名詞-有機化合物之基$", "有机基"),
    (r"^化學名詞-化學相關科學家$", "化学家"),
    (r"^物理學名詞-物理相關科學家$", "物理学家"),
    (r"^生命科學名詞-科學家譯名$", "科学家"),
    (r"^音樂名詞-音樂歌劇譯名$", "歌剧"),
    (r"^音樂名詞-流行音樂樂團名$", "乐团"),
    (r"^音樂名詞-流行音樂專有名詞音響類$", "音响"),
    (r"^音樂名詞-樂器名$", "乐器"),
    (r"^音樂名詞-(兩岸)?音樂(家|人名)$", "音乐家"),
    (r"^圖書館學與資訊科學", "图书资讯学"),
    (r"^實驗動物及比較醫學", "实验动物医学"),
    (r"^農學名詞-食品科學與營養學", "食品营养"),
    (r"^教育學名詞-教社", "教育社会学"),
    (r"^教育學名詞-特教", "特殊教育"),
    (r"^教育學名詞-科教", "科学教育"),
    (r"^教育學名詞-幼教", "幼儿教育"),
    (r"^生物學名詞-植物", "植物"),
    (r"^教育部特殊專門名詞$", "教育部"),
    (r"^國家教育研究院場所名詞$", "国教院"),
    (r"^科技部政務次長", "科技部"),
    (r"^東南亞6國詞彙$", "警政"),
    (r"^其他$", "机关名称"),
    (r"^考選部雙語詞彙$", "考选部"),
    (r"^地方機關", "地方机关"),
    (r"^中央機關", "中央机关"),
    (r"^出版、社群媒體", "出版传媒"),
    (r"^中小學常用職稱", "学校职称"),
    (r"^一般性常[見用]術語$", "一般术语"),
    (r"^一般公文用語$", "公文"),
    (r"^12年國民基本教育課程綱要", "课纲"),
    (r"^新冠病毒疫情", "新冠疫情"),
    (r"^漢語文化特色詞條$", "汉语文化"),
    (r"^選舉詞彙$", "选举"),
    (r"^(01 )?兩岸常用詞語對照表$", "两岸常用词"),
    (r"^兩岸一般名詞對照$", "两岸常用词"),
]


def domain_label(title):
    """Short label for a file title: 法律學名詞-刑法 -> 刑法, 化學名詞-兩岸化學名詞 -> 化學."""
    for pattern, label in LABELS:
        if re.search(pattern, title):
            return label
    # textbook lists: 高中以下數學名詞, 數學名詞-兩岸中小學教科書名詞 -> 中学数学
    m = re.match(r"^高中以下(.+?)(名詞)?$|^(.+?)(名詞)?-兩岸中小學教科書名詞$", title)
    if m:
        return "中学" + to_simplified(m.group(1) or m.group(3))
    t = title.replace("壓縮檔", "").strip()
    head, _, tail = t.partition("-")
    tail = tail.replace("兩岸對照名詞", "").replace("兩岸", "").strip()
    head = head.replace("兩岸對照名詞", "").strip()
    if tail in ("", "中小學教科書名詞") or tail == head or tail.removesuffix("名詞") == head.removesuffix("名詞"):
        label = head + ("教科書" if tail == "中小學教科書名詞" else "")
    else:
        label = tail
    label = re.sub(r"名詞$|名稱$", "", label) or head
    return to_simplified(label)


# ---------------------------------------------------------------------------
# reading the tables

def find_columns(header):
    cols = {"en": None, "tw": None, "cn": None, "extra": {}}
    for i, h in enumerate(header):
        h = RE_SPACES.sub("", h)
        for kind, pattern in (("cn", COL_CN), ("en", COL_EN), ("tw", COL_TW)):
            if pattern.match(h):
                if cols[kind] is None:
                    cols[kind] = i
                break
        else:
            if h in EXTRA and h not in cols["extra"].values():
                cols["extra"][i] = h
    return cols


def records(catalog):
    """Yield (domain title, en, tw cell, cn cell, extras) for every row of every file."""
    for entry in catalog:
        path = naer_fetch.local_path(entry)
        if not path.exists():
            print(f"missing {path}; run the fetch step", flush=True)
            continue
        for member, rows in naer_read.tables(path):
            cols = None
            for n, (_, row) in enumerate(rows):
                if cols is None:
                    # the header is the first row, except for a title row above it in one file
                    found = find_columns(row)
                    if found["tw"] is not None or found["cn"] is not None:
                        cols = found
                    elif n >= 3:
                        print(f"  skipped {entry['title']} / {member}: no Chinese column", flush=True)
                        break
                    continue

                def cell(i):
                    return RE_TAG.sub("", row[i]).strip() if i is not None and i < len(row) else ""

                en, tw, cn = cell(cols["en"]), cell(cols["tw"]), cell(cols["cn"])
                if not (tw or cn):
                    continue
                extras = [(header, cell(i)) for i, header in cols["extra"].items() if cell(i)]
                yield entry, clean_en(en), tw, cn, extras


# ---------------------------------------------------------------------------
# grouping

class Sense:
    __slots__ = ("en", "domains", "counterparts", "originals", "extras", "rows", "order")

    def __init__(self, en, order):
        self.en = en
        self.domains = {}  # label -> full title
        self.counterparts = {"大陆": {}, "台湾": {}}  # side -> {simplified: display}
        self.originals = {}  # bracketed forms of the headword itself (文档（件）)
        self.extras = {}
        self.rows = 0
        self.order = order


class Group:
    __slots__ = ("forms", "trad", "senses")

    def __init__(self):
        self.forms = set()
        self.trad = None  # traditional form as written in a Taiwan column
        self.senses = {}


PROGRESS_EVERY = 100_000


def collect(catalog):
    groups = {}
    n_rows = 0
    n_order = 0
    used_files = set()
    start = time.time()
    for entry, en, tw_cell, cn_cell, extras in records(catalog):
        n_rows += 1
        if n_rows % PROGRESS_EVERY == 0:
            print(f"  {n_rows:,} rows, {len(used_files)} files, {len(groups):,} headwords, {n_order:,} senses "
                  f"({time.time() - start:.0f}s, now {entry['title']})", flush=True)
        title = to_simplified(entry["title"])
        label = domain_label(entry["title"])
        names = NAME_LISTS.search(entry["title"]) is not None
        seps = NAME_SEPARATORS if names else SEPARATORS
        tw_terms = split_terms(tw_cell, seps)
        cn_terms = split_terms(cn_cell, seps)
        sides = [("台湾", t) for t in tw_terms] + [("大陆", t) for t in cn_terms]
        for side, term in sides:
            hw = headword(term, names)
            if not usable(hw):
                continue
            key = to_simplified(hw)
            g = groups.get(key)
            if g is None:
                g = groups[key] = Group()
            g.forms.add(hw)
            used_files.add(entry["path"])
            if side == "台湾" and g.trad is None:
                g.trad = hw
            other_side, other_terms = ("大陆", cn_terms) if side == "台湾" else ("台湾", tw_terms)
            sense_key = en_key(en) if en else "\0" + "；".join(to_simplified(t) for t in other_terms)
            s = g.senses.get(sense_key)
            if s is None:
                n_order += 1
                s = g.senses[sense_key] = Sense(en, n_order)
            elif better_en(en, s.en):
                s.en = en
            s.rows += 1
            s.domains.setdefault(label, title)
            for t in other_terms:
                # the counterpart list leaves out this headword itself
                if to_simplified(headword(t, names)) != key:
                    s.counterparts[other_side].setdefault(display_zh(t), None)
            shown = display_zh(term)
            if shown != key:
                s.originals.setdefault(shown, None)
            for header, value in extras:
                value = display_zh(value)
                if value not in s.extras.values():
                    s.extras.setdefault(header, value)
    return groups, n_rows, len(used_files)


def final_senses(g):
    """Senses in display order. A sense without English (a row of the 兩岸 word lists) is kept only
    for counterparts that no sense with English already shows."""
    shown = {side: set() for side in ("大陆", "台湾")}
    for s in g.senses.values():
        if s.en:
            for side in shown:
                shown[side].update(s.counterparts[side])
    out = []
    for s in g.senses.values():
        if not s.en:
            for side in shown:
                s.counterparts[side] = {c: None for c in s.counterparts[side] if c not in shown[side]}
            if not any(s.counterparts.values()) and not s.extras:
                if any(x.en for x in g.senses.values()):
                    continue
        out.append(s)
    out.sort(key=lambda s: (-len(s.domains), -s.rows, s.order))
    return out[:MAX_SENSES]


# ---------------------------------------------------------------------------
# output

def node(tag, content, kind=None, **extra):
    """Structured-content element; `kind` becomes data-sc-content for styles.css."""
    n = {"tag": tag, "content": content}
    if kind:
        n["data"] = {"content": kind}
    n.update(extra)
    return n


def form_row(simplified, traditional):
    """Traditional / simplified badges, the same header as the HYDCD, chengyu and Wikipedia dictionaries."""
    badges = [node("span", traditional, "traditional-term")]
    if simplified != traditional:
        badges.append(node("span", simplified, "simplified-term"))
    return node("span", [node("span", badges, "terms-parent")], "first-row-parent")


def sense_item(s):
    head = []
    if s.en:
        head.append(node("span", s.en, lang="en"))
    labels = list(s.domains.items())
    for label, title in labels[:MAX_LABELS]:
        head.append(node("span", label, "source-label", title=title))
    if len(labels) > MAX_LABELS:
        rest = "、".join(lbl for lbl, _ in labels[MAX_LABELS:])
        head.append(node("span", f"+{len(labels) - MAX_LABELS}", "source-label", title=rest))
    content = [node("div", head)]
    notes = []
    if s.originals:
        notes.append("、".join(s.originals))
    for side in ("大陆", "台湾"):
        if s.counterparts[side]:
            notes.append(f"{side}：" + "；".join(s.counterparts[side]))
    for header, value in s.extras.items():
        name = EXTRA[header]
        notes.append(f"{name}：{value}" if name else value)
    if notes:
        content.append(node("div", " · ".join(notes), "phonology"))
    return node("li", content)


def reading(word):
    return "".join(lazy_pinyin(word, style=Style.TONE)).replace(" ", "")


def headword_forms(simplified, originals):
    forms = {simplified, *originals}
    for conv in CONVERTERS:
        forms.add(conv.convert(simplified))
    return sorted(forms, key=lambda f: (f != simplified, f))


def build(catalog):
    start = time.time()

    def log(msg):
        print(f"{msg} ({time.time() - start:.0f}s)", flush=True)

    groups, n_rows, n_files = collect(catalog)
    n_senses = sum(len(g.senses) for g in groups.values())
    log(f"read {n_rows:,} rows from {n_files} of {len(catalog)} files into {len(groups):,} headwords, "
        f"{n_senses:,} senses")

    updated = max(parse_date(e["updated"]) for e in catalog)
    index = make_index(len(groups), updated, n_files)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / ZIP_NAME
    n_entries = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=2))
        z.writestr("source_attribution.txt", ATTRIBUTION.format(updated=updated))
        z.write(ROOT / "styles.css", "styles.css")  # shared with the HYDCD, chengyu and Wikipedia dictionaries
        bank, bank_no = [], 0

        def flush():
            nonlocal bank, bank_no
            bank_no += 1
            z.writestr(f"term_bank_{bank_no}.json", json.dumps(bank, ensure_ascii=False, separators=(",", ":")))
            bank = []

        for seq, key in enumerate(sorted(groups), start=1):
            g = groups.pop(key)
            senses = final_senses(g)
            glossary = [{
                "type": "structured-content",
                "content": node("span", [
                    form_row(key, g.trad or opencc_s2t.convert(key)),
                    node("ol", [sense_item(s) for s in senses]),
                ], "naer-entry", lang="zh-Hans"),
            }]
            pinyin = reading(key)
            for form in headword_forms(key, g.forms):
                bank.append([form, pinyin, "", "", 0, glossary, seq, ""])
                n_entries += 1
                if len(bank) >= BANK_SIZE:
                    flush()
                if n_entries % PROGRESS_EVERY == 0:
                    log(f"  {n_entries:,} entries, {seq:,} of {seq + len(groups):,} headwords, "
                        f"{bank_no} term banks")
        if bank:
            flush()
    n_headwords = seq
    log(f"wrote {zip_path}: {n_headwords:,} headwords, {n_entries:,} entries")
    write_repo_files(index, updated, n_headwords, n_entries, n_rows, n_files, zip_path.stat().st_size)


def parse_date(s):
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else "0000-00-00"


def make_index(n_headwords, updated, n_files):
    return {
        "title": TITLE,
        "revision": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "format": 3,
        "sequenced": True,
        "sourceLanguage": "zh",
        "targetLanguage": "en",
        "author": "National Academy for Educational Research (國家教育研究院)",
        "isUpdatable": True,
        "indexUrl": INDEX_URL,
        "downloadUrl": DOWNLOAD_URL,
        "url": f"https://github.com/{REPO}",
        "description": (
            f"{n_headwords:,} Chinese technical and academic terms with their English equivalents and "
            f"subject labels, from {n_files} term lists on the NAER Web of Words (樂詞網), updated up to "
            f"{updated}. Taiwan terms and their mainland Chinese equivalents are both headwords and point to "
            "each other. Simplified and traditional lookup forms; no frequency data."
        ),
        "attribution": (
            "Term data from 國家教育研究院 樂詞網 (National Academy for Educational Research, Web of Words), "
            "https://terms.naer.edu.tw/, used under the site's open data declaration (政府網站資料開放宣告). "
            "Terms were regrouped by headword and converted to simplified Chinese."
        ),
    }


def write_repo_files(index, updated, n_headwords, n_entries, n_rows, n_files, zip_size):
    """index.json and source_attribution.txt at the repo root, README stats."""
    (ROOT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "source_attribution.txt").write_text(ATTRIBUTION.format(updated=updated), encoding="utf-8")
    stats = (
        f"- {n_headwords:,} headwords from {n_rows:,} rows in {n_files} NAER term lists (newest update "
        f"{updated}), {n_entries:,} simplified and traditional lookup entries\n"
        f"- Yomitan package: dictionary format 3, {zip_size / 1e6:.0f} MB"
    )
    readme = ROOT / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        text = re.sub(r"(<!-- stats:start -->\n).*?(<!-- stats:end -->)",
                      lambda m: m.group(1) + stats + "\n" + m.group(2), text, flags=re.S)
        readme.write_text(text, encoding="utf-8")

    # release title and notes for the CI release step
    (DIST_DIR / "release_title.txt").write_text(
        f"NAER term lists to {updated} (built {index['revision'][:10]})", encoding="utf-8")
    notes = (
        f"樂詞網 dictionary built from the NAER Web of Words term lists, newest list update {updated}.\n\n"
        f"{stats}\n\n"
        f"Import `{ZIP_NAME}` into Yomitan. It is updatable: Yomitan's **Check for updates** finds new "
        f"releases through `{INDEX_URL}`.\n\n"
        "Term data from 國家教育研究院 樂詞網 (https://terms.naer.edu.tw/), used under its open data "
        "declaration (政府網站資料開放宣告).\n"
    )
    (DIST_DIR / "release_notes.md").write_text(notes, encoding="utf-8")


ATTRIBUTION = """Source attribution

國家教育研究院 樂詞網 (National Academy for Educational Research, Web of Words)
https://terms.naer.edu.tw/
Downloads: https://terms.naer.edu.tw/download/ (term lists updated up to {updated})

Used under the site's 政府網站資料開放宣告 (open data declaration,
https://terms.naer.edu.tw/mysite/about/2/), which allows free, non-exclusive,
irrevocable reuse, adaptation and redistribution provided the source is cited.

Changes: rows from all downloadable term lists were regrouped by Chinese
headword, with each English equivalent listed once together with the
subject areas it comes from. Taiwan terms and mainland Chinese equivalents
(中國大陸譯名) are both used as headwords. Chinese text is converted to
simplified Chinese with OpenCC; readings come from pypinyin and may be wrong
for some terms. This is not an official NAER product.
"""


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["catalog", "fetch", "build", "all"])
    p.add_argument("--refresh", action="store_true", help="download every file again")
    args = p.parse_args()

    if args.step == "catalog":
        naer_fetch.catalog()
    if args.step in ("fetch", "all"):
        catalog = naer_fetch.catalog()
        naer_fetch.download(catalog, force=args.refresh)
    if args.step in ("build", "all"):
        build(naer_fetch.load_catalog())


if __name__ == "__main__":
    main()
