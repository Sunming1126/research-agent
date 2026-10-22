# 本地百篇论文库

`pdfs/` 存放 100 篇来自 arXiv 的真实 PDF，供科研助手本地评测模拟授权论文库。`manifest.json` 记录每篇论文的原始页面、PDF 地址、标题、摘要、学科、页数、大小和 SHA-256，可用清单逐一复核。七篇前期已下载并校验的研究助手论文与新下载的 93 篇跨领域论文共同构成恰好 100 篇。

运行 `python scripts/build_paper_library.py` 可断点续跑。脚本遵循 arXiv 官方 API 的三秒请求间隔，并检查 PDF 文件头、文本层、页数、哈希和唯一编号。目录内原始 PDF 只供本地研究评测，不进入 Git 或 Docker 镜像；对外分享报告时请链接 `https://arxiv.org/abs/<编号>`，不要重新分发 PDF。

来源：[arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)、[API 使用条款](https://info.arxiv.org/help/api/tou.html)、[学科分类](https://arxiv.org/category_taxonomy)。
