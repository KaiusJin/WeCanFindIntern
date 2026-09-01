# WeCanFindIntern 技术总文档

本文档是当前代码的系统级说明，描述运行形态、数据边界、功能链路、并发控制、重试/断点恢复、回退策略和验证方式。模块细节见 [`docs/modules/`](modules/README.md)，故障处理见 [可靠性与恢复](RELIABILITY_AND_RECOVERY.md)。

## 1. 产品和架构边界

WeCanFindIntern 是一个本地优先的求职与申请工作台，包含：

- JobSpy 多来源公共职位采集、规范化、分类、去重和搜索；
- WaterlooWorks 专用 Chrome 登录态下的本地采集；
- Profile、简历导入、ATS 诊断、求职信、模拟面试和 Application Tracker；
- 经过审批保护的 AI Agent、推荐检索和记忆；
- 浏览器开发运行方式，以及不依赖 Docker/独立服务器的 Electron 桌面运行方式。

系统不把来源页面当作稳定数据库，也不让供应商 DataFrame、原始职位 JSON 或浏览器凭据越过各自边界。

## 2. 仓库地图

```text
WeCanFIndIntern/
├── src/wecanfindintern/
│   ├── api/                 FastAPI 应用、模型和路由组
│   ├── agent/               Agent、工具、推荐、记忆和持久化
│   ├── ats/                 确定性 ATS 诊断和匹配
│   ├── cover_letter/        求职信生成和 DOCX/PDF 导出
│   ├── db/                  PostgreSQL 连接池、读仓库和写仓库
│   ├── desktop/             桌面路径、sidecar、迁移、安全和后台采集
│   ├── domain/              职位、地点、薪资、分类和招聘术语
│   ├── ingestion/           JobSpy 边界、目录、流水线和 enrichment
│   ├── interview/           模拟面试、STT/TTS、分析和历史
│   ├── llm/                 Provider gateway、JSON 解析和缓存
│   ├── profile/             Profile、简历解析和安全校验
│   ├── tracker/             Application 模型和仓库
│   └── waterlooworks/       Chrome、采集器、提取器、SQLite 和状态
├── desktop/                 Electron main/preload、PostgreSQL、备份和打包
├── web/                     静态 HTML/CSS/native ES modules
├── migrations/              有序 PostgreSQL schema migrations
├── schemas/                 版本化公共 JSON Schema
├── scripts/                 采集、维护、桌面构建和校验命令
├── config/                  采集目录、launchd 和任务配置
├── vendor/JobSpy/           审计过的本地 JobSpy 依赖
└── tests/                   单元、路由、仓库、集成和内存测试
```

## 3. 两种运行形态

### 3.1 浏览器/开发运行

```text
Browser
  └─ HTTP ─> FastAPI/Uvicorn
              ├─ PostgreSQL 16 async pool
              ├─ WaterlooWorksService ─> dedicated Chrome + local SQLite
              └─ static web/
```

开发运行由 `.env` 提供 `DATABASE_URL`，PostgreSQL 通常由 Docker Compose 提供。FastAPI lifespan 打开连接池、初始化服务和后台推荐索引维护；关闭时停止后台任务并关闭连接。

### 3.2 Electron 桌面运行

```text
Electron main process
  ├─ sandboxed BrowserWindow + restricted preload
  ├─ OS secure storage, tray, backup/restore, single-instance lock
  ├─ embedded PostgreSQL 16 on random loopback port
  └─ packaged Python/FastAPI sidecar on random loopback port
       ├─ ordered migrations before listen
       ├─ static web + token-protected API
       ├─ resident four-hour public collection
       └─ recommendation-index maintenance
```

Electron 只暴露白名单 IPC；renderer 没有 Node.js 权限。sidecar 只监听 `127.0.0.1`/`::1`，所有 `/health`、`/api/` 和 `/desktop/` 请求要求每次启动生成的至少 32 字符 token。AI key 通过 Electron `safeStorage` 加密，浏览器开发模式才使用 localStorage 作为 fallback。桌面目录、构建、升级和备份见 [Desktop Scheme C](DESKTOP_SCHEME_C.md)。

## 4. 数据所有权和核心边界

| 数据 | 权威存储 | 是否进入公共 API | 关键隔离 |
|---|---|---:|---|
| 公共职位当前规范化记录 | PostgreSQL `jobs` | 是 | 原始来源不随列表返回 |
| 来源身份/链接 | PostgreSQL `job_sources` | 详情中以链接返回 | source fingerprint 唯一 |
| 原始抓取快照 | PostgreSQL `raw_job_snapshots` | 否 | 分区、哈希去重和保留策略 |
| Profile/简历导入 | PostgreSQL profile tables | 结构化结果 | 简历先 draft，Apply 才修改当前 profile |
| Tracker | PostgreSQL tracker tables | 是 | WaterlooWorks 使用 external Job ID |
| WaterlooWorks posting | 用户目录下 SQLite | 是（去掉 raw） | 不进入公共 `jobs` 或跨源 dedupe |
| Chrome SSO/MFA/cookies | 专用 Chrome profile | 否 | 不写入 PostgreSQL/SQLite |
| Agent/记忆/审批/audit | PostgreSQL Agent tables | 按 API 返回摘要 | 写操作需要审批 |
| LLM cache | PostgreSQL `llm_cache` | 否 | key 为 provider/model/prompt hash |

## 5. 公共职位端到端链路

```text
collection_plans.json
  → expand_collection_catalog
  → bounded concurrent JobSpy site/page queries
  → stable DataFrame columns / NormalizedJob
  → country scope + per-query fingerprint filtering
  → cross-query best-record selection
  → CanonicalJobInput
  → PostgreSQL batch ingest + advisory-lock dedupe
  → salary enrichment: source → regex → DeepSeek
  → recruiting term: cache → regex → DeepSeek
  → recommendation lexical/index queue
  → GET /api/v1/jobs + cursor UI
```

### 5.1 JobSpy 边界

`JobSpyQuery` 在调用上游前校验 source、结果数、分页 offset 和 provider 特有的互斥过滤器。`stabilize_jobspy_frame()` 即使遇到零行/零列 DataFrame 也补齐稳定列序；`clean_scalar()` 清理 NaN、日期和 pandas 标量。`scrape_checked()` 区分“正常空页”和“上游记录 ERROR 但返回空表”：前者是成功，后者转成可重试异常。

### 5.2 规范化、分类和去重

规范化保留原始文本，同时生成 company/location/work mode、salary interval、classification、skill/requirement tags、description hash、URL hash 和 dedupe block。未知值保留为 unknown/NULL，不猜测国家、城市、薪资或工作方式。

去重分两层：

1. 同一来源由 `source_fingerprint` 和唯一约束保证幂等；
2. 不同来源先按 direct URL/hash、company/location block 和日期窗口生成有限候选，再由标题、地点、工作方式、日期和描述 shingles 比较。

去重决策写入 `dedupe_candidates`/`dedupe_decisions`，包含命中规则、分数和算法版本。自动 match 合并 source edge；不匹配则创建独立 canonical job。WaterlooWorks 不参与此流程。

## 6. 并发模型

| 场景 | 并发策略 | 保护对象 | 失败影响 |
|---|---|---|---|
| 公共采集查询 | `asyncio.Semaphore`，默认 4，CLI 限制 1–16；阻塞 JobSpy 放线程 | 上游压力和事件循环 | 单 query 失败，其他 query 继续 |
| 公共采集进程 | Unix `fcntl`/Windows `msvcrt` 非阻塞文件锁 | 手动、launchd、Task Scheduler、桌面重复运行 | 新运行跳过 |
| PostgreSQL | async pool 默认 2–20；statement timeout 默认 5s | 连接数和慢 SQL | 当前请求/批次失败，事务回滚 |
| ingestion dedupe | fingerprint/block 内 advisory transaction lock | 同源重复和同 block 竞争 | 只串行相关候选，不阻塞无关 block |
| 薪资 DeepSeek | semaphore=5；先完整 regex，再发模型请求 | provider rate/成本 | 单条失败不删除既有薪资 |
| recruiting term | regex 全批先完成；batch 模式按 batch 顺序，`batch_size=0` 时并发 5 | provider rate/批处理 | 单条 generation 可失败，其他项继续 |
| WaterlooWorks | collector 按 board 顺序，posting 写入放线程；service lock 防重复任务 | Chrome 页面/SQLite 写入 | 单 board/单 posting 隔离，run 可 partial |
| Recommendation index | queue 分页 drain；失败项递增 attempts | poison row 阻塞全队列 | 失败项保留，其他项继续 |
| Agent | 每个 HTTP turn 独立；单 turn 最多 4 个 plan round；approval pending 时串行决策 | 重复工具调用和未确认写入 | 当前 turn 安全降级 |

数据库连接池控制并不等于全局请求限流；如果部署多个 API worker，每个 worker 都会有自己的 pool，生产环境需按数据库容量设置 worker/pool 上限。

## 7. 断点、重试、幂等和回退

### 7.1 公共采集

当前没有把“每个 source/page offset”持久化成 checkpoint。一个 query 的 offset 和已见 fingerprint 只在本次进程内存中存在；进程中断后，下一次运行从 query 起点重新抓取。这是有意设计：数据库 ingest 的 source fingerprint、唯一约束、dedupe block lock 和 changed-payload hash 使重跑安全，避免保存一个可能已经过期的网页 offset。

因此恢复规则是：

- 网络调用失败：同一页最多额外重试 `max_retries` 次，指数退避 `1.5s * 2^n + jitter`，最多 15s；
- 某 query 重试耗尽：记录 source/query failure，其他 query 和已收集结果继续；
- 进程在持久化期间中断：已提交 batch 保留，未提交 batch 回滚；下次全量重跑不会重复 source row；
- enrichment 中断：结构化/已成功写入结果保留，剩余候选下次由输入 hash/缺失条件重新处理；
- 正常空页：结束该 query，不触发重试；带 JobSpy ERROR 的空页：按上游失败重试。

### 7.2 推荐索引

公共职位更新由 trigger 写入 `recommendation_index_queue`。`index_pending()` 只处理 `attempts < 5` 的项；失败项递增 attempts 并保留 `last_error`，不会阻塞其他项。职位更新会把该项 attempts 重置为 0。没有 embedding provider 时，词法文档仍由 API 维护，向量阶段跳过；embedding 失败不会删除词法文档。

### 7.3 LLM 功能

共享 gateway 对 provider transport failure 做有限指数重试；JSON 解析、schema/业务校验失败不重试，以免重复付费。命中内容寻址 cache 时不调用 provider。求职信使用最多 5 轮 Writer/Reviewer；耗尽后返回最后一个非空草稿并标记未通过。ATS 确定性分数、Profile draft、Tracker 当前值不依赖模型回退。

### 7.4 WaterlooWorks

WaterlooWorks 没有网页分页 checkpoint。run 记录 board 状态，posting 按 source Job ID insert-once；重新运行会把已有 posting 计为 known，只更新 `last_seen_at`，因此浏览器关闭、单 board 失败或进程重启后直接重新运行即可恢复。应用状态同步同样按 source Job ID 幂等，已存在的用户 stage 不被外部状态覆盖。

### 7.5 桌面数据库恢复

Electron 启动顺序为验证内置 PostgreSQL → 初始化/启动随机 loopback 端口 → preflight backup/restore → 运行带 checksum 的 migrations → 启动 FastAPI。restore 前一定生成 safety backup；restore 失败自动回退到 safety backup。PostgreSQL major version 不可直接指向旧 data directory，必须走兼容 backup/restore 或 `pg_upgrade`；WaterlooWorks SQLite、Chrome profile、model cache 和加密 secrets 不随 PostgreSQL restore 改写。

## 8. API 和客户端处理情形

FastAPI 在静态文件 fallback 前注册 API routes。主要前缀：

```text
/health
/api/v1/jobs
/api/v1/ats
/api/v1/interview
/api/v1/cover-letter
/api/v1/tracker
/api/v1/profile
/api/v1/waterlooworks
/api/v1/agent
/desktop/status
/desktop/collection/run
```

处理原则：

- 参数、UUID、日期、薪资、cursor 不合法：422，不进入仓库；
- 资源不存在：404，不伪造空对象；
- 重复 bookmark/approval/同步：使用唯一约束或 pending 条件返回幂等结果/冲突；
- 数据库/外部 provider 失败：返回可读 error，内部细节进入日志；
- 列表使用 keyset cursor，详情单独加载 source links，避免深 OFFSET 和大 raw payload；
- 客户端为 filter 变化重置 cursor，为异步状态轮询提供 loading/error/partial 状态。

公共职位 schema 为 `job.v3`、`job-detail.v4`、`job-page.v3`、`job-facets.v2`；WaterlooWorks detail/list 使用另一套 external ID 语义。

## 9. 功能模块协作

### Profile、ATS 和求职材料

简历上传先检查扩展名/MIME、magic bytes、大小、页数、结构、active content、文本量和英文启发式；LaTeX 只解析文本，绝不编译。解析结果写 `resume_documents` 和 `profile_imports` draft；用户确认后在一个事务中应用到当前 Profile。ATS readiness/match 是证据化确定性计算；ATS commentary、求职信、面试分析才进入 LLM gateway。

### Tracker

Tracker 把用户工作流 stage 与 WaterlooWorks 的外部 status 分开。Bookmark 是幂等的；stage/field 修改和 event 写入在同一事务边界。批量操作预校验 ID 并返回逐项结果；来源职位消失时保留 Tracker snapshot。

### WaterlooWorks

专用 Chrome 处理 SSO/MFA 和页面 JS，SQLite 保存本地 posting/run/board/application 观察。collector 按五个 board 独立执行，某 board 或 posting 失败不会抹掉其他 board 已导入数据。

### AI Agent 和推荐

Agent 读取可立即执行；写操作先保存精确参数和 preview 到 approval，用户批准后使用原始参数执行一次。推荐先 hard filter，再 lexical/vector retrieval、RRF 和确定性评分；可选 LLM review 只能在 top 15 内提出有限调整，不能添加候选或取代主分数。

## 10. 安全模型

- 桌面 renderer 使用 context isolation、sandbox 和白名单 preload；sidecar loopback 请求需要 token；
- AI keys 在桌面使用 OS secure store，provider key 不写 profile/Agent 表；
- Chrome 凭据、cookies、MFA 只存在专用 profile；
- raw provider payload 不进入公共 API；
- 简历上传不会执行 LaTeX 或解包任意文件；
- Agent 不执行任意 SQL，写操作必须显式确认并记录 audit；
- LLM prompt 将职位描述/简历作为带分隔符的参考数据处理，防止来源文本注入工具指令；
- 日志和 backup 目录在桌面模式下使用用户目录，Unix 权限分别收紧到 0700/0600。

## 11. 迁移、升级和变更流程

`migrate.py` 按文件名顺序执行 migrations，在 `schema_migrations` 保存文件名和 checksum；已应用 migration 被修改时拒绝静默继续。派生字段/分类版本变化要提供 backfill 命令；公共响应变化同时更新 Pydantic model、`schemas/`、frontend consumer 和 contract tests。

推荐变更顺序：

1. 先定义数据所有权和失败语义；
2. 修改 domain model/数据库 migration/repository；
3. 修改 API model/route 和公共 schema；
4. 修改前端状态、loading/error/partial 处理；
5. 增加正常、空结果、重复、并发、失败、重试和恢复测试；
6. 运行完整验证并检查文档链接。

## 12. 验证入口

```bash
PYTHONPATH=src .venv/bin/python -m pytest
make check
git diff --check
```

`make check` 包含 Ruff、pytest、frontend/API contract verifier 和所有 ES module 的 Node syntax check。桌面构建还需要按平台执行 `docs/DESKTOP_SCHEME_C.md` 的 PostgreSQL、backend 和 Electron make 流程；桌面发布 workflow 只构建 artifact，不自动发布服务器或 app store。

## 13. 权威模块文档

- [可靠性与恢复](RELIABILITY_AND_RECOVERY.md)
- [Job ingestion](modules/job-ingestion.md)
- [Domain normalization](modules/domain-normalization.md)
- [Database and Data API](modules/database-and-data-api.md)
- [WaterlooWorks](modules/waterlooworks.md)
- [Profile](modules/profile.md)
- [Tracker](modules/tracker.md)
- [AI Agent and memory](modules/ai-agent.md)
- [LLM-assisted tools](modules/llm-assisted-tools.md)
- [Frontend](modules/frontend.md)
- [Operations and verification](modules/operations.md)
- [Desktop Scheme C](DESKTOP_SCHEME_C.md)
