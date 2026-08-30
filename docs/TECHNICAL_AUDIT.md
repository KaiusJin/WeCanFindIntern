# 项目技术审计报告

> 审计范围：全仓库（src / web / migrations / scripts / tests / CI / 部署配置）
> 审计基准提交：`cb75e27`（main，已推送）
> 测试基线：203 个单元测试 + 3 个数据库集成测试全部通过
>
> **修复状态（更新）**：§4.1、§4.2、§4.3、§4.4 已修复落地；§4.5 经核实为方法级
> 原子安全（跨方法序列确认为设计决策，见 4.5 备注）。

---

## 1. 文件层级架构现状

```text
WeCanFindIntern/
├── .github/workflows/ci.yml        # CI：lint + test + contract + frontend-check
├── config/                          # 采集配置（collection plans）
├── docs/                            # 技术文档（模块文档 + 契约文档）
│   ├── modules/                     #   按模块拆分的技术文档
│   └── TECHNICAL_AUDIT.md           #   本报告
├── migrations/                      # 21 个编号 SQL 迁移 + schema_migrations 校验和
├── schemas/                         # JSON Schema
├── scripts/
│   ├── collection/ dev/ maintenance/
│   └── maintenance/                 # migrate + 5 个数据回填/修复脚本
├── src/wecanfindintern/
│   ├── agent/                       # AI Agent（编排/工具/记忆/推荐 RAG）
│   │   ├── memory/                  #   记忆子系统（窗口/摘要/抽取/召回/偏好）
│   │   └── recommend/               #   推荐流水线（召回/粗排/精排/嵌入/索引）
│   ├── api/
│   │   ├── app.py                   # create_app + jobs 系列路由内联（⚠ 见 4.2）
│   │   └── routes/                  # 7 个 router（agent/ats/cover_letter/interview/profile/tracker/waterlooworks）
│   ├── ats/                           # 确定性简历解析与岗位匹配评分
│   ├── cover_letter/ interview/       # LLM 求职工具（服务层）
│   ├── db/                          # 连接池 + 读仓库 + 仓库集合
│   ├── domain/                      # 纯领域逻辑（规范化/分类/地点/薪资）
│   ├── ingestion/                   # JobSpy 适配与入库管道
│   ├── llm/                         # 统一 LLM 网关（6 provider + 嵌入）
│   ├── profile/ tracker/ waterlooworks/  # 业务模块（各自 models+repository）
│   └── deduplication.py
├── tests/                           # 203 个测试（全 fake，无 DB 集成）
├── web/
│   ├── index.html (1,099 行)        # 单页 8+1 个 tab
│   ├── styles.css (4,565 行)        # 全站样式单文件
│   ├── modules/                     # 原生 ES Module（main 入口 + 按 tab 懒加载）
│   └── vendor/                      # d3 / topojson / 地图边界数据（共 ~1.5MB）
├── docker-compose.yml               # pgvector/pgvector:pg16
└── pyproject.toml                   # 依赖 + ruff + pytest 配置
```

### 层级健康度总评

| 区域 | 评价 |
|---|---|
| `src/` 分层（api → service → repository → domain） | ✅ 清晰且执行一致 |
| `domain/` 纯函数化（无 I/O） | ✅ 可测试性好 |
| `migrations/` 编号 + 校验和 runner | ✅ 规范 |
| `web/modules/` ES Module 懒加载 | ✅ 已优化（见提交 ff6f134） |
| `agent/tools.py`（1,621 行） | ⚠ 单文件过大，建议拆包（见 6.1） |
| `styles.css`（4,565 行）/ `index.html`（1,099 行） | ⚠ 单体，建议拆分（见 6.2） |
| `api/app.py` 内联 jobs 路由 vs `routes/` router | ⚠ 风格不一致（见 4.2） |

---

## 2. 数据库连接情况评估

### 2.1 PostgreSQL 连接池（`db/pool.py`）

```python
AsyncConnectionPool(
    min_size=2, max_size=20, open=False,
    timeout=10,
    kwargs={"row_factory": dict_row, "prepare_threshold": 5,
            "options": "-c statement_timeout=5000"},
)
await pool.open(wait=True, timeout=15)
```

**结论：配置健康。** 要点核实：

| 项 | 状态 | 说明 |
|---|---|---|
| 有界池（2–20） | ✅ | `DB_POOL_MIN/MAX_SIZE` 可经环境覆盖，且有非法值校验 |
| `statement_timeout=5000ms` | ✅ | 防止失控查询拖垮连接；LLM 长请求不查库故不受影响 |
| `prepare_threshold=5` | ✅ | 热查询走服务端预处理，收益明显 |
| `open=False` + 显式 `open(wait=True)` | ✅ | 避免构造期竞态，生命周期由 app 管理 |
| 行工厂统一 `dict_row` | ✅ | 仓库层返回 dict，风格一致 |

**改进建议（P2）**：补充 `max_lifetime`（psycopg 参数，如 1800s）避免长连接因后端重启/网络设备老化而腐化；建议增加启动时 `SELECT 1` 健康探针并在 `/health` 中暴露池状态（`pool.get_stats()`）。

### 2.2 连接使用模式

- 所有仓库通过 `async with self.pool.connection()` 借还连接，未发现手动 `connect()` 泄漏路径。
- `waterlooworks/repository.py` 使用独立 SQLite（`sqlite3.connect` 每次新建连接 + service 层 `asyncio.to_thread` 包装）——数据量小（数百行）可接受；若未来增长，建议换 `aiosqlite` 或常量连接 + WAL。
- **事务边界**：`migrate.py` 与回填脚本使用 `connection.commit()`；API 写路径依赖 psycopg 默认 autocommit=False + 连接归还时回滚语义。**注意**：`AgentRepository` 多语句操作（如会话+消息+审计）未显式包事务，中途失败会留下部分写入——单用户场景影响低，列为 P2（见 4.3）。

### 2.3 迁移与 Schema

- 21 个迁移全部有 `IF NOT EXISTS`/幂等保护，runner 记录校验和防止篡改重放。✅
- **缺口**：SQL 本身无自动化测试。本周期内 `geo-distribution`、推荐 RAG 等新 SQL 都是运行时才首次验证。建议建立集成测试基建（见 7.1）。

---

## 3. 代码编写情况评估

**优点**（核实过的模式）：

- 统一 LLM 网关：key 清洗、JSON 容错解析（`_find_last_json` 处理推理前缀块）、有界超时与重试、usage 记录全部单点实现。✅
- 写工具两阶段审批协议（plan/execute + 落库参数 + 原子翻转）在迭代循环改造后保持不变。✅
- 推荐流水线分层清晰（召回/粗排/精排/缓存），每层可独立降级。✅
- 无 `TODO/FIXME` 残留；ruff（E/F/I/UP/B/SIM）零告警；CI 四道门禁齐全。✅

**问题清单**见第 4 节。

---

## 4. 代码错误与风险清单（按优先级）

### P1 — 已全部修复

#### 4.1 ✅ 已修复 — `POST /api/v1/interview/analyze` 在 async 路由中同步阻塞事件循环

`api/routes/interview.py:161`：`analyze_answer` 是 `async def`，却直接调用同步的 `analyze_interview_performance`（内含本地 Whisper 转写 + 一次 30–180 秒的 LLM 调用）。**期间整个事件循环被阻塞——所有并发 API 请求（包括健康检查）全部冻结。**

同文件内 `create_interview_session`（line 33）已正确使用 `asyncio.to_thread`。修复只需一行：

```python
response = await asyncio.to_thread(
    analyze_interview_performance, job_description=..., ...
)
```

注意对比：`get_questions` 是 `def`（FastAPI 自动线程池，无问题）；ATS/cover_letter 路由也是 `def`（无问题）。**只有 analyze 一处**。同理复查 `tts` 路由（`def`，OK）。

#### 4.2 ✅ 已修复 — `api/app.py` 路由组织不一致

jobs 系列端点（6 个 `@app.get/post`）内联在 `create_app()` 里，而其余 7 个模块使用 `APIRouter`。影响：依赖注入写法分叉（`RepositoryDependency` vs `Depends`）、contract 测试定位困难。**已修复**：已抽出 `api/routes/jobs.py`（`jobs_router`，prefix `/api/v1/jobs`），`app.py` 现在只负责组装、/health 与中间件。

#### 4.3 ✅ 已修复 — SQL 无集成测试覆盖

203 个测试全部基于 fake 仓库。本轮开发中 `geo-distribution` 的 SQL 是上线运行时才首次验证（幸运地一次通过）；`list_jobs_for_recommendation`、`recall_public` 等复杂 SQL 同样裸奔。**已修复**：`tests/integration/` 提供真实 PostgreSQL 集成测试（`-m db` 标记、
自动应用全部迁移、每用例前 TRUNCATE、无库自动跳过），CI 增加 pgvector service
单独运行。覆盖：geo 聚合、推荐召回排除、地区筛选。

### P2 — 计划内修复

#### 4.4 ✅ 已修复 — Agent 记忆维护任务字典缓慢泄漏

`agent/memory/manager.py:58` `_maintenance_tasks: dict[UUID, Task]` 只增不删——任务完成后仍留在字典中。每个会话一个条目，长期运行（数周）会积累。已修复（`add_done_callback` 清理条目）。原方案：

```python
task = asyncio.create_task(...)
task.add_done_callback(lambda _: self._maintenance_tasks.pop(session_id, None))
```

#### 4.5 ✅ 已核实 — 非原子多语句写入（无需修复）

`AgentRepository` 的"turn 记录"（消息 + 工具调用 + 审计三条 insert）无显式事务包裹。连接归还时 psycopg 会回滚未提交更改，但代码路径上三次独立 `execute` 若中途抛错，前序写入不回滚（除非同一连接未归还）。**核实结论**：仓库内唯一的多 execute 方法 `append_audit` 是 if/else 分支而非顺序写，
且所有方法都在单个 `async with pool.connection()` 块内（psycopg 语义：成功一起提交、
异常一起回滚）——**方法级事务原子性成立**。跨方法的 turn 序列（消息+工具调用+审计）
分散在三个连接上是设计选择，单用户场景可接受；如需强一致再引入 Unit-of-Work。

#### 4.6 `recruiting_term_enrichment`/`salary_enrichment` 与主入库共享 `statement_timeout=5s`

批量回填脚本走独立同步连接（不受池限制），但 ingestion 管道内的 LLM 增强若触发慢 SQL 会被 5s 掐断。目前未观察到问题，列为观察项。

#### 4.7 配置解析手工实现

`config.py` 手写 `.env` 解析（`os.environ.setdefault`）——多个进程同时首次启动存在理论竞态；且布尔/路径类配置无类型校验。建议迁移 `pydantic-settings`（P2，收益是类型化和文档化）。

### 已核实无问题（此前列为疑点）

| 疑点 | 结论 |
|---|---|
| `recommendation_cache` 无界增长 | ✅ 有 `max_entries` 上限 |
| facets 全局缓存竞态 | ✅ 单事件循环内安全，TTL 120s |
| Orchestrator 外层重试 sleep | ✅ 已删除，重试收敛在 gateway |
| `.env` 进 git | ✅ 已 gitignore，`config/` 目录是采集配置非密钥 |
| STT 临时文件泄漏 | ✅ `finally` + `suppress(OSError)` 清理 |
| Waterlooworks SQLite 阻塞 | ✅ service 层 `asyncio.to_thread` 包装 |

---

## 5. 代码性能优化建议

### 已完成（本周期，见提交历史）

- 推荐召回 N+1（~200 次查询）→ 单 SQL + GIN 索引
- 前端按 tab 懒加载（首页只加载 Jobs + 共享模块）
- gzip（styles.css 传输 86KB → 17KB）+ 资源缓存头（no-store → max-age + ETag 304）

### 待实施（按收益排序）

| # | 建议 | 预期收益 | 位置 |
|---|---|---|---|
| 5.1 | 修复 4.1 事件循环阻塞 | 转写+LLM 期间 API 不再冻结 | `routes/interview.py:161` |
| 5.2 | `d3` 全量包（280KB）→ 按需子包 `d3-geo`+`d3-selection`（~45KB） | Heatmap tab 首载 -80% | `web/vendor/` |
| 5.3 | `canada-provinces.geojson`（1.1MB）预压缩为 `.geojson.gz` + 配合现有 gzip 中间件双层 | 弱网下地图加载时间 -60% | `web/vendor/` |
| 5.4 | `search_jobs` 兼容回退路径仍存在 N+1（`get_job` per item）——仅轻量仓库触发，建议给 `FakeJobRepo` 类场景保留但生产仓库确保 `list_jobs_for_recommendation` 恒存在 | 防御性 | `agent/tools.py` |
| 5.5 | `styles.css` 按 tab 拆分 + 懒加载（4,565 行中约 40% 属于非首屏 tab） | 首屏 CSS -40%（gzip 前） | `web/styles.css` |
| 5.6 | `_facets_cache` 与 `recommendation_cache` 失效目前靠 TTL；采集完成后可主动 `clear()`（ingestion 管道结束钩子） | 数据新鲜度 | `ingestion/` |
| 5.7 | `jobs` 表 `search_document` 已有 GIN；`list_jobs` 的 `count(*)` 每次全量计数——分页深时可用 `count` 估算或缓存 | 列表页 | `db/read_repository.py` |

---

## 6. 文件层级架构修改建议

### 6.1 拆分 `agent/tools.py`（1,621 行 → 包）

```text
agent/tools/
├── __init__.py        # 导出 AgentDeps/LlmConfig/run_tool/TOOL_CATALOG（保持公共 API 不变）
├── catalog.py         # TOOL_CATALOG + TOOL_HANDLERS 注册表
├── shared.py          # AgentDeps、JobReference 解析、_public/_ww summary
├── job_tools.py       # search/get_details/add_interested/remove_interested/tracker
├── profile_tools.py   # get_profile/update_profile/propose
├── recommend_tool.py  # tool_recommend_jobs（本身 ~400 行）
├── interview_tool.py  # generate_interview_questions
└── rendering.py       # summarize_for_llm / profile_summary
```

公共导入路径 `from wecanfindintern.agent.tools import AgentDeps` 不变，测试零改动。

### 6.2 前端拆分

```text
web/
├── styles/
│   ├── base.css tokens.css career.css
│   ├── jobs.css tracker.css agent.css interview.css waterlooworks.css profile.css
│   └── index.css (@import 入口；构建时可选 concat)
└── modules/
    ├── interview/
    │   ├── recorder.js    # 麦克风 + MediaRecorder（~150 行）
    │   ├── history.js     # Practice History 面板
    │   └── index.js       # 现有 interview.js 其余
```

`index.html`（1,099 行）短期保持单文件（tab 结构清晰），长期每 tab 抽 `<template>` 或由后端分片。

### 6.3 jobs 路由迁出 `app.py`

新建 `api/routes/jobs.py`（`jobs_router`），app.py 只保留 `create_app()` 组装 + 中间件。这是 4.2 的修复动作，同时使 contract 脚本可按 router 枚举。

### 6.4 目标层级树（摘要）

```text
api/
├── app.py                 # 只做组装：routers + 中间件 + 静态挂载
├── dependencies.py        # 所有 Repo 依赖（消除各 routes 重复的 _repo 工厂）
└── routes/
    ├── jobs.py            # 从 app.py 迁出
    └── …（现有 7 个）
agent/tools/               # 见 6.1
```

---

## 7. 项目实施架构建议

### 7.1 ✅ 已落地 — 数据库集成测试基建

```yaml
# ci.yml 追加
services:
  postgres:
    image: pgvector/pgvector:pg16
    ...（对齐 docker-compose.yml）
steps:
  - run: PYTHONPATH=src python -m pytest -m db
```

**已实现**：`tests/integration/`（conftest 会话级事件循环 + 迁移自动应用 +
TRUNCATE 隔离；`test_database.py` 覆盖 geo 聚合/召回排除/地区筛选），
CI 增加 pgvector service 并拆分 `-m "not db"` 与 `-m db` 两步。

### 7.2 工程规范补强

| 项 | 现状 | 建议 |
|---|---|---|
| `ruff format --check` | Makefile 有 target，CI 未跑 | 加入 ci.yml |
| 类型检查 | 无 | 渐进引入 mypy：先 `domain/` + `db/`（纯函数/SQL 边界） |
| 依赖锁定 | 无 lock 文件 | 引入 `uv pip compile` 或 `pip-tools` 生成 requirements.lock |
| coverage | 未统计 | `pytest --cov=src --cov-fail-under=60` 起步 |
| pre-commit | 无 | ruff + ruff-format 两个 hook 即可 |
| ADR | 无 | `docs/adr/` 记录架构决策（如"不用 LangChain"、"vendored d3"） |

### 7.3 部署与运行

- `docker-compose.yml` 仅含 postgres；建议加 `app` 服务（uvicorn）实现一键起停
- `.uvicorn.log` 落在仓库根并 gitignore ✓；建议切结构化日志（JSON）便于 grep，当前 print/logging 混用
- 备份：postgres volume 无备份策略——单用户本地可接受，但建议 `scripts/maintenance/backup.sh`（pg_dump cron）

---

## 8. 项目模块需求建议（Roadmap）

### P0 — ✅ 已全部完成（4.1 / 4.2 / 7.1，见上文修复状态）

### P1 — 下一周期

4. `agent/tools.py` 拆包（6.1）——在添加更多工具前做，成本随工具数线性增长
5. Interview **session 持久化绑定 Tracker**（此前明确推迟）：从 tracked application 一键生成面试题，练习记录挂到岗位
6. **动态追问**：分析答案后生成一个针对性追问（复用迭代循环模式），把题单变成真面试循环
7. Agent 新工具（按已排优先级）：`review_job_fit`（ATS 链式示范）→ `draft_cover_letter` → `add_custom_application`
8. **SSE 流式回复**：迭代循环最多 4 轮 plan + compose，等待时间可感

### P2 — 观察后决策

9. **嵌入缓存统一**：`recommendation` 嵌入与未来语义搜索共用同一向量化管道（避免两套 embedding 调用）
10. **deadline 提醒**：WW 岗位 `application_deadline` 已入库，做一个"临期汇总"（Agent 问候语或 Jobs 页角标）
11. **推荐反馈闭环**：记录用户对推荐结果的 bookmark 行为作为隐式反馈，反哺粗排权重
12. **多用户**：当前"单用户本地"边界（无鉴权）写进了产品文档；若未来部署到服务器，需要先补 session 鉴权 + 请求速率限制

### 明确不做（维持产品边界）

- 自动投递、自主浏览器操作、邮件外发（见 `docs/ai-agent-requirements-and-plan.md`）
- LangChain/LangGraph 引入（自研实现已覆盖所需能力，见会话决策记录）
- pgvector 之外的向量库

---

## 9. 审计方法与证据索引

- 层级树：`find` 全仓扫描（排除 .venv/.git/vendor-JobSpy）
- 连接池：`db/pool.py` + `config.py` 全文核读
- 阻塞调用：grep 全部 `async def` 路由对同步服务的直接调用（4.1 为唯一命中）
- 泄漏：`_maintenance_tasks`（manager.py:58,157）、`recommendation_cache`（有界 ✅）
- CI/规范：`.github/workflows/ci.yml`、`Makefile`、`.gitignore` 全文核读
- 测试基线：203 个测试（`grep -c "def test_"`），最近一次全量运行全部通过
- 性能数据：来自本周期实测（gzip 86→17KB、推荐召回 200→1 次查询、304 协商验证）
