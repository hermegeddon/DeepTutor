# DeepTutor Pairwise Human Alignment 使用说明

这个工具用于做盲法成对偏好人类对齐实验：人类评审员比较同一 TutorBench session 下的 `deep_tutor` 和 `mock` 输出，但只看到匿名的 System A / System B。评审员在既有 10 个 TutorBench metrics 上选择 `A`、`B` 或 `tie`。汇总脚本会把人类多数偏好与 Step 3 LLM-as-judge 分数诱导出的偏好进行对比。

核心流程：

```text
Step 2 transcripts + Step 3 eval JSON
        ↓
export_annotations.py
        ↓
annotation_package.jsonl + review_ui.html + annotation_key.json
        ↓
人类评审导出 completed_annotations.csv
        ↓
summarize_annotations.py
        ↓
human_alignment_summary.json/.md
```

## 1. 前置条件

需要已有 benchmark pipeline 输出：

```text
benchmark/data/bench_pipeline/
├── transcripts/<kb_name>/deep_tutor/<profile_id>.json
├── transcripts/<kb_name>/mock/<profile_id>.json
├── evaluations/<kb_name>/deep_tutor/<profile_id>_eval.json
└── evaluations/<kb_name>/mock/<profile_id>_eval.json
```

导出脚本会按 `(kb_name, profile_id, entry_id/session_index)` 匹配 `deep_tutor` 与 `mock` session。匹配不到的 session 会写入 `manifest.json`，不会中断导出。

## 2. 导出 A/B 盲评包

```bash
python3 -m benchmark.human_alignment.export_annotations \
  --output-root benchmark/data/bench_pipeline \
  --kb-names "Calculus,LinearAlgebra" \
  --target-backend deep_tutor \
  --baseline-backend mock \
  --limit-pairs 40 \
  --seed 13
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--output-root` | benchmark pipeline 输出根目录 |
| `--kb-names` | 要导出的 KB 名称，逗号分隔 |
| `--target-backend` | 目标系统，默认 `deep_tutor` |
| `--baseline-backend` | baseline 系统，默认 `mock` |
| `--limit-pairs` | 最多导出多少个 matched pairs；`0` 表示全量 |
| `--source-chars` | 每个 source page 给评审员看的最大字符数，默认 `1500` |
| `--output-dir` | 导出目录；默认 `<output-root>/human_alignment_pairwise` |
| `--seed` | A/B 随机化和抽样 seed |

默认输出：

```text
benchmark/data/bench_pipeline/human_alignment_pairwise/
├── annotation_package.jsonl
├── annotation_template.csv
├── annotation_key.json
├── review_ui.html
├── rubric.md
└── manifest.json
```

## 3. 文件说明

### 给评审员的文件

只给：

```text
annotation_package.jsonl
review_ui.html
rubric.md
```

`annotation_package.jsonl` 是盲评包，一行一个 pair。每个 pair 包含：

- `pair_id`
- 共享的 `profile`
- 共享的 `task`
- 共享的 `gaps` 和 `source_excerpts`
- `system_a.dialog`
- `system_a.practice_questions`
- `system_b.dialog`
- `system_b.practice_questions`

它不包含 backend 名称。

### 自己保留的文件

```text
annotation_key.json
```

这个文件包含：

- System A / B 分别对应哪个 backend
- transcript/evaluation 文件路径
- entry/session 定位信息

不要给评审员，否则就不是盲评。

## 4. 前端评审流程

打开：

```text
benchmark/data/bench_pipeline/human_alignment_pairwise/review_ui.html
```

步骤：

1. 输入 `Rater ID`
2. 上传 `annotation_package.jsonl`
3. 对每个 pair 比较 System A 与 System B
4. 每个 metric 选择 `A better`、`Tie` 或 `B better`
5. 可选填写 `comment`
6. 点击 `Export CSV`

导出的 CSV 字段：

```text
pair_id,rater_id,SF,PER,APP,VID,LD,FIT,GND,DIV,ANS,CC,comment
```

偏好值为：

```text
A
B
tie
```

前端会用浏览器 `localStorage` 保存进度。也可以通过 `Resume CSV` 继续编辑旧 CSV。

## 5. Metrics

Transcript metrics：

| 字段 | 含义 |
| --- | --- |
| `SF` | Source faithfulness：哪个 tutor 更忠实于 source excerpts |
| `PER` | Personalization：哪个 tutor 更贴合学生 profile、知识状态和困惑 |
| `APP` | Applicability：哪个 tutor 更能帮助学生完成 task |
| `VID` | Vividness：哪个 tutor 解释更具体、生动、有例子 |
| `LD` | Logical depth：哪个 tutor 的推理和概念展开更充分 |

Practice question metrics：

| 字段 | 含义 |
| --- | --- |
| `FIT` | 哪组 practice questions 更适合学生和目标 gaps |
| `GND` | 哪组 practice questions 更符合 source excerpts |
| `DIV` | 哪组题覆盖角度更多样 |
| `ANS` | 哪组题答案/选项质量更好 |
| `CC` | 哪组题更能连接相关概念 |

## 6. 多评审员合并

每个评审员独立导出 CSV：

```text
completed_annotations_rater_01.csv
completed_annotations_rater_02.csv
completed_annotations_rater_03.csv
```

合并：

```bash
head -n 1 completed_annotations_rater_01.csv > completed_annotations_all.csv
tail -n +2 completed_annotations_rater_01.csv >> completed_annotations_all.csv
tail -n +2 completed_annotations_rater_02.csv >> completed_annotations_all.csv
tail -n +2 completed_annotations_rater_03.csv >> completed_annotations_all.csv
```

确保每个评审员使用不同的 `rater_id`。

## 7. 汇总 human-vs-LLM 对齐

```bash
python3 -m benchmark.human_alignment.summarize_annotations \
  --annotations benchmark/data/bench_pipeline/human_alignment_pairwise/completed_annotations_all.csv \
  --key benchmark/data/bench_pipeline/human_alignment_pairwise/annotation_key.json \
  --tie-threshold 0.25
```

`--tie-threshold 0.25` 表示从 Step 3 LLM 分数诱导偏好时，如果 A/B 分差绝对值小于等于 `0.25`，就记为 `tie`。

输出：

```text
human_alignment_summary.json
human_alignment_summary.md
```

## 8. 现场 LLM judge 并画 human-vs-LLM preference 图

如果已经有 `completed_annotations_all.csv`，可以直接从 CSV、private key 和 public package 生成 summary 与 SVG 图。
这条命令会现场调用 Claude Sonnet 4.6 重新做 A/B preference judge，不读取 Step 3 eval 里的 LLM preference：

```bash
python3 -m benchmark.human_alignment.plot_alignment \
  --annotations benchmark/data/bench_pipeline/human_alignment_pairwise/completed_annotations_all.csv \
  --key benchmark/data/bench_pipeline/human_alignment_pairwise/annotation_key.json \
  --output benchmark/data/bench_pipeline/human_alignment_pairwise/human_alignment_preference_alignment.svg
```

默认参数：

```text
--llm-source live
--judge-model anthropic/claude-sonnet-4.6
--judge-concurrency 8
```

API key、base URL 和 provider binding 默认走项目现有 LLM 基建配置，例如 OpenRouter：

```bash
export LLM_API_KEY=...
export LLM_HOST=https://openrouter.ai/api/v1
export LLM_BINDING=openrouter
```

如果你的环境已经在 `.env` / `DeepTutor.env` 里配好了这些变量，就不需要在命令里重复传。

如果要明确指定 package、模型或并发：

```bash
python3 -m benchmark.human_alignment.plot_alignment \
  --annotations benchmark/data/bench_pipeline/human_alignment_pairwise/completed_annotations_all.csv \
  --key benchmark/data/bench_pipeline/human_alignment_pairwise/annotation_key.json \
  --package benchmark/data/bench_pipeline/human_alignment_pairwise/annotation_package.jsonl \
  --judge-model anthropic/claude-sonnet-4.6 \
  --judge-concurrency 8 \
  --output benchmark/data/bench_pipeline/human_alignment_pairwise/human_alignment_preference_alignment.svg
```

现场 LLM judge 只会评 `completed_annotations_all.csv` 中已经有人类标注的 pair。空白 template 行不会触发 LLM 调用。
每个 pair 会按 10 个 metric 分开调用 LLM，即一次调用只判断一个 metric，再聚合成最终的 per-metric preference。

会额外输出：

```text
live_llm_judgments.json
```

其中保存 Claude 对每个 pair/metric 的 A/B/tie 判断和简短理由。

如果已经先跑过现场 judge + summary，也可以直接从 `human_alignment_summary.json` 画图，不再调用 LLM：

```bash
python3 -m benchmark.human_alignment.plot_alignment \
  --summary benchmark/data/bench_pipeline/human_alignment_pairwise/human_alignment_summary.json \
  --output benchmark/data/bench_pipeline/human_alignment_pairwise/human_alignment_preference_alignment.svg
```

如果想回到旧逻辑，从 Step 3 eval 分数诱导 LLM preference：

```bash
python3 -m benchmark.human_alignment.plot_alignment \
  --annotations benchmark/data/bench_pipeline/human_alignment_pairwise/completed_annotations_all.csv \
  --key benchmark/data/bench_pipeline/human_alignment_pairwise/annotation_key.json \
  --llm-source eval \
  --tie-threshold 0.25 \
  --output benchmark/data/bench_pipeline/human_alignment_pairwise/human_alignment_preference_alignment.svg
```

图中：

```text
H = human majority preference
L = LLM-judge preference
绿色 = DeepTutor preferred
灰色 = tie
红色 = Mock preferred
```

柱子里的数字表示 DeepTutor preference rate。即使 CSV 只标注了一部分 pair，也可以先画 pilot 图；图里的比例只基于已完成标注的 pair。

## 9. 汇总结果怎么看

每个 metric 会报告：

| 字段 | 含义 |
| --- | --- |
| `human_target_preference_rate` | 人类多数偏好 DeepTutor 的比例 |
| `llm_target_preference_rate` | LLM-judge 偏好 DeepTutor 的比例 |
| `agreement_rate` | 人类多数偏好与 LLM 偏好一致率 |
| `cohen_kappa` | 三分类偏好的一致性 κ |
| `human_tie_rate` | 人类多数判断为 tie 的比例 |
| `llm_tie_rate` | LLM 判断为 tie 的比例 |

这里的三分类标签是：

```text
target    = deep_tutor
baseline  = mock
tie
```
