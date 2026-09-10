# IFV Agent Test Runner

独立的 Image Factual Verifier 测试集 Agent 运行仓库。它只负责测试集 rollout、工程重试、raw-history 轨迹保存和机械结果导出，不包含 SFT、RL、教师训练轨迹或 private-gold judge。

## 给接手 Codex 的任务

你的任务只有一项：在目标服务器上为 `GPT-5.5` 和 `Qwen3.5-397B-A17B` 配置实际可用的模型 API，然后使用同一份完整测试集分别跑完两个模型的 Agent 轨迹。

必须完成：

1. 阅读本 README 和 `AGENTS.md`。
2. 根据目标服务器实际服务确定两个模型各自的 Base URL、API key 环境变量、wire format 和真实 model ID。
3. 验证模型支持图片输入、原生工具调用、长上下文和多轮 tool response。
4. 配置 Serper、Jina、百度 OCR 和 OSS；禁止本地 OCR。
5. 从目标服务器已有数据集构建一次完整测试 release，预计约 1,684 条，以 manifest 实际行数为准。
6. 分别运行 `GPT-5.5` 和 `Qwen3.5-397B-A17B`，不得共用输出目录。
7. 持续监控至全部工程重试结束，不能只启动进程后停止任务。
8. 核对每个 case 都有 terminal success 或明确工程失败。
9. 交付两个模型的完整结果目录和汇总，不运行 private-gold judge、SFT 或 RL。

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
