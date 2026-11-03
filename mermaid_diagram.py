# -*- coding: utf-8 -*-
"""Pure-Python vector diagram -> Mermaid conversion.

Supports the two diagram families used by the source PDFs:

* sequence diagrams (participants, lifelines, solid/dashed arrows, self
  messages, yellow notes, alt/else, opt, loop and par/and frames);
* flowcharts (rectangular nodes, orthogonal connectors, bidirectional arrows).

Everything else returns ``None`` so the caller can fall back to a PNG crop.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import pymupdf

Point = pymupdf.Point
Rect = pymupdf.Rect

# 源 PDF 中用到的绘制颜色（RGB，0~1）
LIFELINE_COLOR = (0.854, 0.807, 0.951)
HEADER_FILL = (0.926, 0.926, 1.0)
NOTE_FILL = (1.0, 0.96, 0.68)
DARK_COLORS = ((0.0, 0.0, 0.0), (0.2, 0.2, 0.2))


# ------------------------------------------------------------------ helpers

def _rgb(value) -> Optional[Tuple[float, float, float]]:
    """把 PyMuPDF 的颜色值归一化为三元组。"""
    if not value:
        return None
    vals = tuple(float(x) for x in value[:3])
    return vals if len(vals) == 3 else None


def _close(color: Optional[Tuple[float, float, float]],
           target: Tuple[float, float, float], tol: float = 0.03) -> bool:
    return bool(color) and all(abs(a - b) <= tol for a, b in zip(color, target))


def _is_dark(color: Optional[Tuple[float, float, float]]) -> bool:
    return bool(color) and max(color) <= 0.26


def _is_light(color: Optional[Tuple[float, float, float]]) -> bool:
    return bool(color) and min(color) >= 0.85


def _intersects(a: Rect, b: Rect) -> bool:
    return not (a.x1 < b.x0 or b.x1 < a.x0 or a.y1 < b.y0 or b.y1 < a.y0)


def _contains_rect(outer: Rect, inner: Rect, tol: float = 1.0) -> bool:
    return (outer.x0 - tol <= inner.x0 and outer.y0 - tol <= inner.y0 and
            outer.x1 + tol >= inner.x1 and outer.y1 + tol >= inner.y1)


def _contains_point(rect: Rect, x: float, y: float, tol: float = 0.0) -> bool:
    return (rect.x0 - tol <= x <= rect.x1 + tol and
            rect.y0 - tol <= y <= rect.y1 + tol)


def _normalize_cjk(text: str) -> str:
    """把 PDF 字体里的康熙部首/兼容汉字还原成常用汉字。"""
    out = []
    for ch in text:
        code = ord(ch)
        if 0x2E80 <= code <= 0x2FDF or 0xF900 <= code <= 0xFAFF:
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def _clean_label(text: str) -> str:
    """Mermaid 标签清洗：去换行、避免语法分隔符。"""
    text = _normalize_cjk(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("\n", " ")
    text = text.replace(":", "：").replace(";", "；")
    text = text.replace("|", "/")
    return text


def _point_segment_dist(px: float, py: float, seg) -> float:
    x1, y1, x2, y2 = seg[0].x, seg[0].y, seg[1].x, seg[1].y
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = x1 + t * dx, y1 + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _path_distance(px: float, py: float, lines: Sequence) -> float:
    return min((_point_segment_dist(px, py, s) for s in lines), default=1e9)


def _merge_spans(spans: Iterable[Tuple[float, float, float]],
                 axis: str = "h", tol: float = 1.0) -> List[Tuple[float, float, float]]:
    """合并共线且相邻的线段（轴对齐）。"""
    spans = sorted(spans)
    out: List[Tuple[float, float, float]] = []
    for key, a, b in spans:
        if out and abs(out[-1][0] - key) <= tol and a <= out[-1][2] + tol:
            out[-1] = (out[-1][0], out[-1][1], max(out[-1][2], b))
        else:
            out.append((key, a, b))
    return out


@dataclass
class Shape:
    """一条矢量绘制（已提取直线段）。"""
    kind: str
    rect: Rect
    fill: Optional[Tuple[float, float, float]]
    color: Optional[Tuple[float, float, float]]
    fill_opacity: float
    stroke_opacity: float
    dashes: str
    lines: List[Tuple[Point, Point]] = field(default_factory=list)
    n_items: int = 0
    item_kinds: Tuple[str, ...] = ()

    @property
    def cx(self) -> float:
        return (self.rect.x0 + self.rect.x1) / 2

    @property
    def cy(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


@dataclass
class TextItem:
    """图内文本行。"""
    bbox: Rect
    text: str
    size: float

    @property
    def cx(self) -> float:
        return (self.bbox.x0 + self.bbox.x1) / 2

    @property
    def cy(self) -> float:
        return (self.bbox.y0 + self.bbox.y1) / 2


@dataclass
class Participant:
    """时序图参与者。"""
    index: int
    name: str
    x: float
    header: Optional[Rect] = None


@dataclass
class Message:
    """时序图消息（含自循环）。"""
    y: float
    sender: int
    receiver: int
    dashed: bool
    label: str = ""
    x0: float = 0.0
    x1: float = 0.0
    self_loop: bool = False
    curve: Optional[Rect] = None


@dataclass
class Note:
    """时序图黄色备注。"""
    y: float
    start: int
    end: int
    text: str
    rect: Rect


@dataclass
class Frame:
    """时序图 alt/opt/loop/par 框。"""
    kind: str
    top: float
    bottom: float
    left: float
    right: float
    condition: str
    dividers: List[Tuple[float, str]] = field(default_factory=list)

    @property
    def rect(self) -> Rect:
        return Rect(self.left, self.top, self.right, self.bottom)


@dataclass
class DiagramResult:
    """转换结果；confidence 低于阈值时调用方应回退为图片。"""
    kind: str
    mermaid: str
    confidence: float
    stats: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class FlowNode:
    """流程图节点。"""
    index: int
    rect: Rect
    label: str


@dataclass
class FlowEdge:
    """流程图连线。"""
    src: int
    dst: int
    label: str
    both: bool = False
    arrow_src: bool = False
    arrow_dst: bool = False
    lines: List[Tuple[Point, Point]] = field(default_factory=list)


# ------------------------------------------------------------------ extraction

def _parse_drawings(drawings: Sequence[dict], fig: Rect) -> List[Shape]:
    shapes: List[Shape] = []
    for d in drawings:
        r = Rect(d["rect"])
        if not _intersects(r, fig):
            continue
        fill = _rgb(d.get("fill"))
        color = _rgb(d.get("color"))
        fo = d.get("fill_opacity")
        so = d.get("stroke_opacity")
        items = d.get("items", [])
        lines = [(it[1], it[2]) for it in items if it[0] == "l"]
        shapes.append(Shape(
            kind=d.get("type", ""),
            rect=r,
            fill=fill,
            color=color,
            fill_opacity=1.0 if fo is None else float(fo),
            stroke_opacity=1.0 if so is None else float(so),
            dashes=d.get("dashes") or "",
            lines=lines,
            n_items=len(items),
            item_kinds=tuple(it[0] for it in items),
        ))
    return shapes


def _text_items(lines: Sequence[Any], fig: Rect) -> List[TextItem]:
    out: List[TextItem] = []
    for ln in lines:
        if not _contains_point(fig, ln.cx, ln.cy, 3.0):
            continue
        text = _normalize_cjk(ln.text).strip()
        if not text:
            continue
        out.append(TextItem(Rect(ln.bbox), text, float(ln.size)))
    return out


# ------------------------------------------------------------------ sequence

def _find_lifelines(shapes: Sequence[Shape], fig: Rect) -> List[float]:
    xs: List[Tuple[float, float, float]] = []
    for s in shapes:
        if s.kind != "s" or s.stroke_opacity < 0.5 or len(s.lines) != 1:
            continue
        r = s.rect
        if r.width > 2.0 or r.height < fig.height * 0.25:
            continue
        if not (_close(s.color, LIFELINE_COLOR, 0.05) or _is_dark(s.color)):
            continue
        x = (r.x0 + r.x1) / 2
        if x < fig.x0 + 4 or x > fig.x1 - 4:
            continue
        xs.append((round(x, 2), r.y0, r.y1))
    merged = _merge_spans(xs, tol=1.0)
    # 更长的线优先；把被错误拆分的同 x 线段合并后按 x 排序
    merged.sort(key=lambda t: t[0])
    return [t[0] for t in merged]


def _header_boxes(shapes: Sequence[Shape], fig: Rect) -> List[Rect]:
    boxes: List[Rect] = []
    for s in shapes:
        if s.kind not in ("fs", "f") or s.fill_opacity < 0.5:
            continue
        if not (_close(s.fill, HEADER_FILL, 0.04) and
                (_close(s.color, LIFELINE_COLOR, 0.05) or s.color is None)):
            continue
        r = s.rect
        if not (12 <= r.width <= 110 and 6 <= r.height <= 34):
            continue
        cy = (r.y0 + r.y1) / 2
        if not (cy < fig.y0 + fig.height * 0.3 or cy > fig.y1 - fig.height * 0.3):
            continue
        boxes.append(Rect(r))
    # 去掉重复框（上下各一组只取一组）
    uniq: List[Rect] = []
    for r in sorted(boxes, key=lambda r: (r.y0, r.x0)):
        if not any(abs(r.x0 - o.x0) < 1 and abs(r.y0 - o.y0) < 1 and
                   abs(r.width - o.width) < 1 for o in uniq):
            uniq.append(r)
    if not uniq:
        return []
    top_group = [r for r in uniq if (r.y0 + r.y1) / 2 < fig.y0 + fig.height / 2]
    return top_group or uniq


def _find_participants(shapes: Sequence[Shape], texts: Sequence[TextItem],
                       fig: Rect, lifelines: Sequence[float]) -> List[Participant]:
    if len(lifelines) < 2:
        return []
    boxes = _header_boxes(shapes, fig)
    assigned: dict[int, Participant] = {}
    used_texts: set[int] = set()
    for bi, box in enumerate(boxes):
        inside = [(ti, t) for ti, t in enumerate(texts)
                  if _contains_point(box, t.cx, t.cy, 1.5) and t.size < 12 and ti not in used_texts]
        if not inside:
            continue
        name = " ".join(t.text for _, t in sorted(inside, key=lambda x: (x[1].bbox.y0, x[1].bbox.x0)))
        name = _clean_label(name)
        if not name:
            continue
        x = (box.x0 + box.x1) / 2
        li = min(range(len(lifelines)), key=lambda i: abs(lifelines[i] - x))
        if abs(lifelines[li] - x) > 14:
            continue
        if li in assigned:
            continue
        assigned[li] = Participant(li, name, lifelines[li], box)
        used_texts.update(ti for ti, _ in inside)
    if len(assigned) < 2:
        # 兜底：从第一条消息上方的文本里找参与者名
        assigned = {}
        for li, x in enumerate(lifelines):
            cands = [t for t in texts if abs(t.cx - x) <= 30 and t.size < 12]
            if cands:
                t = min(cands, key=lambda t: t.bbox.y0)
                assigned[li] = Participant(li, _clean_label(t.text), x)
    parts = [assigned[i] for i in sorted(assigned)]
    for p in parts:
        if not p.name:
            p.name = f"对象{p.index + 1}"
    return parts


def _arrowheads(shapes: Sequence[Shape]) -> List[Tuple[float, float]]:
    heads: List[Tuple[float, float]] = []
    for s in shapes:
        if not _is_dark(s.fill):
            continue
        r = s.rect
        if r.width > 6.5 or r.height > 6.5 or r.width * r.height > 42:
            continue
        if not (3 <= s.n_items <= 5):
            continue
        heads.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return heads


def _nearest_participant(x: float, lifelines: Sequence[float], tol: float = 14) -> Optional[int]:
    idx = min(range(len(lifelines)), key=lambda i: abs(lifelines[i] - x))
    return idx if abs(lifelines[idx] - x) <= tol else None


def _find_notes(shapes: Sequence[Shape], texts: Sequence[TextItem],
                lifelines: Sequence[float], fig: Rect):
    notes: List[Note] = []
    used: set[int] = set()
    for s in shapes:
        if s.kind not in ("f", "fs") or s.fill_opacity < 0.5:
            continue
        if not _close(s.fill, NOTE_FILL, 0.03):
            continue
        r = s.rect
        if r.width < 10 or r.height < 5 or not _intersects(r, fig):
            continue
        inside = [(ti, t) for ti, t in enumerate(texts)
                  if _contains_point(r, t.cx, t.cy, 1.5) and ti not in used]
        if not inside:
            continue
        text = " ".join(t.text for _, t in sorted(inside, key=lambda x: (x[1].bbox.y0, x[1].bbox.x0)))
        text = _clean_label(text)
        covered = [i for i, x in enumerate(lifelines) if r.x0 - 2 <= x <= r.x1 + 2]
        if not covered:
            covered = [min(range(len(lifelines)), key=lambda i: abs(lifelines[i] - (r.x0 + r.x1) / 2))]
        notes.append(Note(r.y0, covered[0], covered[-1], text, Rect(r)))
        used.update(ti for ti, _ in inside)
    return notes, used


def _purple_segments(shapes: Sequence[Shape], fig: Rect):
    h: List[Tuple[float, float, float]] = []
    v: List[Tuple[float, float, float]] = []
    for s in shapes:
        if s.kind not in ("f", "fs") or s.fill_opacity < 0.5:
            continue
        if not _close(s.fill, LIFELINE_COLOR, 0.04):
            continue
        r = s.rect
        if not _intersects(r, fig):
            continue
        if r.height <= 0.9 and r.width >= 8:
            h.append((round((r.y0 + r.y1) / 2, 2), r.x0, r.x1))
        elif r.width <= 0.9 and r.height >= 8:
            v.append((round((r.x0 + r.x1) / 2, 2), r.y0, r.y1))
    return _merge_spans(h, tol=0.8), _merge_spans(v, tol=0.8)


def _find_frames(shapes: Sequence[Shape], texts: Sequence[TextItem],
                 fig: Rect, lifelines: Sequence[float]):
    h, v = _purple_segments(shapes, fig)
    if len(h) < 4 or len(v) < 2:
        return [], set(), True
    candidates = []
    for hi, (ty, tx0, tx1) in enumerate(h):
        best = None
        for bi, (by, bx0, bx1) in enumerate(h):
            if by <= ty + 5:
                continue
            if abs(bx0 - tx0) > 2.5 or abs(bx1 - tx1) > 2.5:
                continue
            left_ok = any(abs(x - tx0) <= 2.5 and y0 <= ty + 2 and y1 >= by - 2 for x, y0, y1 in v)
            right_ok = any(abs(x - tx1) <= 2.5 and y0 <= ty + 2 and y1 >= by - 2 for x, y0, y1 in v)
            if left_ok and right_ok:
                best = by
        if best is None:
            continue
        candidates.append((ty, best, tx0, tx1))
    if not candidates:
        return [], set(), True
    # 面积大的优先；同范围的分隔线矩形会被大框吸收
    candidates.sort(key=lambda c: ((c[1] - c[0]) * (c[3] - c[2])), reverse=True)
    accepted: List[Tuple[float, float, float, float]] = []
    for ty, by, x0, x1 in candidates:
        dup = False
        for ay0, ay1, ax0, ax1 in accepted:
            same_x = abs(ax0 - x0) <= 2.5 and abs(ax1 - x1) <= 2.5
            if same_x and ay0 - 2 <= ty and by <= ay1 + 2:
                dup = True
                break
        if not dup:
            accepted.append((ty, by, x0, x1))

    labels = {"alt", "opt", "loop", "par", "else", "and", "break", "critical"}
    frames: List[Frame] = []
    used_text: set[int] = set()
    for ty, by, x0, x1 in accepted:
        cand = [(ti, t) for ti, t in enumerate(texts)
                if t.text.lower() in labels and x0 - 2 <= t.cx <= x1 + 2 and
                ty - 2 <= t.cy <= ty + 12 and ti not in used_text]
        if not cand:
            continue
        ti, lab = min(cand, key=lambda x: (x[1].bbox.y0, x[1].bbox.x0))
        kind = lab.text.lower()
        if kind in ("else", "and"):
            continue
        cond_items = [(tj, t) for tj, t in enumerate(texts)
                      if tj != ti and x0 - 2 <= t.cx <= x1 + 2 and
                      abs(t.cy - lab.cy) <= 4.5]
        condition = ""
        if cond_items:
            cti, ct = min(cond_items, key=lambda x: abs(x[1].cx - lab.cx))
            condition = ct.text
            used_text.add(cti)
        used_text.add(ti)
        dividers: List[Tuple[float, str]] = []
        for dy, dx0, dx1 in h:
            if not (ty + 2 < dy < by - 2):
                continue
            if abs(dx0 - x0) > 2.5 or abs(dx1 - x1) > 2.5:
                continue
            dcond = ""
            dcands = [(tj, t) for tj, t in enumerate(texts)
                      if x0 - 2 <= t.cx <= x1 + 2 and abs(t.cy - dy) <= 6 and
                      t.text.lower() not in labels]
            if dcands:
                dti, dt = min(dcands, key=lambda x: abs(x[1].cy - dy))
                dcond = dt.text
                used_text.add(dti)
            dividers.append((dy, _clean_label(dcond)))
        frames.append(Frame(kind, ty, by, x0, x1, _clean_label(condition), dividers))

    frames.sort(key=lambda f: (f.top, -(f.bottom - f.top)))
    # 只保留嵌套或互不相交的框；交叉框全部放弃
    ok = True
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            a, b = frames[i], frames[j]
            inter = _intersects(a.rect, b.rect)
            if not inter:
                continue
            if not (_contains_rect(a.rect, b.rect) or _contains_rect(b.rect, a.rect)):
                ok = False
                break
        if not ok:
            break
    return (frames if ok else []), used_text, ok


def _find_messages(shapes: Sequence[Shape], lifelines: Sequence[float], fig: Rect):
    heads = _arrowheads(shapes)
    messages: List[Message] = []
    for s in shapes:
        if s.stroke_opacity < 0.5:
            continue
        r = s.rect
        horizontal = r.height <= 1.6 and r.width >= 8
        if not horizontal or not _intersects(r, fig):
            continue
        dashed = False
        if s.kind == "s":
            if len(s.lines) != 1 or not _is_dark(s.color):
                continue
            if any(k != "l" for k in s.item_kinds):
                continue
            if s.dashes and s.dashes not in ("[] 0", "[]"):
                dashed = True
        elif s.kind in ("f", "fs"):
            if not _is_dark(s.fill) or s.fill_opacity < 0.5:
                continue
            if s.n_items < 3 or any(k != "l" for k in s.item_kinds):
                continue
            dashed = True
        else:
            continue
        y = (r.y0 + r.y1) / 2
        x0, x1 = r.x0, r.x1
        left = any(abs(hx - x0) <= 5 and abs(hy - y) <= 3.5 for hx, hy in heads)
        right = any(abs(hx - x1) <= 5 and abs(hy - y) <= 3.5 for hx, hy in heads)
        if not left and not right:
            continue
        pi0 = _nearest_participant(x0, lifelines, 16)
        pi1 = _nearest_participant(x1, lifelines, 16)
        if pi0 is None or pi1 is None:
            continue
        if left and right:
            left = False
        if left:
            sender, receiver = pi1, pi0
        else:
            sender, receiver = pi0, pi1
        messages.append(Message(y, sender, receiver, dashed, "", x0, x1))
    # 自循环
    for s in shapes:
        if s.kind != "s" or s.stroke_opacity < 0.5 or not _is_dark(s.color):
            continue
        if "c" not in s.item_kinds:
            continue
        r = s.rect
        if not (6 <= r.width <= 90 and 5 <= r.height <= 40):
            continue
        if not any(abs(hx - (r.x0 + r.x1) / 2) <= r.width + 10 and
                   abs(hy - (r.y0 + r.y1) / 2) <= r.height + 10 for hx, hy in heads):
            continue
        x = min((r.x0, r.x1), key=lambda px: min(abs(px - lx) for lx in lifelines))
        pi = _nearest_participant(x, lifelines, 18)
        if pi is None:
            continue
        messages.append(Message(r.y0, pi, pi, False, "", r.x0, r.x1, True, Rect(r)))
    messages.sort(key=lambda m: (m.y, m.x0))
    return messages


def _assign_message_labels(messages: Sequence[Message], texts: Sequence[TextItem],
                           excluded: set[int]) -> None:
    remaining = set(range(len(texts))) - excluded
    for mi, msg in enumerate(messages):
        if msg.self_loop and msg.curve is not None:
            r = msg.curve
            cands = []
            for ti in remaining:
                t = texts[ti]
                if r.y0 - 18 <= t.cy <= r.y1 + 4 and \
                        t.cx >= r.x0 - 80 and t.cx <= r.x1 + 140:
                    cands.append((abs(t.cy - r.y0), ti))
        else:
            cands = []
            for ti in remaining:
                t = texts[ti]
                d = msg.y - t.bbox.y1
                if -1.0 <= d <= 16.0 and t.cx >= msg.x0 - 30 and t.cx <= msg.x1 + 30:
                    cands.append((d, ti))
        if not cands:
            continue
        cands.sort()
        chosen = [cands[0]]
        # 同一消息上方可能有多行标签
        base_d = cands[0][0]
        for d, ti in cands[1:]:
            if d - base_d <= 13:
                chosen.append((d, ti))
        chosen.sort(key=lambda x: (texts[x[1]].bbox.y0, texts[x[1]].bbox.x0))
        label = "<br/>".join(texts[ti].text for _, ti in chosen)
        msg.label = _clean_label(label)
        remaining.difference_update(ti for _, ti in chosen)


def _sequence_mermaid(participants: Sequence[Participant], messages: Sequence[Message],
                      notes: Sequence[Note], frames: Sequence[Frame],
                      use_frames: bool) -> str:
    out = ["sequenceDiagram"]
    for p in participants:
        out.append(f"    participant P{p.index + 1} as {p.name}")
    events = []
    for m in messages:
        events.append((m.y, 1, "message", m))
    for n in notes:
        events.append((n.y, 1, "note", n))
    if use_frames:
        for f in frames:
            events.append((f.top, 0, "frame_top", f))
            for dy, cond in f.dividers:
                events.append((dy, 0, "divider", (f, cond)))
            events.append((f.bottom, 2, "frame_bottom", f))
    events.sort(key=lambda e: (e[0], e[1]))

    stack: List[Frame] = []
    for _, _, typ, obj in events:
        indent = "    " * (len(stack) + 1)
        if typ == "frame_top":
            if stack and not _contains_rect(stack[-1].rect, obj.rect):
                continue
            stack.append(obj)
            cond = obj.condition or "（条件）"
            # Mermaid 自己会给 loop 的说明加方括号，去掉原文括号避免出现双层
            if obj.kind == "loop" and cond.startswith("[") and cond.endswith("]"):
                cond = cond[1:-1].strip()
            out.append(f"{'    ' * len(stack)}{obj.kind} {cond}")
        elif typ == "divider":
            frame, cond = obj
            if not stack or stack[-1] is not frame:
                continue
            word = "else" if frame.kind == "alt" else "and"
            out.append(f"{'    ' * len(stack)}{word} {cond or '（条件）'}")
        elif typ == "frame_bottom":
            if stack and stack[-1] is obj:
                depth = len(stack)
                stack.pop()
                out.append(f"{'    ' * depth}end")
        elif typ == "message":
            m: Message = obj
            src = f"P{m.sender + 1}"
            dst = f"P{m.receiver + 1}"
            arrow = "-->>" if m.dashed else "->>"
            label = m.label or "…"
            out.append(f"{indent}{src}{arrow}{dst}: {label}")
        else:
            n: Note = obj
            if n.start == n.end:
                out.append(f"{indent}Note over P{n.start + 1}: {n.text}")
            else:
                out.append(f"{indent}Note over P{n.start + 1},P{n.end + 1}: {n.text}")
    # 未闭合的框（正常不会出现）
    while stack:
        stack.pop()
        out.append(f"{'    ' * (len(stack) + 1)}end")
    return "\n".join(out)


def convert_sequence(shapes: Sequence[Shape], texts: Sequence[TextItem],
                     fig: Rect) -> Optional[DiagramResult]:
    lifelines = _find_lifelines(shapes, fig)
    if len(lifelines) < 2:
        return None
    participants = _find_participants(shapes, texts, fig, lifelines)
    if len(participants) < 2:
        return None
    # 参与者名称本身不能再次当作消息标签
    participant_text: set[int] = set()
    for p in participants:
        if p.header is None:
            continue
        for ti, t in enumerate(texts):
            if _contains_point(p.header, t.cx, t.cy, 1.5):
                participant_text.add(ti)
    # 参与者必须覆盖大部分生命线
    lifelines = [p.x for p in participants]
    notes, note_text = _find_notes(shapes, texts, lifelines, fig)
    frames, frame_text, frames_ok = _find_frames(shapes, texts, fig, lifelines)
    messages = _find_messages(shapes, lifelines, fig)
    if len(messages) < 2:
        return None
    _assign_message_labels(messages, texts, note_text | frame_text | participant_text)
    labeled = sum(1 for m in messages if m.label)
    coverage = labeled / max(1, len(messages))
    if coverage < 0.35:
        return None
    conf = 1.0
    if not frames_ok:
        conf *= 0.8
    if len(participants) < len(lifelines):
        conf *= 0.8
    if coverage < 0.7:
        conf *= 0.85
    mermaid = _sequence_mermaid(participants, messages, notes, frames, frames_ok and bool(frames))
    stats = {
        "participants": len(participants),
        "messages": len(messages),
        "labeled": labeled,
        "notes": len(notes),
        "frames": len(frames) if frames_ok else 0,
    }
    warnings = []
    if not frames_ok:
        warnings.append("帧边界交叉，已省略 alt/opt/par 框")
    return DiagramResult("sequence", mermaid, conf, stats, warnings)


# ------------------------------------------------------------------ flowchart

def _find_nodes(shapes: Sequence[Shape], texts: Sequence[TextItem], fig: Rect):
    nodes: List[FlowNode] = []
    for s in shapes:
        if s.kind != "fs" or s.stroke_opacity < 0.5:
            continue
        if not _is_dark(s.color) or not _is_light(s.fill):
            continue
        r = s.rect
        if r.width < 14 or r.height < 10 or not _contains_point(fig, s.cx, s.cy, 1):
            continue
        inside = [t for t in texts if _contains_point(r, t.cx, t.cy, 1.0) and t.size < 10.5]
        if not inside:
            continue
        inside.sort(key=lambda t: (t.bbox.y0, t.bbox.x0))
        label = _clean_label("<br/>".join(t.text for t in inside))
        nodes.append(FlowNode(len(nodes), Rect(r), label))
    nodes.sort(key=lambda n: (n.rect.y0, n.rect.x0))
    for i, n in enumerate(nodes):
        n.index = i
    return nodes


def _path_endpoints(lines: Sequence[Tuple[Point, Point]]) -> Optional[Tuple[Point, Point]]:
    counts: dict[Tuple[float, float], int] = {}
    pts: dict[Tuple[float, float], Point] = {}
    for p0, p1 in lines:
        for p in (p0, p1):
            key = (round(p.x, 1), round(p.y, 1))
            counts[key] = counts.get(key, 0) + 1
            pts[key] = p
    ends = [pts[k] for k, v in counts.items() if v == 1]
    if len(ends) == 2:
        return ends[0], ends[1]
    if len(ends) > 2:
        # 取相距最远的一对
        best = None
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                d = (ends[i].x - ends[j].x) ** 2 + (ends[i].y - ends[j].y) ** 2
                if best is None or d > best[0]:
                    best = (d, ends[i], ends[j])
        if best:
            return best[1], best[2]
    return None


def _hit_node(pt: Point, nodes: Sequence[FlowNode], tol: float = 5.0) -> Optional[FlowNode]:
    hits = [n for n in nodes if _contains_point(n.rect, pt.x, pt.y, tol)]
    if not hits:
        return None
    return min(hits, key=lambda n: n.rect.get_area())


def _find_flow_edges(shapes: Sequence[Shape], nodes: Sequence[FlowNode], fig: Rect):
    heads = _arrowheads(shapes)
    raw: List[Tuple[FlowEdge, Tuple[Point, Point]]] = []
    for s in shapes:
        if s.kind != "s" or s.stroke_opacity < 0.5 or not _is_dark(s.color):
            continue
        # 只接受直线段组成的折线，排除箭头轮廓和曲线
        if not s.lines or any(k != "l" for k in s.item_kinds):
            continue
        r = s.rect
        if r.width < 6 and r.height < 6:
            continue
        if not _intersects(r, fig):
            continue
        ends = _path_endpoints(s.lines)
        if ends is None:
            continue
        n0 = _hit_node(ends[0], nodes)
        n1 = _hit_node(ends[1], nodes)
        if n0 is None or n1 is None:
            continue
        raw.append((FlowEdge(n0.index, n1.index, "", False, False, False, list(s.lines)), ends))
    # 箭头统一做“最近端点”匹配，避免拥挤节点边界上把邻线箭头算进来
    endpoints = []
    usage = []
    for ei, (_, ends) in enumerate(raw):
        endpoints.append((ends[0], ei, 0))
        endpoints.append((ends[1], ei, 1))
        usage.extend([0, 0])
    for hx, hy in heads:
        cands = []
        for k, (pt, ei, side) in enumerate(endpoints):
            d = ((hx - pt.x) ** 2 + (hy - pt.y) ** 2) ** 0.5
            if d <= 3.0:
                # 距离先分桶，近等距时优先使用尚未被占用的端点
                cands.append((round(d * 2), usage[k], d, ei, side, k))
        if not cands:
            continue
        cands.sort()
        _, _, _, ei, side, k = cands[0]
        usage[k] += 1
        edge = raw[ei][0]
        if side == 0:
            edge.arrow_src = True
        else:
            edge.arrow_dst = True
    edges = [e for e, _ in raw]
    for e in edges:
        e.both = e.arrow_src and e.arrow_dst
    return edges


def _assign_flow_labels(edges: Sequence[FlowEdge], texts: Sequence[TextItem],
                        nodes: Sequence[FlowNode]) -> None:
    cand = [t for t in texts if t.size < 10.5 and
            not any(_contains_point(n.rect, t.cx, t.cy, 1.0) for n in nodes)]
    buckets: dict[int, List[TextItem]] = {}
    for t in cand:
        best = None
        for i, e in enumerate(edges):
            d = _path_distance(t.cx, t.cy, e.lines)
            if best is None or d < best[0]:
                best = (d, i)
        if best and best[0] <= 8.5:
            buckets.setdefault(best[1], []).append(t)
    for i, items in buckets.items():
        items.sort(key=lambda t: (t.bbox.y0, t.bbox.x0))
        edges[i].label = _clean_label("<br/>".join(t.text for t in items))


def _flowchart_mermaid(nodes: Sequence[FlowNode], edges: Sequence[FlowEdge]) -> str:
    out = ["flowchart TD"]
    for n in nodes:
        label = n.label.replace('"', "#quot;")
        out.append(f'    N{n.index + 1}["{label}"]')
    for e in edges:
        src, dst = e.src, e.dst
        arrow = "---"
        if e.both:
            arrow = "<-->"
        elif e.arrow_dst:
            arrow = "-->"
        elif e.arrow_src:
            src, dst = dst, src
            arrow = "-->"
        label = e.label
        if label:
            out.append(f"    N{src + 1} {arrow}|{label}| N{dst + 1}")
        else:
            out.append(f"    N{src + 1} {arrow} N{dst + 1}")
    return "\n".join(out)


def convert_flowchart(shapes: Sequence[Shape], texts: Sequence[TextItem],
                      fig: Rect) -> Optional[DiagramResult]:
    nodes = _find_nodes(shapes, texts, fig)
    if len(nodes) < 2 or len(nodes) > 12:
        return None
    # 嵌套节点（架构图的分层框）不适合用 flowchart 表达
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if i != j and _contains_rect(a.rect, b.rect, 0):
                return None
    edges = _find_flow_edges(shapes, nodes, fig)
    if len(edges) < 1:
        return None
    _assign_flow_labels(edges, texts, nodes)
    # 去重：同一对节点、同一标签、同方向
    uniq: List[FlowEdge] = []
    seen = set()
    for e in edges:
        key = (min(e.src, e.dst), max(e.src, e.dst), e.label, e.both)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    mapped = sum(1 for e in uniq if e.label or e.both or e.arrow_dst or e.arrow_src)
    conf = 1.0 if uniq else 0.0
    if mapped < len(uniq) * 0.6:
        conf *= 0.8
    stats = {"nodes": len(nodes), "edges": len(uniq), "labeled": sum(1 for e in uniq if e.label)}
    return DiagramResult("flowchart", _flowchart_mermaid(nodes, uniq), conf, stats)


# ------------------------------------------------------------------ entry point

def convert_figure(page: pymupdf.Page, fig: Rect, lines: Sequence[Any],
                   drawings: Optional[Sequence[dict]] = None) -> Optional[DiagramResult]:
    """把一张矢量图转换为 Mermaid；无法可靠识别时返回 None。"""
    if drawings is None:
        drawings = page.get_drawings()
    shapes = _parse_drawings(drawings, fig)
    texts = _text_items(lines, fig)
    if len(texts) < 2 or len(shapes) < 4:
        return None
    seq = convert_sequence(shapes, texts, fig)
    if seq is not None and seq.confidence >= 0.6:
        return seq
    flow = convert_flowchart(shapes, texts, fig)
    if flow is not None and flow.confidence >= 0.5:
        return flow
    return None
