import sys, io, collections, pymupdf

path = sys.argv[1]
if sys.argv[2].lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")
out = io.open(sys.argv[2], "w", encoding="utf-8")
doc = pymupdf.open(path)
print("pages:", doc.page_count, file=out)

font_stats = collections.Counter()
watermark_pages = []
mono_pages = []
for pno in range(doc.page_count):
    page = doc[pno]
    d = page.get_text("dict")
    wm = []
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        for line in b["lines"]:
            for s in line["spans"]:
                key = (s["font"], round(s["size"], 1), s["color"], s.get("flags"))
                font_stats[key] += 1
                if s["size"] > 40 and len(s["text"].strip()) > 2:
                    wm.append((s["font"], round(s["size"], 1), s["text"].strip()))
    if wm:
        watermark_pages.append((pno + 1, wm))
    # detect monospace-ish fonts
    txt_fonts = {s["font"] for b in d["blocks"] if b["type"] == 0 for l in b["lines"] for s in l["spans"]}
    if any("Mono" in f or "Courier" in f or "Consolas" in f or "Code" in f for f in txt_fonts):
        mono_pages.append(pno + 1)

print("\nFONT STATS (font,size,color,flags): count", file=out)
for k, v in font_stats.most_common(80):
    print(k, v, file=out)

print("\nWATERMARK PAGES count:", len(watermark_pages), file=out)
for p, wm in watermark_pages[:10]:
    print(p, wm, file=out)
print("...", file=out)
print("\nMONO PAGES:", mono_pages[:50], "total", len(mono_pages), file=out)

# save all spans of selected pages for inspection
for pno in [int(x) for x in sys.argv[3:]]:
    page = doc[pno - 1]
    print(f"\n===== PAGE {pno} FULL SPANS =====", file=out)
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0:
            print("IMAGE", b["bbox"], file=out)
            continue
        for line in b["lines"]:
            txt = "".join(s["text"] for s in line["spans"])
            s0 = line["spans"][0]
            print(f"bbox={[round(v,1) for v in line['bbox']]} dir={line['dir']} font={s0['font']} size={round(s0['size'],1)} color={hex(s0['color'])} | {txt}", file=out)

out.close()
