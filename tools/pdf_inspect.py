import sys, io, pymupdf

if len(sys.argv) < 3:
    sys.exit("usage: python tools/pdf_inspect.py input.pdf [input2.pdf ...] output.txt")

outp = sys.argv[-1]
paths = sys.argv[1:-1]
if outp.lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")

out = io.open(outp, "w", encoding="utf-8")
for path in paths:
    doc = pymupdf.open(path)
    print("=" * 80, file=out)
    print("FILE:", path, file=out)
    print("pages:", doc.page_count, file=out)
    print("metadata:", doc.metadata, file=out)
    print("toc:", doc.get_toc()[:30], file=out)
    for pno in range(min(4, doc.page_count)):
        page = doc[pno]
        print(f"--- page {pno+1} size={page.rect.width}x{page.rect.height} ---", file=out)
        d = page.get_text("dict")
        print("blocks:", len(d["blocks"]), "images:", len(page.get_images(full=True)), file=out)
        for b in d["blocks"]:
            if b["type"] == 0:
                for line in b["lines"][:6]:
                    spans = line["spans"]
                    txt = "".join(s["text"] for s in spans)
                    fonts = sorted({(s["font"], round(s["size"], 1), hex(s["color"])) for s in spans})
                    print("  TEXT", fonts, "bbox", [round(v) for v in b["bbox"]], "|", txt[:160], file=out)
            else:
                print("  IMAGE bbox=", [round(v) for v in b["bbox"]], file=out)
    doc.close()
out.close()
