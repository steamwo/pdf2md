import sys, io, pymupdf

path, pno, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
if outp.lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")
doc = pymupdf.open(path)
page = doc[pno - 1]
tabs = page.find_tables()
out = io.open(outp, "w", encoding="utf-8")
print("tables:", len(tabs.tables), file=out)
for ti, t in enumerate(tabs.tables):
    print(f"--- table {ti} bbox={t.bbox} rows={t.row_count} cols={t.col_count}", file=out)
    for row in t.extract():
        print(row, file=out)
    print("header:", t.header.names if t.header else None, file=out)

print("\nDRAWINGS:", file=out)
for d in page.get_drawings():
    r = d["rect"]
    if r.width > 50 and r.height > 10:
        print(f"type={d['type']} fill={d.get('fill')} color={d.get('color')} width={d.get('width')} rect={[round(v,1) for v in r]} items={len(d['items'])}", file=out)
out.close()
