# 大型语言模型跨文化敏感性评估（进行中 · MVP）

本仓库公开了正在进行的跨文化敏感性研究的原始数据（JSON）与最小可复现分析脚本。当前版本包含：
- data/raw：按国家×模型的原始结果（JSON）
- scripts/merge_and_quicklook.py：合并JSON并输出基础图表与简报
- reports/：自动生成的图表与表格，以及 summary.md

快速运行：
```bash
pip install -r requirements.txt
python scripts/merge_and_quicklook.py --raw-dir "data/raw" --out "reports"
