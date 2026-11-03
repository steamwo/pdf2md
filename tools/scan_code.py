import sys, io, pymupdf

path, outp = sys.argv[1], sys.argv[2]
if outp.lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")
doc = pymupdf.open(path)
out = io.open(outp, "w", encoding="utf-8")
blocks = []
for pno in range(doc.page_count):
    page = doc[pno]
    for d in page.get_drawings():
        r = d["rect"]
        fill = d.get("fill")
        if not fill:
            continue
        if tuple(round(x, 3) for x in fill) != (0.345, 0.408, 0.565):
            continue
        if r.width < 300 or r.height < 20:
            continue
        blocks.append((pno + 1, r))

print("code containers:", len(blocks), file=out)
for i, (pno, r) in enumerate(blocks):
    page = doc[pno - 1]
    lines = []
    for b in page.get_text("dict", clip=r)["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            txt = "".join(s["text"] for s in l["spans"])
            if txt.strip() == "复制代码":
                continue
            fonts = {s["font"] for s in l["spans"]}
            if not all(("Consolas" in f or "NSimSun" in f or "Mono" in f) for f in fonts):
                continue
            lines.append((round(l["bbox"][1], 1), txt))
    lines.sort()
    texts = [t for _, t in lines]
    print(f"--- block {i} page {pno} bbox={[round(v,1) for v in r]} lines={len(texts)}", file=out)
    for t in texts[:6]:
        print("   >", t[:150], file=out)
    if len(texts) > 8:
        print("   ...", file=out)
        for t in texts[-3:]:
            print("   >", t[:150], file=out)
    print("   firstchar:", repr(texts[0][:20]) if texts else None, file=out)
out.close()
