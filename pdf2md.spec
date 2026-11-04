# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：输出单文件 pdf2md.exe（内置 PyMuPDF / pikepdf / MCP 运行时）。

构建:
    .venv\\Scripts\\python.exe -m PyInstaller pdf2md.spec --noconfirm

产物: dist\\pdf2md.exe（单文件，免装 Python）。
想要启动更快的目录版，把末尾 EXE(...) 换成 onedir 的 COLLECT 写法即可。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("pymupdf", "pikepdf"):
    d, b, h = collect_all(pkg)
    # mupdf-devel 只是 C 头文件，不参与运行
    datas += [t for t in d if "mupdf-devel" not in t[0]]
    binaries += [t for t in b if "mupdf-devel" not in t[0]]
    hiddenimports += h

hiddenimports += collect_submodules("mcp.server")
hiddenimports += ["mcp_server", "probe", "convert", "clean_watermark", "mermaid_diagram"]

a = Analysis(
    ["cli.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pdf2md",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
