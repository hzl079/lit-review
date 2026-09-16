# lit-review

从一组"种子论文"出发，自动抓取关联文献、补全元数据、去重，输出 **Zotero 可直接导入的 BibTeX**。一个命令行工具，替代"在 Connected Papers 网页上逐一点击下载"的手工流程。

## 它能做什么

- **关联文献**：用 Semantic Scholar 的 `recommendations`（ML 推荐，等价于 Connected Papers 图谱）批量抓取。
- **元数据补全**：用 OpenAlex 按 DOI 补全作者全名、期刊/会议、卷期页。
- **去重**：按 DOI → arXiv ID → 归一化标题去重。
- **输出 BibTeX**：标准 `.bib`，交给 Zotero 原生导入（拖进窗口即可），arXiv 预印本自动输出为 `@misc` + `eprint`。

## 为什么不用 Connected Papers 网页 + 手动导入

| 旧做法 | 本工具 |
|---|---|
| 浏览器里逐篇输入、点 More、点 Download | 一个命令批量抓取，可复现 |
| 手写 bib→JSON 转换器导入 Zotero | 输出标准 `.bib`，用 Zotero 自带 translator 导入 |
| 改 Zotero 数据库建分类 | 分类手动建一次，导入时选中即可 |
| 元数据粗（作者缩写、journal=ArXiv） | OpenAlex 补全作者全名与期刊 |

## 依赖

- Python 3（**仅标准库**，无第三方依赖）
- 一个**免费的 Semantic Scholar API key**（强烈建议，否则容易限速 429）

申请：<https://www.semanticscholar.org/product/api#api-key-form>

```bash
export S2_API_KEY=你的key
```

## 快速开始

```bash
# 1. 准备种子文件（每行一个标题/DOI/arXiv ID）
cat > seeds.txt <<'EOF'
2410.12361
10.1145/3706598.3713760
Attention Is All You Need
EOF

# 2. 运行
python scripts/lit_review.py seeds.txt --related-limit 50 --enrich --per-seed --out output/

# 3. 导入 Zotero：把 output/all.bib 拖进 Zotero 窗口（先选中目标分类）
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--related-limit N` | 50 | 每篇种子的关联文献数上限 |
| `--enrich` | 关 | 用 OpenAlex 补全作者/期刊/卷期页 |
| `--per-seed` | 关 | 额外按种子各生成一个 `.bib` |
| `--source` | auto | `auto` / `semanticscholar` / `openalex`（解析源；关联文献始终走 Semantic Scholar） |
| `--out DIR` | lit-review-output | 输出目录 |

## 输出

```
output/
├── all.bib           # 去重后的全部关联文献（核心产出）
├── 01-论文标题.bib    # --per-seed 时按种子分文件
├── seeds.json        # 种子元数据
└── related.json      # 关联文献原始元数据
```

## 导入 Zotero

1. 左侧建分类（一次性）：主题分类 → 右键新建子分类。
2. **选中**目标分类。
3. 把 `all.bib` 拖进窗口，或 `文件 → 导入`。条目自动落入当前选中分类。
4. 跑一次「重复条目」合并（脚本已做一层去重，跨批次仍需 Zotero 兜底）。

## 已知限制

- **关联文献依赖 Semantic Scholar**：无 key 时会限速。OpenAlex 对 2024+ 新论文的关联/引用图几乎是空的，只适合做元数据补全，不适合当关联文献源。
- arXiv 预印本统一输出为 `@misc` + `eprint`，Zotero 会映射为 preprint 类型。

## 目录结构

```
lit-review/
├── SKILL.md                       # Comate skill 定义与工作流
├── README.md                      # 本文件
├── scripts/lit_review.py          # 主脚本（stdlib only）
└── examples/seeds.example.txt     # 种子示例
```

## License

MIT（如发布到 GitHub，可自行替换或补充 LICENSE 文件）。
