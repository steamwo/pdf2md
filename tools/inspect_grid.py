import sys, io, pymupdf

path, pno, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
if outp.lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")
doc = pymupdf.open(path)
page = doc[pno - 1]
out = io.open(outp, "w", encoding="utf-8")

# collect thin dark filled rects as grid lines
V, H = [], []
for d in page.get_drawings():
    fill = d.get("fill")
    if not fill:
        continue
    if not (abs(fill[0] - 0.09) < 0.02 and abs(fill[1] - 0.102) < 0.02 and abs(fill[2] - 0.114) < 0.02):
        continue
    r = d["rect"]
    if r.width <= 1.5 and r.height > 3:
        V.append((round((r.x0 + r.x1) / 2, 2), r.y0, r.y1))
    elif r.height <= 1.5 and r.width > 3:
        H.append((round((r.y0 + r.y1) / 2, 2), r.x0, r.x1))

def merge_v(segs):
    segs = sorted(segs)
    out = []
    for x, y0, y1 in segs:
        if out and abs(out[-1][0] - x) < 0.6 and y0 <= out[-1][2] + 1.5:
            out[-1] = (out[-1][0], out[-1][1], max(out[-1][2], y1))
        else:
            out.append((x, y0, y1))
    return out

def merge_h(segs):
    segs = sorted(segs)
    out = []
    for y, x0, x1 in segs:
        if out and abs(out[-1][0] - y) < 0.6 and x0 <= out[-1][2] + 1.5:
            out[-1] = (out[-1][0], out[-1][1], max(out[-1][2], x1))
        else:
            out.append((y, x0, x1))
    return out

V, H = merge_v(V), merge_h(H)
print("V lines:", V, file=out)
print("H lines:", H, file=out)

# simple: assume one table per page for now; print cells
if V and H:
    xs = sorted(set(x for x, _, _ in V))
    ys = sorted(set(y for y, _, _ in H))
    print("xs", xs, file=out)
    print("ys", ys, file=out)
    for i in range(len(ys) - 1):
        mid = (ys[i] + ys[i + 1]) / 2
        row_xs = [x for x, y0, y1 in V if y0 - 1 <= mid <= y1 + 1]
        row_xs = sorted(set(row_xs + [min(xs), max(xs)]))
        cells = []
        for j in range(len(row_xs) - 1):
            rect = pymupdf.Rect(row_xs[j], ys[i], row_xs[j + 1], ys[i + 1])
            lines = []
            for b in page.get_text("dict", clip=rect)["blocks"]:
                if b["type"] != 0: continue
                for l in b["lines"]:
                    cx, cy = (l["bbox"][0] + l["bbox"][2]) / 2, (l["bbox"][1] + l["bbox"][3]) / 2
                    if rect.x0 + 1 <= cx <= rect.x1 - 1 and rect.y0 + 1 <= cy <= rect.y1 - 1:
                        lines.append((round(l["bbox"][1], 1), round(l["bbox"][0], 1), "".join(s["text"] for s in l["spans"])))
            lines.sort()
            cells.append(" / ".join(t for _, _, t in lines))
        print(f"row {i} y={ys[i]}-{ys[i+1]}:", cells, file=out)
out.close()
