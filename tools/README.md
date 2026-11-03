# 诊断工具

这些脚本用于定位 PDF 解析问题（字体、代码块、表格网格、页面结构），不属于转换主链路。所有脚本都是纯 PyMuPDF，无额外依赖。

在仓库根目录运行，输入 PDF 路径可指向任意位置，输出为 UTF-8 文本。

| 脚本 | 用途 | 用法 |
| --- | --- | --- |
| `pdf_inspect.py` | 查看 PDF 元数据、前 4 页文本/图片块和字体 | `python tools/pdf_inspect.py input.pdf inspect.txt` |
| `scan_fonts.py` | 统计全文“字体/字号/颜色”组合，并列出标题候选 | `python tools/scan_fonts.py input.pdf fonts.txt` |
| `scan_code.py` | 定位代码块容器，打印容器内前几行和首字符 | `python tools/scan_code.py input.pdf code.txt` |
| `analyze.py` | 统计水印、等宽字体页面，并可按页码导出完整 span | `python tools/analyze.py input.pdf analyze.txt 12 20` |
| `inspect_grid.py` | 导出指定页的表格横竖线坐标和逐行单元格文本 | `python tools/inspect_grid.py input.pdf 20 grid.txt` |
| `inspect_tables.py` | 对比 PyMuPDF `find_tables()` 结果和页面绘制对象 | `python tools/inspect_tables.py input.pdf 20 tables.txt` |

其中 `inspect_grid.py` 和 `inspect_tables.py` 由早期 `test_grid.py`、`test_tables.py` 改名而来，它们是命令行排查脚本，不依赖 pytest。
