# -*- coding: utf-8 -*-
"""
Merge JSON → CSV → Quick Look figs + (optional) interactive dashboard + markdown report.

Usage:
  python scripts/merge_and_quicklook.py \
      --raw-dir "data/raw" \
      --out "reports" \
      --csv "data/processed/merged_cross_cultural_data.csv" \
      --with-plotly  


  python scripts/merge_and_quicklook.py --raw-dir "data raw" --out "reports"
"""
import os, re, glob, json, argparse, warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from pandas import json_normalize

PLOTLY_AVAILABLE = True
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:
    PLOTLY_AVAILABLE = False

plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
sns.set_style("whitegrid")


def guess_culture_model(fname):
    base = os.path.basename(fname).lower()
    # culture
    if 'china' in base or re.search(r'\bcn\b', base): culture = "Chinese"
    elif 'german' in base or 'germany' in base or re.search(r'\bde(_|\b)', base): culture = "German"
    elif 'swed' in base or 'sweden' in base or 'swedish' in base or 'se_' in base: culture = "Swedish"
    elif re.search(r'\bus\b', base) or 'usa' in base or 'american' in base: culture = "American"
    else: culture = "Unknown"
    # model 
    if re.search(r'claude[-_ ]?4|sonnet\s*4', base): model = "Claude-4"
    elif re.search(r'gpt[-_ ]?4(\.1)?|gpt4', base): model = "GPT-4"
    elif re.search(r'deepseek[-_ ]?v?3', base): model = "DeepSeek-V3"
    elif re.search(r'gemini[-_ ]?2\.5', base): model = "Gemini-2.5"
    else: model = "Unknown"
    return culture, model

def standardize_model(name):
    if not isinstance(name, str): return name
    low = name.lower()
    if 'claude' in low and '4' in low: return 'Claude-4'
    if 'gpt' in low and '4' in low: return 'GPT-4'
    if 'deepseek' in low and ('v3' in low or 'v-3' in low): return 'DeepSeek-V3'
    if 'gemini' in low and ('2.5' in low or '2_5' in low): return 'Gemini-2.5'
    return name


# ---------- 读取 JSON，优先解析 {task_results: [{basic_info, interaction_data}]} 结构 ----------
def load_json_records(path):
    """
    返回 DataFrame。若检测到 task_results 结构，则抽取核心字段；
    否则用 json_normalize 退化解析，并补充从文件名推断的 culture/model。
    """
    # 读取文本
    with open(path, 'r', encoding='utf-8') as f:
        txt = f.read().strip()

    # 尝试整体json
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError:
        # 按行解析（jsonlines）
        records = []
        for line in txt.splitlines():
            line = line.strip()
            if not line: continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        if not records:
            return pd.DataFrame()
        df = json_normalize(records, max_level=2)
        return df

    # 如果是我们期望的结构：{"task_results": [ {basic_info, interaction_data}, ... ]}
    if isinstance(obj, dict) and isinstance(obj.get('task_results'), list):
        rows = []
        for task in obj['task_results']:
            basic = task.get('basic_info', {}) or {}
            inter  = task.get('interaction_data', {}) or {}
            # Cultural_Keywords 既可能是list也可能是str
            ck = inter.get('Cultural_Keywords', None)
            if isinstance(ck, (list, tuple)):
                ck = ', '.join([str(x) for x in ck])
            # 记录
            rows.append({
                'task_id': basic.get('TaskID', ''),
                'timestamp': basic.get('Timestamp', ''),
                'model': basic.get('Model', ''),
                'temperature': basic.get('Temperature', ''),
                'culture': basic.get('Culture', ''),
                'function': basic.get('Function', ''),
                'complexity': basic.get('Complexity', ''),
                'language': basic.get('Language', ''),
                'scenario': basic.get('Scenario', ''),
                'prompt_text': inter.get('Prompt_Text', ''),
                'response_text': inter.get('Response_Text', ''),
                'response_time': inter.get('Response_Time', None),
                'word_count': inter.get('Word_Count', None),
                'cultural_keywords': ck,
                'model_version': inter.get('Model_Version', ''),
            })
        return pd.DataFrame(rows)

    # 其他结构：list 或 dict，尽量展开
    if isinstance(obj, list):
        return json_normalize(obj, max_level=2)
    if isinstance(obj, dict):
        # 常见容器 key
        for k in ['results', 'data', 'items', 'records']:
            if isinstance(obj.get(k), list):
                return json_normalize(obj[k], max_level=2)
        return json_normalize(obj, max_level=2)

    return pd.DataFrame()


# ---------- 合并目录下所有 JSON ----------
def merge_raw_json(raw_dir):
    files = sorted(glob.glob(os.path.join(raw_dir, "*.json")))
    if not files:
        raise FileNotFoundError(f"未在 {raw_dir} 找到 .json 文件")
    frames = []
    total_rows = 0
    for fp in files:
        df = load_json_records(fp)
        if df.empty:
            print(f"跳过：无法解析 {os.path.basename(fp)}")
            continue
        # 若缺少 culture/model，从文件名推断
        if 'culture' not in df.columns or df['culture'].isna().all():
            c, _ = guess_culture_model(fp)
            df['culture'] = df.get('culture', c) if 'culture' in df.columns else c
        if 'model' not in df.columns or df['model'].isna().all():
            _, m = guess_culture_model(fp)
            df['model'] = df.get('model', m) if 'model' in df.columns else m

        df['source_file'] = os.path.basename(fp)
        frames.append(df)
        total_rows += len(df)
        print(f"读取 {os.path.basename(fp)} → {len(df)} 行")
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    print(f"合并完成：{len(files)} 个文件，共 {total_rows} 行")
    return merged


# ---------- 预处理（数值转换、模型名标准化、文化代码等） ----------
def preprocess(df):
    # 数值列转换（尽量不丢行）
    for col in ['response_time', 'word_count', 'temperature']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 标准化模型名
    if 'model' in df.columns:
        df['model_standardized'] = df['model'].apply(standardize_model)
    else:
        df['model_standardized'] = 'Unknown'

    # 文化代码（可选）
    if 'culture' in df.columns:
        culture_map = {'Chinese':'CN', 'American':'US', 'German':'DE', 'Swedish':'SE'}
        df['culture_code'] = df['culture'].map(culture_map)

    return df


# ---------- 出表 + 出图 ----------
def export_tables_and_figs(df, out_dir):
    fig_dir = os.path.join(out_dir, "figures")
    tbl_dir = os.path.join(out_dir, "tables")
    os.makedirs(fig_dir, exist_ok=True)
    os.makedirs(tbl_dir, exist_ok=True)

    # 1) 缺失率 & 计数表
    df.isna().mean().sort_values(ascending=False).to_frame("missing_rate").to_csv(
        os.path.join(tbl_dir, "missing_rate.csv"))
    if 'culture' in df.columns and 'model_standardized' in df.columns:
        counts = df.groupby(['culture','model_standardized']).size().reset_index(name='n')
        counts.to_csv(os.path.join(tbl_dir, "counts_by_culture_model.csv"), index=False)

    # 2) 基础分布 + 箱线（若有数值）
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    # 基础分布图：response_time / word_count
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.ravel()

    if 'response_time' in df.columns and df['response_time'].notna().any():
        sns.histplot(df['response_time'].dropna(), bins=30, kde=True, ax=axes[0], color='skyblue')
        axes[0].set_title('响应时间分布'); axes[0].set_xlabel('秒')
    else:
        axes[0].set_visible(False)

    if 'word_count' in df.columns and df['word_count'].notna().any():
        sns.histplot(df['word_count'].dropna(), bins=30, kde=True, ax=axes[1], color='lightgreen')
        axes[1].set_title('词数分布'); axes[1].set_xlabel('词数')
    else:
        axes[1].set_visible(False)

    if 'culture' in df.columns and 'response_time' in df.columns and df['response_time'].notna().any():
        sns.boxplot(data=df, x='culture', y='response_time', ax=axes[2], color='#55a868')
        axes[2].set_title('不同文化的响应时间'); axes[2].tick_params(axis='x', rotation=20)
    else:
        axes[2].set_visible(False)

    if 'model_standardized' in df.columns and 'response_time' in df.columns and df['response_time'].notna().any():
        sns.boxplot(data=df, x='model_standardized', y='response_time', ax=axes[3], color='#c44e52')
        axes[3].set_title('不同模型的响应时间'); axes[3].tick_params(axis='x', rotation=20)
    else:
        axes[3].set_visible(False)

    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "basic_analysis.png"), dpi=300, bbox_inches='tight')
    plt.close()
    print("保存：figures/basic_analysis.png")

    # 文化×模型热力图、复杂度/场景柱状（按可用性绘制）
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    axes = axes.ravel()

    plotted = False
    if {'culture', 'model_standardized', 'response_time'} <= set(df.columns):
        pivot_rt = df.pivot_table(values='response_time', index='culture', columns='model_standardized', aggfunc='mean')
        if pivot_rt.notna().any().any():
            sns.heatmap(pivot_rt, annot=True, cmap='YlOrRd', ax=axes[0], cbar_kws={'label':'平均响应时间(秒)'})
            axes[0].set_title('文化×模型：响应时间'); plotted = True
    if {'culture', 'model_standardized', 'word_count'} <= set(df.columns):
        pivot_wc = df.pivot_table(values='word_count', index='culture', columns='model_standardized', aggfunc='mean')
        if pivot_wc.notna().any().any():
            sns.heatmap(pivot_wc, annot=True, cmap='Blues', ax=axes[1], cbar_kws={'label':'平均词数'})
            axes[1].set_title('文化×模型：词数'); plotted = True

    if {'culture','complexity','response_time'} <= set(df.columns) and df['response_time'].notna().any():
        bar = df.groupby(['culture','complexity'])['response_time'].mean().unstack()
        bar.plot(kind='bar', ax=axes[2], color=['lightcoral','lightblue'])
        axes[2].set_title('不同复杂度的响应时间'); axes[2].set_ylabel('秒'); axes[2].tick_params(axis='x', rotation=20); plotted = True
    else:
        axes[2].set_visible(False)

    if {'culture','scenario','word_count'} <= set(df.columns) and df['word_count'].notna().any():
        bar2 = df.groupby(['culture','scenario'])['word_count'].mean().unstack()
        bar2.plot(kind='bar', ax=axes[3], colormap='tab20')
        axes[3].set_title('不同场景的词数'); axes[3].set_ylabel('词数'); axes[3].tick_params(axis='x', rotation=20); plotted = True
    else:
        axes[3].set_visible(False)

    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "cultural_model_analysis.png"), dpi=300, bbox_inches='tight')
    plt.close()
    print("保存：figures/cultural_model_analysis.png")

    return num_cols


# ---------- 交互式仪表板（可选） ----------
def export_dashboard(df, out_dir):
    if not PLOTLY_AVAILABLE:
        print("未安装 plotly，跳过交互式仪表板。")
        return
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=('文化响应时间', '模型响应时间', '复杂度响应时间', '场景词数'),
        specs=[[{"type": "bar"}, {"type": "bar"}],
               [{"type": "bar"}, {"type": "bar"}]]
    )
    if 'culture' in df.columns and 'response_time' in df.columns:
        m = df.groupby('culture')['response_time'].mean()
        fig.add_bar(x=m.index, y=m.values, name='文化-响应时间', row=1, col=1)
    if 'model_standardized' in df.columns and 'response_time' in df.columns:
        m = df.groupby('model_standardized')['response_time'].mean()
        fig.add_bar(x=m.index, y=m.values, name='模型-响应时间', row=1, col=2)
    if 'complexity' in df.columns and 'response_time' in df.columns:
        m = df.groupby('complexity')['response_time'].mean()
        fig.add_bar(x=m.index, y=m.values, name='复杂度-响应时间', row=2, col=1)
    if 'scenario' in df.columns and 'word_count' in df.columns:
        m = df.groupby('scenario')['word_count'].mean()
        fig.add_bar(x=m.index, y=m.values, name='场景-词数', row=2, col=2)

    fig.update_layout(height=800, showlegend=False, title_text="跨文化AI评估交互式仪表板", title_x=0.5)
    html_path = os.path.join(out_dir, "interactive_dashboard.html")
    fig.write_html(html_path)
    print(f"保存：{html_path}")


# ---------- 简短 Markdown 报告 ----------
def export_report(df, out_dir):
    def uniq(col):
        return ', '.join([str(x) for x in df[col].dropna().unique().tolist()]) if col in df.columns else 'NA'

    rpt = []
    rpt.append("# 跨文化AI评估数据分析报告\n")
    rpt.append("## 数据概览")
    rpt.append(f"- 总样本数: {len(df):,}")
    rpt.append(f"- 文化背景: {uniq('culture')}")
    rpt.append(f"- AI模型: {uniq('model_standardized') if 'model_standardized' in df.columns else uniq('model')}")
    if 'complexity' in df.columns: rpt.append(f"- 复杂度: {uniq('complexity')}")
    if 'language' in df.columns: rpt.append(f"- 语言: {uniq('language')}")
    if 'scenario' in df.columns: rpt.append(f"- 场景: {uniq('scenario')}")

    if 'response_time' in df.columns and df['response_time'].notna().any():
        rpt.append("\n## 响应时间")
        rpt.append(f"- 平均: {df['response_time'].mean():.2f} 秒")
        rpt.append(f"- 标准差: {df['response_time'].std():.2f} 秒")
        rpt.append(f"- 范围: {df['response_time'].min():.2f} ~ {df['response_time'].max():.2f} 秒")

    if 'word_count' in df.columns and df['word_count'].notna().any():
        rpt.append("\n## 词数")
        rpt.append(f"- 平均: {df['word_count'].mean():.1f} 词")
        rpt.append(f"- 标准差: {df['word_count'].std():.1f} 词")
        rpt.append(f"- 范围: {df['word_count'].min():.0f} ~ {df['word_count'].max():.0f} 词")

    if {'culture','response_time'} <= set(df.columns):
        m = df.groupby('culture')['response_time'].agg(['mean','std','count'])
        rpt.append("\n## 文化差异（响应时间，均值±SD, n）")
        for idx, row in m.iterrows():
            rpt.append(f"- {idx}: {row['mean']:.2f}±{row['std']:.2f} 秒 (n={int(row['count'])})")

    if {'model_standardized','response_time'} <= set(df.columns):
        m = df.groupby('model_standardized')['response_time'].agg(['mean','std','count'])
        rpt.append("\n## 模型差异（响应时间，均值±SD, n）")
        for idx, row in m.iterrows():
            rpt.append(f"- {idx}: {row['mean']:.2f}±{row['std']:.2f} 秒 (n={int(row['count'])})")

    rpt.append("\n---")
    rpt.append(f"报告生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "analysis_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(rpt))
    print(f"保存：{path}")


# ---------- 主流程 ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="data/raw", help="JSON 原始目录")
    parser.add_argument("--out", default="reports", help="输出目录（图、表、报告）")
    parser.add_argument("--csv", default="data/processed/merged_cross_cultural_data.csv", help="合并后的CSV输出路径")
    parser.add_argument("--with-plotly", action="store_true", help="生成交互式HTML（需安装 plotly）")
    args = parser.parse_args()

    # 1) 合并
    merged = merge_raw_json(args.raw_dir)
    if merged.empty:
        print("没有可用数据，退出。"); return

    # 2) 预处理
    merged = preprocess(merged)

    # 3) 保存合并CSV
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    merged.to_csv(args.csv, index=False, encoding="utf-8")
    print(f"💾 合并数据已保存：{args.csv}  形状={merged.shape}")

    # 4) 出表+出图
    os.makedirs(args.out, exist_ok=True)
    export_tables_and_figs(merged, args.out)

    # 5) 交互式仪表板（可选）
    if args.with-plotly:
        export_dashboard(merged, args.out)

    # 6) 报告
    export_report(merged, args.out)

    # 7) 终端总结
    fastest_culture = merged.groupby('culture')['response_time'].mean().dropna().sort_values().index[0] \
                        if {'culture','response_time'} <= set(merged.columns) and merged['response_time'].notna().any() else 'NA'
    fastest_model = merged.groupby('model_standardized')['response_time'].mean().dropna().sort_values().index[0] \
                        if {'model_standardized','response_time'} <= set(merged.columns) and merged['response_time'].notna().any() else 'NA'
    print("\n====== 总结 ======")
    print(f"- 样本数：{len(merged):,}")
    print(f"- 文化数：{merged['culture'].nunique() if 'culture' in merged.columns else 'NA'}")
    print(f"- 模型数：{merged['model_standardized'].nunique() if 'model_standardized' in merged.columns else 'NA'}")
    print(f"- 响应最快文化：{fastest_culture}")
    print(f"- 响应最快模型：{fastest_model}")
    print("完成 reports/")

if __name__ == "__main__":
    main()
