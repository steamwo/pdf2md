# -*- coding: utf-8 -*-
"""删除 PDF 中指定字体/字号的水印 Form XObject。

用法: python clean_watermark.py <输入.pdf> <输出.pdf>
"""
import sys

import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream

# 水印 Form XObject 的特征：Hei 66 号字，或 66 号字 + 固定 CJK 编码串
_WM_FONT = b'/Hei 66 Tf'
_WM_TEXT = b'6570636E57FA7840'


def _is_watermark_xobject(xobj) -> bool:
    try:
        if xobj.get('/Subtype') != '/Form':
            return False
        data = xobj.read_bytes()
        return _WM_FONT in data or (b'66 Tf' in data and _WM_TEXT in data)
    except Exception:
        return False


def _matching_names(page) -> set:
    xobjs = page.obj.Resources.get('/XObject')
    if not xobjs:
        return set()
    return {str(name) for name, x in xobjs.items() if _is_watermark_xobject(x)}


def has_watermark(pdf_path: str) -> bool:
    """是否存在水印 XObject（只读资源字典，比逐页解析内容流快得多）。"""
    with pikepdf.open(pdf_path) as pdf:
        return any(_matching_names(page) for page in pdf.pages)


def count_watermarks(pdf_path: str) -> int:
    """统计命中的水印绘制次数（按页累计），只读不写。"""
    total = 0
    with pikepdf.open(pdf_path) as pdf:
        for page in pdf.pages:
            wm = _matching_names(page)
            if not wm:
                continue
            for operands, operator in parse_content_stream(page):
                if str(operator) == 'Do' and operands and str(operands[0]) in wm:
                    total += 1
    return total


def remove_watermark(src: str, dst: str) -> int:
    """删除水印并另存，返回删除的绘制次数。"""
    removed = 0
    pdf = pikepdf.open(src)
    try:
        for page in pdf.pages:
            xobjs = page.obj.Resources.get('/XObject')
            if not xobjs:
                continue
            wm = _matching_names(page)
            if not wm:
                continue
            new = []
            for operands, operator in parse_content_stream(page):
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
    finally:
        pdf.close()
    return removed


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python clean_watermark.py <input.pdf> <output.pdf>", file=sys.stderr)
        raise SystemExit(2)
    n = remove_watermark(sys.argv[1], sys.argv[2])
    print(f"removed {n} watermark draws -> {sys.argv[2]}")
