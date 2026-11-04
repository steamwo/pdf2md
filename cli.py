# -*- coding: utf-8 -*-
"""pdf2md 统一命令行入口（也是 PyInstaller 打包入口）。

子命令:
    pdf2md probe   <input.pdf>
    pdf2md clean   <input.pdf> [output.pdf]
    pdf2md convert <input.pdf> [-o out.md] [--assets DIR] [--prefix P]
                   [--keep-source-image] [--no-clean-watermark]
    pdf2md mcp                                   # 以 MCP 服务器方式运行（stdio）
"""
import argparse
import json
import os
import sys
import tempfile


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_probe(args):
    from probe import probe_pdf
    try:
        _emit(probe_pdf(args.input))
        return 0
    except Exception as e:
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1


def cmd_clean(args):
    import clean_watermark
    from probe import resolve_pdf
    try:
        src = resolve_pdf(args.input)
        dst = os.path.abspath(args.output) if args.output else os.path.join(
            tempfile.gettempdir(), os.path.basename(src)[:-4] + "_clean.pdf")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        removed = clean_watermark.remove_watermark(src, dst)
        _emit({"ok": True, "removed": removed, "output": dst})
        return 0
    except Exception as e:
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1


def cmd_convert(args):
    import convert
    try:
        _emit({"ok": True, **convert.run_conversion(
            args.input, args.output, args.assets, args.prefix,
            clean_watermark_first=not args.no_clean_watermark,
            keep_source_image=args.keep_source_image)})
        return 0
    except Exception as e:
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1


def cmd_mcp(args):
    from mcp_server import mcp
    mcp.run()
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="pdf2md",
        description="PDF → Markdown（文本层 + 矢量图形；无 OCR）")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("probe", help="预检 PDF 是否适合转换")
    sp.add_argument("input")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("clean", help="删除水印")
    sp.add_argument("input")
    sp.add_argument("output", nargs="?")
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("convert", help="转换为 Markdown")
    sp.add_argument("input")
    sp.add_argument("-o", "--output", default="", help="输出 .md（默认与源文件同目录）")
    sp.add_argument("--assets", default="", help="图片目录（默认 <同名>_assets）")
    sp.add_argument("--prefix", default="", help="图片名前缀（默认取文件名 ASCII 部分）")
    sp.add_argument("--keep-source-image", action="store_true",
                    help="在 Mermaid 后附带 300dpi 原图")
    sp.add_argument("--no-clean-watermark", action="store_true",
                    help="跳过去水印步骤")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("mcp", help="以 MCP 服务器方式运行（stdio）")
    sp.set_defaults(func=cmd_mcp)
    return p


def _force_utf8_when_piped():
    """打包成 exe 后 PYTHONIOENCODING 会被隔离模式忽略：管道/重定向输出统一成 UTF-8。

    控制台（tty）保持系统编码，避免中文 Windows 下显示乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        try:
            if reconfigure is not None and not stream.isatty():
                reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not (argv and argv[0] == "mcp"):
        _force_utf8_when_piped()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
