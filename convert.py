# -*- coding: utf-8 -*-
"""Convert the two watermarked PDFs to Markdown while preserving layout semantics.

Usage: python convert.py <input.pdf> <output.md> <assets_dir> <asset_prefix>
"""
import sys, os, re, json, statistics, time, shutil, tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pymupdf

import mermaid_diagram

# 是否在 Mermaid 代码块后附带可折叠的原图（便于核对保真度）
# False 时只保留语义化结果，且不为已转成功的图渲染 PNG
KEEP_SOURCE_IMAGE = False
DIAGRAM_LABELS = {"sequence": "时序图", "flowchart": "流程图"}

# ---------------------------------------------------------------- utilities

BULLET_CHARS = set("●•○▪◦‣·∙⋅")
NOISE_RE = re.compile(r"^\d+\s*/\s*\d+$")
NUMBERED_RE = re.compile(r"^(\d+)\s*[.、]\s*")
HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+)+|附录\s*[A-Z]|[A-Z]\.\d+)\s*")
BOM = "\ufeff"
ZW = "\u200b\u200c\u200d"


def clean_text(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = s.replace(BOM, "")
    for ch in ZW:
        s = s.replace(ch, "")
    return s


def is_noise(text: str, y0: float, font: str, size: float) -> bool:
    t = text.strip()
    if not t:
        return True
    if "复制代码" in t:
        return True
    if NOISE_RE.match(t) and y0 > 780:
        return True
    if font == "Hei" and size > 40:
        return True
    return False


def rect_contains(rect, x, y, margin=0.0):
    return (rect.x0 - margin <= x <= rect.x1 + margin and
            rect.y0 - margin <= y <= rect.y1 + margin)


def rect_intersects(a, b):
    return not (a.x1 < b.x0 or b.x1 < a.x0 or a.y1 < b.y0 or b.y1 < a.y0)


def same_columns(a, b, tol=2.0):
    """两个表格的列边界是否一致（跨页续表会沿用同一套列坐标）。"""
    if not a.col_xs or len(a.col_xs) != len(b.col_xs):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a.col_xs, b.col_xs))


# ---------------------------------------------------------------- data model

@dataclass
class Line:
    page: int
    bbox: pymupdf.Rect
    spans: list
    text: str
    size: float
    font: str
    bold: bool
    marker: Optional[str] = None

    @property
    def x0(self): return self.bbox.x0
    @property
    def y0(self): return self.bbox.y0
    @property
    def x1(self): return self.bbox.x1
    @property
    def y1(self): return self.bbox.y1

    @property
    def cx(self): return (self.bbox.x0 + self.bbox.x1) / 2
    @property
    def cy(self): return (self.bbox.y0 + self.bbox.y1) / 2


@dataclass
class Element:
    kind: str
    page: int
    y: float
    x: float = 0.0
    text: str = ""
    level: int = 0
    marker: str = ""
    lines: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    header: list = field(default_factory=list)
    ncols: int = 0
    bbox: Optional[pymupdf.Rect] = None
    lang: str = ""
    image: str = ""
    col_xs: list = field(default_factory=list)


# ---------------------------------------------------------------- line extraction

def extract_lines(page, pno):
    lines = []
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            spans = [s for s in l["spans"] if s["text"]]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans)
            s0 = spans[0]
            if is_noise(text, l["bbox"][1], s0["font"], s0["size"]):
                continue
            lines.append(Line(
                page=pno,
                bbox=pymupdf.Rect(l["bbox"]),
                spans=spans,
                text=text,
                size=max(s["size"] for s in spans),
                font=s0["font"],
                bold=any("Bold" in s["font"] for s in spans),
            ))
    return lines


# ---------------------------------------------------------------- drawings / grids

def is_dark_grid_color(fill):
    return (fill and abs(fill[0] - 0.09) < 0.02 and
            abs(fill[1] - 0.102) < 0.02 and abs(fill[2] - 0.114) < 0.02)


def merge_v(segs):
    segs = sorted(segs)
    out = []
    for x, y0, y1 in segs:
        # 同一 x 才可能是同一条线；同时要求 y 方向连续，
        # 否则会把上下两个表格里 x 相近的竖线误并成一条
        if (out and abs(out[-1][0] - x) < 0.8
                and out[-1][1] - 1.5 <= y0 <= out[-1][2] + 1.5):
            out[-1] = (out[-1][0], min(out[-1][1], y0), max(out[-1][2], y1))
        else:
            out.append((x, y0, y1))
    return out


def merge_h(segs):
    segs = sorted(segs)
    out = []
    for y, x0, x1 in segs:
        if (out and abs(out[-1][0] - y) < 0.8
                and out[-1][1] - 1.5 <= x0 <= out[-1][2] + 1.5):
            out[-1] = (out[-1][0], min(out[-1][1], x0), max(out[-1][2], x1))
        else:
            out.append((y, x0, x1))
    return out


def get_grid(page):
    V, H = [], []
    for d in page.get_drawings():
        if not is_dark_grid_color(d.get("fill")):
            continue
        r = d["rect"]
        if r.width <= 1.6 and r.height > 3:
            V.append((round((r.x0 + r.x1) / 2, 2), r.y0, r.y1))
        elif r.height <= 1.6 and r.width > 3:
            H.append((round((r.y0 + r.y1) / 2, 2), r.x0, r.x1))
    return merge_v(V), merge_h(H)


def group_grid(V, H):
    """Group vertical/horizontal segments into connected tables."""
    segs = [("v", x, y0, y1) for x, y0, y1 in V] + [("h", y, x0, x1) for y, x0, x1 in H]
    n = len(segs)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    boxes = []
    for typ, a, b, c in segs:
        if typ == "v":
            boxes.append(pymupdf.Rect(a, b, a, c))
        else:
            boxes.append(pymupdf.Rect(b, a, c, a))
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            if (a.x0 - 2 <= b.x1 and b.x0 - 2 <= a.x1 and
                    a.y0 - 2 <= b.y1 and b.y0 - 2 <= a.y1):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    tables = []
    for idxs in groups.values():
        vs = [segs[i][1:] for i in idxs if segs[i][0] == "v"]
        hs = [segs[i][1:] for i in idxs if segs[i][0] == "h"]
        if not vs or not hs:
            continue
        xs = sorted(set(x for x, _, _ in vs))
        ys = sorted(set(y for y, _, _ in hs))
        if len(xs) < 2 or len(ys) < 2:
            continue
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if (x1 - x0) < 40 or (y1 - y0) < 15:
            continue
        tables.append({"V": vs, "H": hs, "xs": xs, "ys": ys,
                       "bbox": pymupdf.Rect(x0, y0, x1, y1)})
    tables.sort(key=lambda t: (t["bbox"].y0, t["bbox"].x0))
    return tables


def detect_figures(page):
    figs = []
    for d in page.get_drawings():
        r = d["rect"]
        fill = d.get("fill")
        color = d.get("color")
        if r.width < 380 or r.height < 80:
            continue
        if fill:
            f = tuple(round(x, 3) for x in fill)
            if f in [(0.922, 0.926, 0.941), (0.969, 0.969, 0.969)]:
                figs.append(pymupdf.Rect(r))
                continue
        if d["type"] == "fs" and color:
            c = tuple(round(x, 3) for x in color)
            if c == (0.961, 0.965, 0.969):
                figs.append(pymupdf.Rect(r))
    # keep outer figures only
    figs.sort(key=lambda r: (r.get_area(), r.y0))
    out = []
    for r in figs:
        if not any(r.x0 >= o.x0 - 2 and r.y0 >= o.y0 - 2 and
                   r.x1 <= o.x1 + 2 and r.y1 <= o.y1 + 2 for o in out):
            out.append(r)
    out.sort(key=lambda r: (r.y0, r.x0))
    return out


def detect_code_containers(page):
    out = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill:
            continue
        if tuple(round(x, 3) for x in fill) != (0.345, 0.408, 0.565):
            continue
        r = d["rect"]
        if r.width >= 300 and r.height >= 20:
            out.append(pymupdf.Rect(r))
    # dedupe nested/identical
    out.sort(key=lambda r: (r.get_area(), r.y0))
    uniq = []
    for r in out:
        if not any(abs(r.x0 - o.x0) < 1 and abs(r.y0 - o.y0) < 1 and
                   abs(r.x1 - o.x1) < 1 and abs(r.y1 - o.y1) < 1 for o in uniq):
            uniq.append(r)
    uniq.sort(key=lambda r: (r.y0, r.x0))
    return uniq


# ---------------------------------------------------------------- inline formatting

def span_style(span):
    font = span["font"]
    if "Consolas" in font or "NSimSun" in font or "Mono" in font:
        return "code"
    if "Bold" in font or "bold" in font:
        return "bold"
    if "Italic" in font or "italic" in font:
        return "italic"
    return "plain"


def format_run(style, text):
    if not text:
        return ""
    if style == "plain":
        return text
    if style == "bold":
        core = text.strip()
        if not core:
            return text
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return f"{lead}**{core}**{trail}"
    if style == "italic":
        core = text.strip()
        if not core:
            return text
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return f"{lead}*{core}*{trail}"
    if style == "code":
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not core:
            return text
        ticks = "`"
        if "`" in core:
            ticks = "``"
            core = f" {core} "
        return f"{lead}{ticks}{core}{ticks}{trail}"
    return text


def make_runs(lines, cell=False):
    """Build (style, text) runs from lines, merging same-style adjacent spans.

    Line breaks are removed (the PDF's line breaks are layout wraps).  For
    table cells, a line that starts a nested list item gets a <br>.
    """
    runs = []
    for li, line in enumerate(lines):
        txt = clean_text("".join(s["text"] for s in line.spans))
        if not txt:
            continue
        if cell and li > 0 and re.match(r"^\s*(?:\d+[.、]|[-•●])\s+", txt):
            runs.append(("br", ""))
        for s in line.spans:
            st = span_style(s)
            t = clean_text(s["text"])
            if not t:
                continue
            if runs and runs[-1][0] == st and st != "br":
                runs[-1] = (st, runs[-1][1] + t)
            else:
                runs.append((st, t))
    return runs


def runs_to_md(runs):
    return "".join(format_run(st, t) if st != "br" else "<br>" for st, t in runs)


def line_text_md(line):
    return runs_to_md(make_runs([line]))


# ---------------------------------------------------------------- tables

def table_cell_lines(table, rect, lines):
    out = []
    for ln in lines:
        if (rect.x0 + 1 <= ln.cx <= rect.x1 - 1 and
                rect.y0 + 1 <= ln.cy <= rect.y1 - 1):
            out.append(ln)
    out.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return out


def nearest_index(vals, v):
    return min(range(len(vals)), key=lambda i: abs(vals[i] - v))


def horizontal_covers(H, y, x0, x1, tol=1.5):
    for hy, hx0, hx1 in H:
        if abs(hy - y) <= tol and hx0 <= x0 + 1.5 and hx1 >= x1 - 1.5:
            return True
    return False


def extract_table(page, tab, lines):
    xs, ys, V, H = tab["xs"], tab["ys"], tab["V"], tab["H"]
    ncols = len(xs) - 1
    nrows = len(ys) - 1
    if ncols <= 0 or nrows <= 0:
        return None
    matrix = [["" for _ in range(ncols)] for _ in range(nrows)]
    spans = []  # (row, cstart, cend, text, y0, x0, x1)
    for i in range(nrows):
        mid = (ys[i] + ys[i + 1]) / 2
        active = [x for x, y0, y1 in V if y0 - 1.5 <= mid <= y1 + 1.5]
        active = sorted(set(active + [xs[0], xs[-1]]))
        # remove points not in xs
        active = [x for x in active if any(abs(x - gx) < 1.0 for gx in xs)]
        if len(active) < 2:
            continue
        for j in range(len(active) - 1):
            xl, xr = active[j], active[j + 1]
            if xr - xl < 2:
                continue
            rect = pymupdf.Rect(xl, ys[i], xr, ys[i + 1])
            cell_lines = table_cell_lines(tab, rect, lines)
            if not cell_lines:
                continue
            md = runs_to_md(make_runs(cell_lines, cell=True)).strip()
            md = re.sub(r"\s+", " ", md).strip()
            if not md:
                continue
            ci = nearest_index(xs, xl)
            cj = nearest_index(xs, xr)
            spans.append((i, ci, cj, md, ys[i], xl, xr))
    # fill rowspans
    for (i, ci, cj, md, ytop, xl, xr) in spans:
        start, end = i, i
        while start > 0 and not horizontal_covers(H, ys[start], xl, xr):
            start -= 1
        while end < nrows - 1 and not horizontal_covers(H, ys[end + 1], xl, xr):
            end += 1
        for r in range(start, end + 1):
            if not matrix[r][ci]:
                matrix[r][ci] = md
    rows = [[c.strip() for c in row] for row in matrix]
    # header detection
    first = rows[0] if rows else []
    nonempty = [c for c in first if c]
    header = False
    if len(nonempty) >= 2 and all(len(c) <= 30 for c in nonempty):
        if not any(re.search(r"[。；;]$", c) for c in nonempty):
            header = True
    if header:
        return {"header": rows[0], "rows": rows[1:], "ncols": ncols,
                "bbox": tab["bbox"], "xs": xs}
    return {"header": [""] * ncols, "rows": rows, "ncols": ncols,
            "bbox": tab["bbox"], "xs": xs}


# ---------------------------------------------------------------- code blocks

def code_lines_in(page, rect, lines):
    out = []
    for ln in lines:
        if not (rect.y0 - 1 <= ln.cy <= rect.y1 + 1 and
                rect.x0 - 1 <= ln.cx <= rect.x1 + 1):
            continue
        fonts = [s["font"] for s in ln.spans if s["text"].strip()]
        if not fonts:
            continue
        if not all(("Consolas" in f or "NSimSun" in f or "Mono" in f) for f in fonts):
            continue
        t = "".join(s["text"] for s in ln.spans)
        if "复制代码" in t:
            continue
        out.append(ln)
    out.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return out


def code_text(lines):
    if not lines:
        return ""
    gaps = [lines[i + 1].y0 - lines[i].y0 for i in range(len(lines) - 1)]
    med = statistics.median(gaps) if gaps else 17.5
    out = []
    prev = None
    for ln in lines:
        t = clean_text("".join(s["text"] for s in ln.spans)).rstrip()
        if prev is not None:
            gap = ln.y0 - prev
            if gap > med * 1.55:
                blanks = max(1, int(round(gap / med)) - 1)
                out.extend([""] * blanks)
        out.append(t)
        prev = ln.y0
    return "\n".join(out)


def classify_code_lang(text):
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if first.startswith("{") or first.startswith("["):
        return "json"
    if re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|HTTP/)", first):
        return "http"
    if re.match(r"^[A-Za-z][\w-]*:\s", first):
        # HTTP headers or YAML
        lines = [l for l in text.splitlines() if l.strip()]
        if any(re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|HTTP/)", l.strip()) for l in lines):
            return "http"
        return "yaml"
    return "text"


def reflow_json(text):
    lines = text.split("\n")
    # Join soft-wrapped lines.  The renderer wraps at the code block's left
    # edge, so continuation lines have zero indentation.
    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            if not cur.strip():
                break
            # string not closed -> definitely a continuation
            if _quote_balance(cur) % 2 == 1:
                cur = cur + nxt.lstrip("\n")
                i += 1
                continue
            # value on the next line after a colon
            if re.search(r":\s*$", cur) and not re.match(r"^\s*[\]\}]", nxt):
                cur = cur + nxt.lstrip(" ")
                i += 1
                continue
            # array/object continuation: previous line ends with a comma+space
            if re.search(r",\s*$", cur) and not re.match(r"^\s*[\]\}\[\{]", nxt):
                # only join if next line starts at column 0 (wrapped)
                if nxt and not nxt.startswith(" "):
                    cur = cur + nxt
                    i += 1
                    continue
            # wrapped mid-token: previous line ends with non-space and next
            # line starts at column 0 and is not a structural token/key line
            if (cur and not cur.endswith(" ") and nxt and not nxt.startswith(" ") and
                    not re.match(r"^[\]\}\[\{]", nxt) and
                    not re.match(r'^"[^"]+"\s*:', nxt)):
                # be conservative: only join if previous line is long
                if len(cur) > 55:
                    cur = cur + nxt
                    i += 1
                    continue
            break
        out.append(cur)
        i += 1
    return "\n".join(out)


def _quote_balance(s):
    bal = 0
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
        elif ch == '"':
            bal += 1
    return bal


# ---------------------------------------------------------------- main conversion

class Converter:
    def __init__(self, pdf_path, out_md, assets_dir, asset_prefix,
                 keep_source_image=None):
        self.doc = pymupdf.open(pdf_path)
        self.out_md = out_md
        self.assets_dir = assets_dir
        self.asset_prefix = asset_prefix
        self.keep_source_image = (KEEP_SOURCE_IMAGE if keep_source_image is None
                                  else keep_source_image)
        self.elements = []
        self.fig_count = 0
        self.heading_size_levels = self.compute_heading_levels()

    def compute_heading_levels(self):
        sizes = set()
        for pno in range(self.doc.page_count):
            page = self.doc[pno]
            for ln in extract_lines(page, pno + 1):
                txt = clean_text(ln.text).strip()
                if not txt:
                    continue
                if ln.size >= 13.4 or (ln.bold and HEADING_NUM_RE.match(txt)):
                    sizes.add(round(ln.size, 1))
        return {s: i + 1 for i, s in enumerate(sorted(sizes, reverse=True))}

    def run(self):
        for pno in range(self.doc.page_count):
            self.process_page(pno + 1)
        self.postprocess()
        self.write()

    def process_page(self, pno):
        page = self.doc[pno - 1]
        lines = extract_lines(page, pno)
        figures = detect_figures(page)
        code_rects = detect_code_containers(page)
        V, H = get_grid(page)
        tables = group_grid(V, H)

        # mark consumed lines
        consumed = set()
        page_elements = []

        # figures
        drawings = page.get_drawings()
        for fi, fr in enumerate(figures):
            for idx, ln in enumerate(lines):
                if rect_contains(fr, ln.cx, ln.cy, margin=1):
                    consumed.add(idx)
            self.fig_count += 1
            path = os.path.join(self.assets_dir,
                                f"{self.asset_prefix}_p{pno:03d}_fig{fi+1}.png")
            # 先尝试把矢量图转成 Mermaid；失败时保留原图
            diagram = mermaid_diagram.convert_figure(page, fr, lines, drawings)
            if diagram is not None and not self.keep_source_image:
                # 只保留语义化结果，不落盘原图
                page_elements.append(Element(
                    kind="mermaid", page=pno, y=fr.y0, x=fr.x0,
                    text=diagram.mermaid, bbox=fr,
                    lang=diagram.kind, marker=",".join(diagram.warnings)))
                continue
            pix = page.get_pixmap(clip=fr, dpi=300, alpha=False)
            pix.save(path)
            if diagram is not None:
                page_elements.append(Element(
                    kind="mermaid", page=pno, y=fr.y0, x=fr.x0,
                    text=diagram.mermaid, image=path, bbox=fr,
                    lang=diagram.kind, marker=",".join(diagram.warnings)))
            else:
                page_elements.append(Element(kind="figure", page=pno, y=fr.y0,
                                             image=path, bbox=fr))

        # code blocks
        for cr in code_rects:
            clines = code_lines_in(page, cr, lines)
            for idx, ln in enumerate(lines):
                if rect_contains(cr, ln.cx, ln.cy, margin=1):
                    consumed.add(idx)
            if not clines:
                continue
            text = code_text(clines)
            page_elements.append(Element(kind="code", page=pno, y=cr.y0,
                                         text=text, bbox=cr,
                                         lang=classify_code_lang(text)))

        # tables
        for tab in tables:
            for idx, ln in enumerate(lines):
                if rect_contains(tab["bbox"], ln.cx, ln.cy, margin=1):
                    consumed.add(idx)
            t = extract_table(page, tab, lines)
            if not t:
                continue
            page_elements.append(Element(
                kind="table", page=pno, y=tab["bbox"].y0, x=tab["bbox"].x0,
                rows=t["rows"], header=t["header"], ncols=t["ncols"],
                bbox=tab["bbox"], col_xs=t["xs"]))

        # remaining lines
        rest = [ln for i, ln in enumerate(lines) if i not in consumed]
        # merge bullet/number marker lines with the text line on the same y
        marker_lines = []
        for ln in rest:
            t = ln.text.strip()
            if ln.x0 < 70 and (t in BULLET_CHARS or re.fullmatch(r"\d+\.", t)):
                marker_lines.append(ln)
        for ml in marker_lines:
            best = None
            for ln in rest:
                if ln is ml or ln.x0 <= ml.x1 - 1:
                    continue
                if abs(ln.cy - ml.cy) <= 6 and ln.x0 > ml.x1 - 2:
                    if best is None or ln.x0 < best.x0:
                        best = ln
            if best is not None:
                best.marker = ml.text.strip()
                ml.marker = "__consumed__"
        rest = [ln for ln in rest if ln.marker != "__consumed__"]

        rest.sort(key=lambda l: (round(l.y0, 1), l.x0))
        text_elements = self.build_text_elements(rest, pno)
        page_elements.extend(text_elements)
        page_elements.sort(key=lambda e: (e.y, e.x))
        self.elements.extend(page_elements)

    # ---------------- text classification ----------------

    def build_text_elements(self, lines, pno):
        els = []
        i = 0
        n = len(lines)
        # collect heading size candidates for this document later; use fixed map
        while i < n:
            ln = lines[i]
            txt = clean_text(ln.text).strip()
            if not txt:
                i += 1
                continue
            # heading
            if self.is_heading(ln, txt):
                text = line_text_md(ln).strip()
                level = self.heading_size_levels.get(round(ln.size, 1), 1)
                els.append(Element(kind="heading", page=pno, y=ln.y0, x=ln.x0,
                                   text=text, level=level))
                i += 1
                continue
            # list item
            is_bullet = (ln.marker in BULLET_CHARS) or (txt[0] in BULLET_CHARS if txt else False)
            num_match = NUMBERED_RE.match(txt)
            is_numbered = (ln.marker is not None and re.fullmatch(r"\d+\.", ln.marker or "")) or bool(num_match)
            if is_bullet or is_numbered:
                item, i = self.collect_list_item(lines, i)
                els.append(item)
                continue
            # paragraph
            para_lines = [ln]
            i += 1
            while i < n:
                nxt = lines[i]
                if self.is_heading(nxt, clean_text(nxt.text).strip()):
                    break
                nt = clean_text(nxt.text).strip()
                if nxt.marker in BULLET_CHARS or (nt and nt[0] in BULLET_CHARS):
                    break
                if NUMBERED_RE.match(nt) or (nxt.marker and re.fullmatch(r"\d+\.", nxt.marker)):
                    break
                if nxt.x0 > 60:
                    break
                if nxt.y0 - para_lines[-1].y0 > 22:
                    break
                para_lines.append(nxt)
                i += 1
            md = runs_to_md(make_runs(para_lines)).strip()
            if md:
                els.append(Element(kind="paragraph", page=pno, y=para_lines[0].y0,
                                   x=para_lines[0].x0, text=md))
        return els

    def collect_list_item(self, lines, i):
        ln = lines[i]
        txt = clean_text(ln.text).strip()
        inline_bullet = (not ln.marker and bool(txt) and txt[0] in BULLET_CHARS)
        marker = ""
        if ln.marker in BULLET_CHARS:
            marker = "-"
            body = txt
        elif ln.marker and re.fullmatch(r"\d+\.", ln.marker):
            marker = ln.marker
            body = txt
        else:
            m = NUMBERED_RE.match(txt)
            if m:
                marker = m.group(1) + "."
                body = txt[m.end():]
            else:
                marker = "-"
                body = txt.lstrip("".join(BULLET_CHARS)).strip()
        # remove a leading bullet char from body
        body = body.lstrip("".join(BULLET_CHARS)).strip()
        item_lines = [ln]
        # We need a synthetic line whose text excludes the marker; easier:
        # build runs from the original line and strip the marker text.
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            nt = clean_text(nxt.text).strip()
            if not nt:
                j += 1
                continue
            if nxt.marker in BULLET_CHARS or (nt and nt[0] in BULLET_CHARS):
                break
            if NUMBERED_RE.match(nt) or (nxt.marker and re.fullmatch(r"\d+\.", nxt.marker)):
                break
            if self.is_heading(nxt, nt):
                break
            if nxt.x0 <= 60:
                break
            if nxt.y0 - item_lines[-1].y0 > 26:
                break
            item_lines.append(nxt)
            j += 1
        # format: remove marker from first line text
        first = item_lines[0]
        first_text = clean_text("".join(s["text"] for s in first.spans))
        if ln.marker in BULLET_CHARS:
            first_text = first_text.lstrip("".join(BULLET_CHARS))
        elif ln.marker and re.fullmatch(r"\d+\.", ln.marker):
            first_text = first_text.lstrip()
        else:
            first_text = NUMBERED_RE.sub("", first_text, count=1)
        # build runs manually for first line without marker
        runs = []
        # simple: strip marker from concatenated text, then re-add bold/code is
        # lost; instead, format each span and remove marker from the first plain run
        first_runs = []
        removed = False
        for s in first.spans:
            st = span_style(s)
            t = clean_text(s["text"])
            if not removed:
                if st == "plain":
                    if ln.marker in BULLET_CHARS or inline_bullet:
                        t2 = t.lstrip("".join(BULLET_CHARS))
                    else:
                        t2 = NUMBERED_RE.sub("", t, count=1)
                    if t2 != t:
                        removed = True
                        t = t2
                else:
                    # marker may be in a different span; try to strip prefix
                    if ln.marker in BULLET_CHARS and t.strip() in BULLET_CHARS:
                        t = ""
                        removed = True
                    elif NUMBERED_RE.match(t.strip()):
                        t = NUMBERED_RE.sub("", t, count=1)
                        removed = True
            if t:
                first_runs.append((st, t))
        runs.extend(first_runs)
        for extra in item_lines[1:]:
            for s in extra.spans:
                st = span_style(s)
                t = clean_text(s["text"])
                if t:
                    runs.append((st, t))
        md = runs_to_md(runs).strip()
        # level from x
        text_x = ln.x0
        level = 0
        if text_x > 80:
            level = min(2, int((text_x - 72) // 18))
        return Element(kind="list", page=ln.page, y=ln.y0, x=ln.x0,
                       text=md, marker=marker, level=level), j

    def is_heading(self, ln, txt):
        if not txt:
            return False
        if ln.size >= 13.4:
            return True
        if ln.bold and HEADING_NUM_RE.match(txt):
            return True
        return False

    # ---------------- postprocess / write ----------------

    def postprocess(self):
        # merge across page boundaries
        merged = []
        # 记录每个表格最后一个片段的页码与底边，支持跨 3 页以上的续表
        table_tail = {}
        for el in self.elements:
            if merged:
                prev = merged[-1]
                if (el.kind == "code" and prev.kind == "code" and
                        prev.page == el.page - 1 and prev.bbox and el.bbox and
                        prev.bbox.y1 > 730 and el.bbox.y0 < 85):
                    prev.text = prev.text.rstrip("\n") + "\n" + el.text.lstrip("\n")
                    prev.bbox = pymupdf.Rect(prev.bbox.x0, prev.bbox.y0,
                                             max(prev.bbox.x1, el.bbox.x1),
                                             max(prev.bbox.y1, el.bbox.y1))
                    prev.lang = prev.lang or el.lang
                    continue
                if (el.kind == "table" and prev.kind == "table" and
                        prev.bbox and el.bbox and prev.ncols == el.ncols and
                        same_columns(prev, el)):
                    prev_page, prev_bottom = table_tail.get(
                        id(prev), (prev.page, prev.bbox.y1))
                    if (el.page == prev_page + 1 and prev_bottom > 400
                            and el.bbox.y0 < 85):
                        if el.header and prev.header and el.header == prev.header:
                            prev.rows.extend(el.rows)
                        else:
                            if el.header and any(c.strip() for c in el.header):
                                prev.rows.append(el.header)
                            prev.rows.extend(el.rows)
                        prev.bbox = pymupdf.Rect(prev.bbox.x0, prev.bbox.y0,
                                                 max(prev.bbox.x1, el.bbox.x1),
                                                 max(prev.bbox.y1, el.bbox.y1))
                        table_tail[id(prev)] = (el.page, el.bbox.y1)
                        continue
                if (el.kind == "paragraph" and prev.kind == "paragraph" and
                        prev.page == el.page - 1 and prev.y > 700 and el.y < 100):
                    last = prev.text.rstrip()
                    head = el.text.lstrip()
                    # 只拼接真正断句的中文段落；否则会把页尾 URL/字段名与下页标题粘在一起
                    if (last and head and not re.search(r"[。！？；：]$", last)
                            and re.search(r"[\u4e00-\u9fa5，、]$", last)
                            and re.search(r"^[\u4e00-\u9fa5（(“\"《]", head)):
                        prev.text = last + head
                        # 更新到已合并片段的页码/位置，避免继续吞并后面的段落
                        prev.page = el.page
                        prev.y = el.y
                        continue
            merged.append(el)
        self.elements = merged

        # 跨页续表会在页尾/页首留下全空的网格残行，统一丢弃
        for el in self.elements:
            if el.kind == "table":
                el.rows = [r for r in el.rows if any(c.strip() for c in r)]

        # reflow code blocks
        for el in self.elements:
            if el.kind == "code":
                lang = classify_code_lang(el.text)
                el.lang = lang
                if lang == "json":
                    reflowed = reflow_json(el.text)
                    # only accept reflow if it parses, or if reflow removed lines
                    try:
                        json.loads(reflowed)
                        el.text = reflowed
                    except Exception:
                        try:
                            json.loads(el.text)
                        except Exception:
                            el.text = reflowed

        # assign heading levels by size: store size on element? We lost it.
        # Recompute from text? Use document-specific mapping by order of sizes.
        # We stored level=0; derive by a second pass using original lines is
        # easier: infer from heading text prefixes and font size not available.
        # Instead, we encode a level hint in build_text_elements via self.heading_sizes.
        # (implemented below by a pre-scan in run())

    def write(self):
        lines = []
        for el in self.elements:
            if el.kind == "heading":
                lines.append("#" * max(1, min(6, el.level)) + " " + el.text.strip())
                lines.append("")
            elif el.kind == "paragraph":
                lines.append(el.text.strip())
                lines.append("")
            elif el.kind == "list":
                indent = "  " * el.level
                if el.marker == "-":
                    lines.append(f"{indent}- {el.text.strip()}")
                else:
                    lines.append(f"{indent}{el.marker} {el.text.strip()}")
            elif el.kind == "code":
                lines.append(f"```{el.lang}")
                lines.append(el.text.rstrip("\n"))
                lines.append("```")
                lines.append("")
            elif el.kind == "table":
                def esc(c):
                    return c.replace("|", "\\|").replace("\n", "<br>")
                header = el.header[:] if el.header else [""] * el.ncols
                if len(header) < el.ncols:
                    header += [""] * (el.ncols - len(header))
                lines.append("| " + " | ".join(esc(c) if c else " " for c in header) + " |")
                lines.append("| " + " | ".join("---" for _ in range(el.ncols)) + " |")
                for row in el.rows:
                    row = row[:] + [""] * (el.ncols - len(row))
                    lines.append("| " + " | ".join(esc(c) if c else " " for c in row) + " |")
                lines.append("")
            elif el.kind == "figure":
                rel = os.path.relpath(el.image, os.path.dirname(self.out_md)).replace("\\", "/")
                lines.append(f"![图]({rel})")
                lines.append("")
            elif el.kind == "mermaid":
                lines.append("```mermaid")
                lines.append(el.text.rstrip())
                lines.append("```")
                lines.append("")
                if self.keep_source_image:
                    rel = os.path.relpath(el.image, os.path.dirname(self.out_md)).replace("\\", "/")
                    label = DIAGRAM_LABELS.get(el.lang, el.lang)
                    lines.append("<details>")
                    lines.append(f"<summary>原图（p{el.page}，{label}）</summary>")
                    lines.append("")
                    lines.append(f"![图]({rel})")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
        # remove excessive blank lines
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        with open(self.out_md, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")


def safe_prefix(stem):
    """把文件名转成可安全用于图片名的前缀（保留 ASCII 部分）。"""
    p = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_").lower()
    return p or "doc"


def default_outputs(pdf_path):
    """默认产出：<同名>.md、<同名>_assets/、ASCII 前缀，均与源文件同目录。"""
    pdf_path = os.path.abspath(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.dirname(pdf_path)
    return (os.path.join(out_dir, base + ".md"),
            os.path.join(out_dir, base + "_assets"),
            safe_prefix(base))


def convert_document(pdf_path, out_md, assets_dir, asset_prefix,
                     keep_source_image=None):
    """转换入口（供 CLI / MCP 复用），返回产出统计。"""
    os.makedirs(assets_dir, exist_ok=True)
    conv = Converter(pdf_path, out_md, assets_dir, asset_prefix,
                     keep_source_image=keep_source_image)
    conv.run()
    kinds = {}
    for el in conv.elements:
        kinds[el.kind] = kinds.get(el.kind, 0) + 1
    pages = conv.doc.page_count
    conv.doc.close()
    # 没有图片产物时不留空目录
    try:
        if not os.listdir(assets_dir):
            os.rmdir(assets_dir)
    except OSError:
        pass
    return {
        "pages": pages,
        "tables": kinds.get("table", 0),
        "mermaid": kinds.get("mermaid", 0),
        "figures": kinds.get("figure", 0),
        "headings": kinds.get("heading", 0),
        "paragraphs": kinds.get("paragraph", 0),
        "code_blocks": kinds.get("code", 0),
        "md_bytes": os.path.getsize(out_md),
    }


def run_conversion(input_pdf, output_md="", assets_dir="", prefix="",
                   clean_watermark_first=True, keep_source_image=False):
    """去水印（按需）→ 转换，返回产出路径与统计。失败直接抛异常。

    供 CLI / MCP / 库调用复用，避免三处重复编排。
    """
    pdf = os.path.abspath(os.path.expanduser(input_pdf))
    if not os.path.isfile(pdf):
        raise FileNotFoundError(f"PDF 不存在: {pdf}")

    d_md, d_assets, d_prefix = default_outputs(pdf)
    out_md = os.path.abspath(output_md) if output_md else d_md
    assets = os.path.abspath(assets_dir) if assets_dir else d_assets
    prefix = prefix or d_prefix
    os.makedirs(os.path.dirname(out_md), exist_ok=True)

    started = time.time()
    removed = 0
    work_dir = None
    to_convert = pdf
    if clean_watermark_first:
        import clean_watermark
        if clean_watermark.has_watermark(pdf):
            work_dir = tempfile.mkdtemp(prefix="pdf2md_")
            to_convert = os.path.join(work_dir, "clean.pdf")
            removed = clean_watermark.remove_watermark(pdf, to_convert)
    try:
        stats = convert_document(to_convert, out_md, assets, prefix,
                                 keep_source_image=keep_source_image)
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)

    return {"md": out_md, "assets": assets, "watermark_removed": removed,
            "elapsed_ms": int((time.time() - started) * 1000), **stats}


if __name__ == "__main__":
    pdf, out_md, assets, prefix = sys.argv[1:5]
    print(json.dumps(convert_document(pdf, out_md, assets, prefix),
                     ensure_ascii=False))
