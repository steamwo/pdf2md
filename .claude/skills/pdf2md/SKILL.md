---
name: pdf2md
description: PDF → Markdown 转换（保留标题层级、表格、代码块、列表；矢量时序图/流程图转 Mermaid），支持去水印与批量转换。当用户要求「把 PDF 转成 md」「PDF 喂给 LLM」「批量转换文档」「去掉 PDF 水印」时使用。仅适用于带文本层 + 矢量图形的数字型 PDF，扫描件/图片型 PDF 无 OCR 能力。
---

# pdf2md

纯 Python 的 PDF → Markdown 工具，针对 Chrome/Skia 渲染的中文技术文档调校。

## 适用判定（先做）

| 特征 | 结论 |
|---|---|
| 有文本层 + 有矢量绘制 | 适合，全功能 |
| 有文本层、无矢量绘制 | 正文可转，插图会丢失 |
| 无文本层（扫描件/截图） | **不支持**，直接告诉用户需要先 OCR（如 ocrmypdf） |

先调用 MCP 工具 `pdf_probe`（或 `python tools/pdf_inspect.py`）确认，不要盲目转换。

## 首选：MCP 工具

| 工具 | 用途 |
|---|---|
| `pdf_probe(path)` | 预检：页数、字符数、空页、drawings、图片数、是否有水印、是否适合转换 |
| `pdf_to_markdown(input_pdf, output_md?, assets_dir?, prefix?, clean_watermark_first=True, keep_source_image=False)` | 主转换，返回 tables/mermaid/figures 等统计 |
| `pdf_batch(input_dir, pattern="*.pdf")` | 批量转换，产出与源文件同目录 |
| `pdf_clean_watermark(input_pdf, output_pdf?)` | 只去水印，返回删除次数 |

默认产出：`<同名>.md` + `<同名>_assets/`（无图片时不建目录）。图片引用为相对路径。

## 零依赖分发（exe）

`dist\pdf2md.exe` 是单文件二进制（约 120 MB，内置 Python / PyMuPDF / pikepdf / MCP），目标机无需安装任何环境：

```powershell
pdf2md.exe probe   input.pdf
pdf2md.exe clean   input.pdf cleaned.pdf
pdf2md.exe convert input.pdf -o out.md --assets out_assets --prefix p1
pdf2md.exe mcp                    # MCP stdio 服务器
```

源文件改动后重新构建：`.venv\Scripts\python.exe -m PyInstaller pdf2md.spec --noconfirm`。
已打包时 MCP 注册命令：`claude mcp add pdf2md -- "<仓库>\dist\pdf2md.exe" "mcp"`。

## 回退：CLI

```powershell
# 仓库根目录，使用仓库自带 venv
.\.venv\Scripts\python.exe clean_watermark.py input.pdf cleaned.pdf   # 可选
.\.venv\Scripts\python.exe convert.py cleaned.pdf out.md out_assets p1
```

参数顺序：输入 PDF、输出 md、图片目录、资产前缀。转换结束会打印 JSON 统计。

## 关键参数

- `clean_watermark_first=True`：先删 `Hei 66 Tf` 风格水印再转换；无水印时自动跳过
- `keep_source_image=False`：默认只保留 Mermaid，不渲染原图；设 `True` 会在 Mermaid 后附 300dpi 原图
- `assets_dir`：转换失败的图（非时序图/流程图、或识别置信度不足）会落成 PNG 并被引用

## 验收清单

1. md 文件存在且页数与 `pdf_probe` 一致量级
2. 表格中没有全空行（`|   |   |`）
3. 每个 `![](...)` 引用的图片文件都存在
4. `pdf_to_markdown` 返回的 `mermaid`/`figures` 数量与文档中图表数量相符

## 已知限制

- 扫描件、图片型 PDF 不支持（无 OCR）
- 表格跨页续表的判定是启发式的（列坐标一致 + 下页页首）；列数在续页塌缩时会退化为分表
- 分层架构图、思维导图等不适合 Mermaid，会保留原图
- 版式与水印规则针对 Chrome/Skia 渲染调校，其他生成器可能需要调阈值

## 排错

- `pymupdf` 导入失败：用仓库 venv（`.venv\Scripts\python.exe`），不要用系统 Python
- 中文路径报错：全部参数用绝对路径并加引号
- 转换很慢：先去水印再转换可复用中间件；300dpi 渲染仅在需要原图时发生
