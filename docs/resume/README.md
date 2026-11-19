# WeCanFindIntern 简历材料与证据

本目录基于 2026-09-05 工作区代码、项目文档及本地采集摘要整理。三个 `.tex` 文件是项目条目，放入用户已有的 Projects 列表，不重复添加 documentclass 或列表外壳。日期 Aug 2026 来自最早 Git 提交（2026-08-25），应按实际贡献时间调整。Built/Implemented 等表述应对应本人实际负责的内容。

## 项目介绍

WeCanFindIntern 是面向 internship、co-op 和 early-career 求职的本地优先工作台，整合公开职位、WaterlooWorks、候选人档案、简历诊断、求职信、模拟面试、申请追踪以及可调用业务工具的 AI 助手。浏览器界面与 Electron 桌面应用复用 FastAPI 接口，公共数据及业务状态主要使用 PostgreSQL，WaterlooWorks 原始本地记录使用独立 SQLite 存储。

## 模块

| 模块 | 已实现功能 |
| --- | --- |
| 职位采集与标准化 | 集成 JobSpy 的 Indeed/LinkedIn 采集，异步并发、重试与来源故障隔离；标准化地域、薪资、类别和招聘学期，跨来源去重、内容哈希及详情缓存 |
| 职位检索与展示 | PostgreSQL 全文检索、筛选、游标分页，前端无限滚动、地图、详情和收藏 |
| WaterlooWorks | 5 个版块同步；独立 Chrome 登录环境保留 SSO/MFA；本地 SQLite，已投申请状态同步 |
| Profile | PDF/LaTeX 导入、结构化档案、可编辑草稿，为推荐和求职工具提供统一背景 |
| ATS 诊断 | 简历解析就绪度与职位匹配两个独立的确定性评分；证据、缺口及不可用维度；可选 AI 解释不修改评分 |
| 求职信 | Writer/Reviewer 最多 5 轮生成与事实检查，DOCX/PDF 导出，未通过审核的结果明确标记 |
| 模拟面试 | 职位相关问题、文字/录音回答、faster-whisper 本地转录、TTS、结构化反馈与历史趋势 |
| 申请追踪 | 公共/WaterlooWorks/自定义职位、5 个阶段、时间线、批量操作及 CSV 导出 |
| AI Agent | 13 个 schema 定义的工具、有限轮次规划、工具执行记录、破坏性操作审批、混合推荐与分层记忆 |
| 桌面与工程交付 | Electron 的 macOS/Windows 打包配置，Python/PostgreSQL 运行时、系统安全存储、后台采集、GitHub Actions 检查 |

## 岗位侧重点

- Backend：数据管道、事务与幂等、advisory locks、内容哈希、数据库查询和缓存。可替换现有 Backend 简历里的 CausalEdge，保留 Degree Explorer 展示不同后端栈。
- Fullstack：跨模块共享数据、列表/地图/筛选、REST/SSE、用户操作反馈、桌面打包。可替换 CausalEdge；与已有 React 项目形成互补。本项目不能写成 React/Next.js/TypeScript 项目。
- AI Agent：工具 schema、规划执行边界、审批审计、混合检索、可解释评分、记忆与多 provider。可放在 Projects 首位，与 NoteFlow 配合展示业务工具执行和文档 RAG 两种场景；篇幅不足时替换 Degree Explorer。本项目使用自定义 Python 编排，不能写成 LangGraph/LangChain 实现，也不能把 Writer/Reviewer 称为分布式多 Agent 系统。

## 数字口径

来源：`logs/campaign_summary_latest.json`，completed_at 为 `2026-09-05T21:57:46.481559+00:00`。这是已有运行记录，本次未重新执行网络采集。摘要是 latest 文件，后续采集会覆盖；以下保留本次引用的数值。

| 数字 | 正确含义与限制 |
| --- | --- |
| 1,264 | 单次运行按来源指纹去重的采集记录；不能称为 1,264 个新增职位或跨平台唯一职位 |
| 76 | 当次完成的查询任务，不是 76 个网站 |
| 672.65 秒 / 11.21 分钟 | 当次完整运行耗时，不是基准对照或吞吐上限 |
| 1,052 / 1,264 = 83.23% | 内容未变化的记录比例，结合代码可表述为绕过完整去重流程；仍刷新数据库时间字段，不能称为消除了所有写入或性能提升 83% |
| 345 / (345 + 412) = 45.57% | 当次 LinkedIn 详情缓存命中率，该次详情失败数为 0；不能称为总请求量或总成本降低 46% |
| 151 / 24 / 37 | 当次 created / merged / updated 数量 |
| 13 | `agent/catalog.py` 的工具数量，静态 AST 计数核验 |
| 6 | `llm/providers.py` 支持的 LLM provider：Gemini、OpenAI、DeepSeek、GLM、Qwen、Ollama；不是 6 种 embedding provider |
| 5 | `waterlooworks/config.py` 配置的 WaterlooWorks 版块数量，不表示全部已成功采集 |
| top 15、±5 分 | 可选 LLM review 的候选数量上限与分数调整边界；不是效果提升 |

没有证据支持的数字不加入条目：用户数、录用率、推荐准确率、延迟改善百分比、云成本改善、长期可用性。后续如测量性能改进，需同一数据集/环境/并发下比较改动前后的多次运行，并保留基线。

## 代码核对位置

- `src/wecanfindintern/db/repositories/jobs.py`：批量内容哈希检测、unchanged 快速刷新、事务和 advisory locks。
- `scripts/collection/run_collection_campaign.py`：并发采集、LinkedIn 详情缓存及运行统计。
- `src/wecanfindintern/db/read_repository.py`：搜索和分页。
- `src/wecanfindintern/agent/catalog.py`、`orchestrator.py`：工具列表、审批及执行循环。
- `src/wecanfindintern/agent/recommend/repository.py`、`indexer.py`、`rerank.py`：全文/向量检索、HNSW、RRF 和受限 LLM review。
- `src/wecanfindintern/agent/memory/`：窗口、摘要、记忆提取及召回。8,000 token 是最近对话窗口的默认估算预算，不是整个模型上下文上限。
- `docs/modules/frontend.md`、`web/modules/`、`desktop/package.json`：原生 JavaScript 和 Electron。

## 本次检查

- 三个 LaTeX 条目均使用用户提供的宏定义独立运行 pdflatex，成功，无 Overfull 报告；未替换整份简历，因此不代表完整简历仍为一页。
- Python 非数据库测试：315 passed、1 failed、7 deselected。失败为缓存测试使用 2026-09-02 固定时间戳，当前运行时已超出 24 小时 TTL；没有冻结测试时间。
- 前端测试：34 passed、4 failed，涉及 Agent 卡片、附件弹窗和布局/样式版本断言。
- 未运行数据库集成测试或验证 macOS/Windows 发布包。本次没有修改应用实现或修复测试，也不将通过率写入简历。
