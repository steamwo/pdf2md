# -*- coding: utf-8 -*-
"""pdf2md 的 MCP 服务器（stdio）。

只处理「带文本层 + 矢量图形」的数字型 PDF；扫描件无 OCR 能力。

启动:
    pdf2md.exe mcp          # 打包后的二进制
    .venv\\Scripts\\python.exe mcp_server.py

注册（Claude Code）:
    claude mcp add pdf2md -- "<路径>\\pdf2md.exe" "mcp"
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

import clean_watermark
import convert
from probe import probe_pdf, resolve_pdf

mcp = MCPServer("pdf2md")


@mcp.tool()
def pdf_probe(path: str) -> dict:
    """预检 PDF 是否适合转换。

    返回页数、文本字符数、空页数、矢量绘制数、嵌入图片数、是否有水印。
    suitable=False 时不要调用 pdf_to_markdown（扫描件/图片型 PDF 无 OCR）。
    """
    try:
        return probe_pdf(path)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def pdf_clean_watermark(input_pdf: str, output_pdf: str = "") -> dict:
    """删除 PDF 中指定字体/字号的水印（不改动正文），返回删除的绘制次数。"""
    try:
        src = resolve_pdf(input_pdf)
        dst = os.path.abspath(output_pdf) if output_pdf else os.path.join(
            tempfile.gettempdir(), os.path.basename(src)[:-4] + "_clean.pdf")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        removed = clean_watermark.remove_watermark(src, dst)
        return {"ok": True, "removed": removed, "output": dst}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def pdf_to_markdown(input_pdf: str, output_md: str = "",
                    assets_dir: str = "", prefix: str = "",
                    clean_watermark_first: bool = True,
                    keep_source_image: bool = False) -> dict:
    """把 PDF 转成 Markdown。

    - 先按需去水印，再转换；默认产出 <同名>.md 与 <同名>_assets/
    - 标题/段落/列表/代码块/表格/跨页续表会被还原；矢量时序图/流程图优先转 Mermaid
    - keep_source_image=True 时，Mermaid 后额外附可折叠原图（并渲染 300dpi PNG）
    - 返回 tables / mermaid / figures 等统计，供后续校验
    """
    try:
        src = resolve_pdf(input_pdf)
        return {"ok": True, **convert.run_conversion(
            src, output_md, assets_dir, prefix,
            clean_watermark_first=clean_watermark_first,
            keep_source_image=keep_source_image)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def pdf_batch(input_dir: str, pattern: str = "*.pdf",
              clean_watermark_first: bool = True,
              keep_source_image: bool = False) -> dict:
    """批量转换目录下的 PDF，产出与源文件同目录。"""
    try:
        if not os.path.isdir(input_dir):
            raise NotADirectoryError(f"目录不存在: {input_dir}")
        import glob
        files = sorted(glob.glob(os.path.join(input_dir, pattern)))
        results = []
        for f in files:
            r = pdf_to_markdown(f, clean_watermark_first=clean_watermark_first,
                                keep_source_image=keep_source_image)
            results.append({"file": os.path.basename(f), **r})
        return {"ok": True, "total": len(results),
                "succeeded": sum(1 for r in results if r.get("ok")),
                "results": results}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    mcp.run()
