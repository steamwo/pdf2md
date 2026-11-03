import sys, io, collections, pymupdf

path, outp = sys.argv[1], sys.argv[2]
if outp.lower().endswith(".pdf"):
    sys.exit("output must be a text file, not a PDF")
doc = pymupdf.open(path)
out = io.open(outp, "w", encoding="utf-8")
stats = collections.Counter()
samples = collections.defaultdict(list)
for pno in range(doc.page_count):
    d = doc[pno].get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0: continue
        for l in b["lines"]:
            for s in l["spans"]:
                t = s["text"].strip()
                if not t: continue
                key = (s["font"], round(s["size"],1), hex(s["color"]))
                stats[key] += 1
                if len(samples[key]) < 4:
                    samples[key].append((pno+1, t[:80]))
print("FONT/SIZE/COLOR counts:", file=out)
for k,v in stats.most_common():
    print(k, v, samples[k], file=out)

# heading-like spans: bold or size > 12.5, group by size
print("\nHEADING-LIKE BY SIZE:", file=out)
hs = collections.Counter()
hsamp = collections.defaultdict(list)
for pno in range(doc.page_count):
    d = doc[pno].get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0: continue
        for l in b["lines"]:
            txt = "".join(s["text"] for s in l["spans"]).strip()
            if not txt: continue
            s0 = l["spans"][0]
            bold = "Bold" in s0["font"]
            if s0["size"] > 12.5 or bold:
                key = (s0["font"], round(s0["size"],1), bold)
                hs[key]+=1
                if len(hsamp[key])<8: hsamp[key].append((pno+1,txt[:90]))
for k,v in hs.most_common():
    print(k, v, hsamp[k], file=out)
out.close()
