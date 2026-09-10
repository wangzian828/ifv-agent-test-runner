# IFV Agent Test Runner

独立的 Image Factual Verifier 测试集 Agent 运行仓库。它只负责测试集 rollout、工程重试、raw-history 轨迹保存和机械结果导出，不包含 SFT、RL、教师训练轨迹或 private-gold judge。

## 给接手 Codex 的任务

你的任务只有一项：在目标服务器上为 `GPT-5.5` 和 `Qwen3.5-397B-A17B` 配置实际可用的模型 API，然后使用同一份完整测试集分别跑完两个模型的 Agent 轨迹。

必须完成：

1. 阅读本 README 和 `AGENTS.md`。
2. 根据目标服务器实际服务确定两个模型各自的 Base URL、API key 环境变量、wire format 和真实 model ID。
3. 验证模型支持图片输入、原生工具调用、长上下文和多轮 tool response。
4. 配置 Serper、Jina、百度 OCR 和 OSS；禁止本地 OCR。
5. 先构建 5 条 smoke release，分别真实运行两个模型。
6. 只有两个模型的 smoke 都达到 5/5 terminal success、工具链正常且轨迹可读，才允许构建和启动完整测试 release。
7. 从目标服务器已有数据集构建一次完整测试 release，预计约 1,684 条，以 manifest 实际行数为准。
8. 分别运行 `GPT-5.5` 和 `Qwen3.5-397B-A17B`，不得共用输出目录。
9. 持续监控至全部工程重试结束，不能只启动进程后停止任务。
10. 发现工程错误时必须使用同一输出目录继续重跑；入口只补失败 case，不重跑已成功 case。
11. `engineering_error_count` 不为零时任务不得宣告完成。必须继续重跑，或明确记录多轮重试后仍无法解决的外部阻塞。
12. 交付两个模型的完整结果目录和汇总，不运行 private-gold judge、SFT 或 RL。

不得修改或依赖原项目服务器的绝对代码路径。本仓库可克隆到任意目录；数据集、release 和输出目录全部通过命令参数传入。

## 目标模型

需要分别运行：

- `GPT-5.5`
- `Qwen3.5-397B-A17B`

两个模型使用同一测试 release，但必须使用不同输出目录。具体 endpoint、API key、wire format 和服务端 model ID 由目标服务器上的 Codex 根据服务实际配置完成，不得猜测。

## 必需 API

- 主模型 API：同时承担主 Agent、视觉工具和网页 Evidence 抽取。
- Serper：文本搜索、图片搜索和 Lens。
- Jina：页面抓取与搜索结果 rerank。
- 百度 OCR：唯一 OCR 后端，禁止 EasyOCR 和任何本地 fallback。
- OSS：向 Lens 提供临时签名图片 URL。

复制 `.env.example` 到服务器未跟踪的 env 文件，填入凭据。不要把 key 写入 Git、日志、轨迹或结果包。

## 安装

```bash
git clone https://github.com/wangzian828/ifv-agent-test-runner.git
cd ifv-agent-test-runner
python -m pip install -e .
```

## 真实 Agent Smoke

全量前必须先从同一数据集生成 5 条 smoke release：

```bash
python scripts/prepare_agent_test_release.py \
  --dataset-root /path/to/full-test-dataset \
  --test-manifest /path/to/full-test-dataset/test-manifest.jsonl \
  --private-gold-sidecar /path/to/full-test-dataset/evaluator_private/private-gold-v1/private-gold.jsonl \
  --output-dir /path/to/runtime-releases/smoke-5 \
  --limit 5
```

分别真实运行两个模型：

```bash
scripts/run_agent_test.sh \
  --benchmark /path/to/runtime-releases/smoke-5/runtime-release/runtime_input/cases.jsonl \
  --output-dir /path/to/results/smoke-gpt55
```

```bash
scripts/run_agent_test.sh \
  --benchmark /path/to/runtime-releases/smoke-5/runtime-release/runtime_input/cases.jsonl \
  --output-dir /path/to/results/smoke-qwen35-397b-a17b
```

全量启动门槛：

- 两个模型均为 `target_case_count=5`；
- 两个模型均为 `successful_case_count=5`；
- `engineering_error_count=0`；
- 主 Agent、图片输入、工具调用、tool response、Serper、Jina、百度 OCR 和 OSS 均实际可用；
- merged trace 中保留完整 raw history，最终 verdict 为有效 `real/fake`。

任一模型 smoke 未通过时，先修复配置或代码并重新跑 smoke，禁止直接启动全量。

## 准备完整测试集

使用原始完整测试集，当前预期为约 1,684 条。不要使用后续筛选后的 1,527 条版本。

数据集目录由目标服务器自行指定，目录内需要存在：

```bash
<dataset-root>/test-manifest.jsonl
<dataset-root>/images/
<dataset-root>/evaluator_private/private-gold-v1/private-gold.jsonl
```

构建完整、仅供评测的 runtime release：

```bash
scripts/prepare_full_test_release.sh \
  /path/to/full-test-dataset \
  /path/to/runtime-releases/full-test
```

脚本自动读取 manifest 的实际非空行数并导出全部 case，不硬编码服务器目录或样本数量。构建后确认：

- `release_stage=development_subset`
- `training_prohibited=true`
- `private_keys_absent=true`
- `preparation.json` 的 `case_count` 与原始 `test-manifest.jsonl` 一致

Agent 可见输入为：

```text
/path/to/runtime-releases/full-test/runtime-release/runtime_input/cases.jsonl
```

## 运行

加载服务器 env 后，对两个模型各执行一次：

```bash
scripts/run_agent_test.sh \
  --benchmark /path/to/runtime-releases/full-test/runtime-release/runtime_input/cases.jsonl \
  --output-dir /path/to/results/agent-test-gpt55
```

```bash
scripts/run_agent_test.sh \
  --benchmark /path/to/runtime-releases/full-test/runtime-release/runtime_input/cases.jsonl \
  --output-dir /path/to/results/agent-test-qwen35-397b-a17b
```

启动前检查同一模型是否已有进程，禁止重复启动。所有服务器进程必须设置 `OMP_NUM_THREADS=1`。

## 工程错误重跑

入口默认每次执行最多进行 4 轮工程尝试。全量结束后检查：

```bash
cat /path/to/results/agent-test-<model>/summary.json
```

如果 `engineering_error_count > 0`，使用完全相同的 benchmark、模型配置和输出目录再次执行原命令：

```bash
scripts/run_agent_test.sh \
  --benchmark /path/to/runtime-releases/full-test/runtime-release/runtime_input/cases.jsonl \
  --output-dir /path/to/results/agent-test-<model>
```

恢复运行会读取已有 attempts，只把没有 terminal success 的 case 加入新一轮，不会重跑已经成功的 case。不得删除旧 attempt、手工改写 trace，或新建另一个目录后丢失失败链路。

只有 `engineering_error_count=0` 才视为完整跑通。如果多轮重跑后仍因 provider 内容拦截、持续网络不可用或权限问题无法完成，必须保留所有 attempt，并在最终交付中逐条列出未解决 case 和最后错误；不能把它们计为模型二分类结果。

## 输出

每个模型至少产生：

```text
summary.json
agent-results.jsonl
run-config.json
target-case-list.txt
rollouts/
```

每个 case 必须有 terminal success 或明确工程失败。工程失败不能伪装为 `real/fake`。所有 attempt 和原始工具观察都必须保留。

本仓库不读取 private gold 进行评估。二分类准确率和证据质量 judge 在结果回收后由其他流程单独完成。

## 最终交付说明

接手 Codex 最终回复必须列出：

- 仓库 commit；
- 两个模型的实际服务端 model ID；
- endpoint 地址，不包含 key；
- 原始测试 manifest 行数；
- runtime release 路径；
- 两个模型各自的输出目录；
- target、success、engineering error 数量；
- attempt 数量和是否存在未解决 case；
- `summary.json`、`agent-results.jsonl` 和 merged traces 的路径；
- 是否确认无 private-gold 泄漏、无本地 OCR、无遗留运行进程。

API key、密码、private gold、完整 env 文件和测试图片不得提交到 GitHub。
