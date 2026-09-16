---
name: lit-review
description: 文献调研"滚雪球"工作流：从一组种子论文（标题/DOI/arXiv ID）出发，用 Semantic Scholar API 抓取关联文献、用 OpenAlex 补全元数据、去重，输出 Zotero 可直接导入的 BibTeX。当用户要做文献综述、找某方向的更多相关论文、从几篇论文扩展成文献库、滚雪球式补充参考文献、或想把 Connected Papers 的关联结果导入 Zotero 时使用——即使用户没明确说"滚雪球"或"文献综述"，只要在"从已有论文出发找更多论文"或"整理文献到 Zotero"就用本 skill，而不是去浏览器里点 Connected Papers。
---

# 文献滚雪球（lit-review）

从"种子论文"出发，自动抓取关联文献并整理成 Zotero 可导入的 BibTeX。这是对"手动在 Connected Papers 网页上逐一点击下载"的替代方案。

## 为什么这么做（而非浏览器点击 / 手写转换 / 改数据库）

- **用 API 而不是浏览器**：Connected Papers 的关联图谱底层是 Semantic Scholar，直接用它的 `recommendations` 接口批量抓取，可复现、可调数量、不依赖浏览器调试环境。
- **用 Zotero 原生 BibTeX 导入**：脚本输出标准 `.bib` 文件，交给 Zotero 自带的 BibTeX translator 导入（拖进窗口即可），不要手写 `bib→JSON` 转换器——Zotero 对作者名/条目类型的映射比手写的准。
- **分类靠手动，不碰 Zotero 数据库**：Zotero 的本地 API 只读、connector API 不能建分类。分类是低频一次性操作，让用户右键新建两个分类（几秒），导入时选中目标分类即可。**不要**用"退出 Zotero → 改 SQLite"这种高风险手段。
- **补全元数据 + 去重**：Semantic Scholar 的元数据较粗（作者缩写、journal 常是 ArXiv），用 OpenAlex 按 DOI 补全作者全名/期刊/卷期页；多篇种子的关联文献会有重叠，先按 DOI→arXiv→标题去重。

## 前置条件

1. Python 3（仅标准库，无第三方依赖）。
2. **强烈建议申请免费的 Semantic Scholar API key**（无 key 时限速很严，容易 429）：

   ```bash
   export S2_API_KEY=你的key
   ```

   申请地址：<https://www.semanticscholar.org/product/api#api-key-form>（30 秒、免费）。

   ⚠ 关联文献只能靠 Semantic Scholar（OpenAlex 对新论文的关联/引用图几乎是空的），所以这一步的 key 基本是必需的。

## 工作流

### 第 1 步：写种子文件

每行一个种子，支持 `标题` / `DOI` / `arXiv ID`（如 `2410.12361`）/ arXiv URL。空行和 `#` 开头行忽略。

参考 `examples/seeds.example.txt`。

### 第 2 步：运行脚本

```bash
python scripts/lit_review.py seeds.txt \
  --related-limit 50 \
  --enrich \
  --per-seed \
  --out output/
```

参数：

- `--related-limit N`：每篇种子的关联文献数上限（默认 50）。
- `--enrich`：用 OpenAlex 按 DOI 补全作者全名/期刊/卷期页（推荐）。
- `--per-seed`：额外为每篇种子各生成一个 `.bib`（`01-标题.bib`）。
- `--source auto|semanticscholar|openalex`：解析数据源，默认 `auto`（S2 失败时降级 OpenAlex 解析；但关联文献始终走 S2）。

输出：

- `all.bib`：去重后的全部关联文献（核心产出）。
- `seeds.json` / `related.json`：原始元数据。
- `NN-标题.bib`：`--per-seed` 时按种子分文件。

### 第 3 步：导入 Zotero（原生 BibTeX）

1. 在 Zotero 左侧建分类（一次性）：顶层建一个主题分类（如 `proactive agent`），再右键它建子分类（如 `more`）。
2. **选中目标分类**。
3. 把 `all.bib`（或分文件）**拖进 Zotero 窗口**，或 `文件 → 导入`。Zotero 会自动按 BibTeX 解析，条目落入当前选中的分类。

### 第 4 步：去重与整理

- 导入后在 Zotero 里跑「重复条目」合并（脚本已做了一层 DOI/标题去重，跨批次仍需 Zotero 兜底）。
- 用 Zotero 的「添加时间」排序即可定位本次导入的条目。

## 常见问题

- **一直报 429 / 关联文献抓不到**：没设 `S2_API_KEY`。设置后重试。
- **`--source openalex` 解析正常但关联文献为 0**：OpenAlex 对 2024+ 新论文的关联图稀疏，这是预期行为；关联文献请走 Semantic Scholar。
- **BibTeX 里 arXiv 预印本是 `@misc` + `eprint`**：这是对的，Zotero 会把它映射成 preprint 类型。

## 脚本位置

- `scripts/lit_review.py` —— 单文件主脚本（stdlib only）。
- `examples/seeds.example.txt` —— 种子文件示例。
