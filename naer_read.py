"""Read the tables inside the NAER download archives (ODS spreadsheets, a few DOCX/XLSX files)."""

import io
import zipfile
from pathlib import Path
from xml.etree.ElementTree import iterparse

ODS_T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
ODS_X = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

MAX_REPEAT = 64  # repeated empty cells/rows at the end of a sheet can claim thousands


def _ods_text(node, out):
    """Text of an ODS paragraph; <text:s c="n"/> stands for n spaces."""
    if node.tag == ODS_X + "s":
        out.append(" " * int(node.get(ODS_X + "c", "1")))
    elif node.tag == ODS_X + "tab":
        out.append(" ")
    elif node.tag == ODS_X + "line-break":
        out.append("\n")
    elif node.text:
        out.append(node.text)
    for child in node:
        _ods_text(child, out)
        if child.tail:
            out.append(child.tail)


def _ods_cell(cell):
    lines = []
    for p in cell.findall(ODS_X + "p"):
        out = []
        _ods_text(p, out)
        lines.append("".join(out))
    return "\n".join(lines).strip()


def ods_rows(data):
    """Yield (sheet, [cell, ...]) for every non-empty row."""
    with zipfile.ZipFile(io.BytesIO(data)) as z, z.open("content.xml") as f:
        sheet = None
        for ev, el in iterparse(f, events=("start", "end")):
            if ev == "start":
                if el.tag == ODS_T + "table":
                    sheet = el.get(ODS_T + "name")
                continue
            if el.tag != ODS_T + "table-row":
                continue
            out = []
            for c in el:
                if c.tag not in (ODS_T + "table-cell", ODS_T + "covered-table-cell"):
                    continue
                rep = min(int(c.get(ODS_T + "number-columns-repeated", "1")), MAX_REPEAT)
                out.extend([_ods_cell(c)] * rep)
            rep = min(int(el.get(ODS_T + "number-rows-repeated", "1")), MAX_REPEAT)
            el.clear()
            while out and not out[-1]:
                out.pop()
            if out:
                for _ in range(rep):
                    yield sheet, out


def docx_rows(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z, z.open("word/document.xml") as f:
        n_table = 0
        for ev, el in iterparse(f, events=("end",)):
            if el.tag == W + "tbl":
                n_table += 1
                el.clear()
            elif el.tag == W + "tr":
                cells = []
                for tc in el.findall(W + "tc"):
                    paras = ["".join(t.text or "" for t in p.iter(W + "t")) for p in tc.iter(W + "p")]
                    cells.append("\n".join(paras).strip())
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    yield f"table{n_table + 1}", cells
                # don't clear: the enclosing tbl clears it


def xlsx_rows(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as f:
                for ev, el in iterparse(f, events=("end",)):
                    if el.tag == S + "si":
                        shared.append("".join(t.text or "" for t in el.iter(S + "t")))
                        el.clear()
        sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        for name in sheets:
            with z.open(name) as f:
                for ev, el in iterparse(f, events=("end",)):
                    if el.tag != S + "row":
                        continue
                    cells = {}
                    for c in el.findall(S + "c"):
                        ref = c.get("r", "")
                        col = 0
                        for ch in ref:
                            if ch.isalpha():
                                col = col * 26 + ord(ch.upper()) - 64
                        v = c.find(S + "v")
                        if c.get("t") == "s" and v is not None:
                            val = shared[int(v.text)]
                        elif c.get("t") == "inlineStr":
                            val = "".join(t.text or "" for t in c.iter(S + "t"))
                        else:
                            val = v.text if v is not None and v.text else ""
                        cells[col - 1] = val.strip()
                    el.clear()
                    if cells:
                        out = [cells.get(i, "") for i in range(max(cells) + 1)]
                        while out and not out[-1]:
                            out.pop()
                        if out:
                            yield Path(name).stem, out


READERS = {".ods": ods_rows, ".docx": docx_rows, ".xlsx": xlsx_rows}


def member_name(info):
    """Inner file names are often Big5/UTF-8 without the UTF-8 flag; zipfile decodes them as cp437."""
    name = info.filename
    if not info.flag_bits & 0x800:
        raw = name.encode("cp437", "replace")
        for enc in ("utf-8", "cp950"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return name


def tables(path):
    """Yield (member, sheet, rows) for each readable table in a downloaded archive.

    Rows are lists of strings; the first row of each member is normally its header.
    """
    path = Path(path)
    if path.suffix.lower() in READERS:
        yield path.name, READERS[path.suffix.lower()](path.read_bytes())
        return
    if path.suffix.lower() != ".zip":
        return
    with zipfile.ZipFile(path) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            name = member_name(info)
            reader = READERS.get(Path(name).suffix.lower())
            if reader and not info.is_dir():
                yield name, reader(z.read(info))
