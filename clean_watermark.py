import sys, os
import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream

src, dst = sys.argv[1], sys.argv[2]
pdf = pikepdf.open(src)
removed = 0
for page in pdf.pages:
    xobjs = page.obj.Resources.get('/XObject')
    if not xobjs:
        continue
    wm = set()
    for name, x in xobjs.items():
        try:
            if x.get('/Subtype') != '/Form':
                continue
            data = x.read_bytes()
            if b'/Hei 66 Tf' in data or (b'66 Tf' in data and b'6570636E57FA7840' in data):
                wm.add(str(name))
        except Exception:
            pass
    if not wm:
        continue
    instructions = parse_content_stream(page)
    new = []
    for operands, operator in instructions:
        if str(operator) == 'Do' and operands and str(operands[0]) in wm:
            removed += 1
            continue
        new.append((operands, operator))
    page.Contents = pdf.make_stream(unparse_content_stream(new))
    for name in wm:
        try:
            del xobjs[name]
        except Exception:
            pass
pdf.save(dst, compress_streams=True)
print(f"removed {removed} watermark draws -> {dst}")
