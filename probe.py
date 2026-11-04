# -*- coding: utf-8 -*-
"""PDF 预检：判断文档是否适合本工具转换（文本层 + 矢量图形）。

扫描件/图片型 PDF 无文本层，本工具不含 OCR，直接调用转换只会得到空内容。
"""
import os

import pymupdf

import clean_watermark


def resolve_pdf(path):
    """校验并返回绝对路径；不合法时抛异常。"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"PDF 不存在: {path}")
    if not path.lower().endswith(".pdf"):
        raise ValueError(f"不是 .pdf 文件: {path}")
    return path


def probe_pdf(path):
    """返回探测结果字典；路径不合法时抛异常。"""
    path = resolve_pdf(path)

    doc = pymupdf.open(path)
    try:
        pages = doc.page_count
        chars = empty_pages = drawings = images = 0
        for page in doc:
            text = page.get_text("text").strip()
            chars += len(text)
            if not text:
                empty_pages += 1
            drawings += len(page.get_drawings())
            images += len(page.get_images(full=True))
        meta = doc.metadata or {}
    finally:
        doc.close()

    text_based = chars > 0
    if not text_based:
        advice = "无文本层，疑似扫描件/图片型 PDF：本工具无 OCR，无法转换"
    elif drawings == 0 and images > 0:
        advice = "有文本层但无矢量图形：正文可转，插图会丢失"
    else:
        advice = "适合转换（文本层 + 矢量图形）"

    return {
        "ok": True,
        "path": path,
        "pages": pages,
        "chars": chars,
        "empty_pages": empty_pages,
        "drawings": drawings,
        "images": images,
        "watermarks": clean_watermark.has_watermark(path),
        "producer": meta.get("producer"),
        "creator": meta.get("creator"),
        "text_based": text_based,
        "suitable": text_based and drawings > 0,
        "advice": advice,
    }
