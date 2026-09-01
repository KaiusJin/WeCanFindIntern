# 可靠性、并发与恢复手册

本文档是处理异常时的操作级参考。它区分“重试”“幂等重跑”“部分成功”和“回退”，不把所有失败都当作同一种错误。系统当前的总体原则是：网络和模型调用做有限重试；数据库写入使用短事务；已经提交的数据不回滚整个 campaign；下一次运行通过唯一键、输入 hash 和 source identity 继续收敛。

## 1. 失败分类

| 类别 | 典型信号 | 是否自动重试 | 用户/运维动作 |
|---|---|---:|---|
| 正常空结果 | JobSpy 没有 ERROR 且返回空页 | 否 | 视为成功空页 |
| 瞬时上游失败 | timeout、连接错误、JobSpy logger ERROR | 是 | 等待本次退避；耗尽后看 source failure |
| 永久输入错误 | 422、无效 source/filter/file | 否 | 修正参数或文件后再提交 |
| 单条脏数据 | 缺失字段、未知地点、解析失败 | 通常否 | 保留可用字段，记录 row/posting failure |
| 数据库/迁移失败 | `/health` 失败、migration checksum mismatch | 当前操作有限 | 先修复数据库/版本/权限，不继续写入 |
| provider 配置错误 | missing key/model/base URL | 否 | Settings 中补配置；不会重复调用 |
| provider transport 失败 | 5xx、网络或 rate limit | gateway 有限重试 | 仍失败时使用功能回退 |
| provider 输出错误 | malformed JSON、schema 不通过 | 否 | 保留确定性结果或安全错误 |
| 并发冲突 | file lock、pending approval、unique conflict | 取决于场景 | 复用现有结果或稍后重试 |

## 2. 公共职位 campaign

### 2.1 运行阶段

1. 读取并展开 catalog；配置错误在 network stage 前失败。
2. 每个 enabled definition/source 作为独立 query task，由 semaphore 限制并发。
3. 每个 page 调用 JobSpy；同一 query 内按 fingerprint 去重。
4. 所有 network task 完成后才创建/写入 ingestion run 的 canonical records。
5. 按 `batch_size` 进行 PostgreSQL ingest；每个 batch 是独立事务。
6. dedupe 完成后运行 salary 和 recruiting-term enrichment。
7. 写入 run summary 和 success/partial 状态。

### 2.2 重试公式

默认每页 `max_retries=3` 次额外尝试。第 n 次重试等待：

```text
min(15s, 1.5s × 2^(n-1) + random jitter[0.5s, 2.0s])
```

重试只包住 JobSpy query，不包住整个 campaign；因此一个 source 失败不会取消已经成功的 source。`scrape_checked()` 把“空表但 JobSpy 记录了错误”升级为异常，防止假成功。

### 2.3 断点和安全重跑

当前没有持久化 page checkpoint。offset、`seen_for_query` 和当前 query 的结果只存在进程内存。中断后的安全操作是重新运行 campaign：

- 已提交的 batch 通过 source fingerprint/唯一约束更新或保持 unchanged；
- 同一 dedupe block 由 advisory lock 串行；
- 相同 raw payload 不重复写 snapshot；
- 已有结构化 salary/recruiting term 不被缺失的新结果清空；
- 尚未处理的 query 会从第一页重新抓取。

这意味着“恢复”可能重新访问上游并产生额外网络成本，但不会因为旧 offset 跳过当前结果。不要手工删除 lock 文件来“清 checkpoint”；锁是进程级互斥，不是进度文件。

### 2.4 campaign 结果判定

- 所有 query 成功，即使部分 query 是合法空页：`success`。
- 至少一个 query 重试耗尽但 DB 阶段完成：`partial`，summary 中保留 failures。
- DB/migration/不可恢复的 pipeline 异常：run 标记 failed/partial，命令返回非零；已提交 batch 仍然保留。
- enrichment 失败：不撤销 canonical job；统计中标记失败，下次按缺失或输入 hash 再处理。

## 3. 数据库事务与回退

### 3.1 什么会回滚

仓库的单次 mutation、ingest batch、dedupe 写入、Tracker event+snapshot、Profile Apply 和 Agent approval execution 都在自己的事务边界内。事务异常时，该边界内的 SQL 回滚；已经提交的其他 batch/请求不回滚。

### 3.2 什么不会自动回滚

系统不会因为一个 source 或 enrichment 失败而删除本次已经入库的职位，也不会把全库恢复到 campaign 开始前。若需要恢复数据库到某个历史状态，应使用桌面备份/restore 或数据库管理员制定的恢复流程，并先备份当前状态。

### 3.3 数据库异常排查

1. 访问 `/health`，确认 `SELECT 1` 和 pool 可用。
2. 检查 migration checksum、extension（`vector`、`pgcrypto`、`pg_trgm`）和数据库用户权限。
3. 查看 campaign summary 中的 `database_stats`、run status 和日志。
4. 若是 statement timeout，先降低批量/并发或优化 SQL，不要盲目无限重试。
5. 修复后直接重跑；依赖幂等键，不要先清空 `jobs`。

## 4. 推荐索引队列

职位更新由数据库 trigger 放入 `recommendation_index_queue`。维护循环按队列分页处理：

- `attempts < 5` 才会再次处理；
- indexing 或 embedding 异常写入 `last_error` 并递增 attempts；
- poison row 不会阻塞同批其他职位；
- 达到上限的项保留在队列中，供检查；该职位再次更新时会将 attempts 重置；
- 没有 embedding 配置时保留 lexical document，推荐仍可走确定性/词法路径；
- embedding 失败不会删除已存在的 document/chunks。

运维先修复 provider/base URL/model/dimensions，再更新或重新入队目标职位。只有确认数据损坏时才使用针对性的 maintenance/backfill 命令。

## 5. WaterlooWorks 恢复

WaterlooWorks 采集按 board 顺序运行，board 内 posting 失败被隔离。SQLite 使用 foreign keys 和 30 秒 busy timeout；posting 以 external Job ID insert-once，重复遇到只更新 `last_seen_at`。

| 情形 | 当前状态 | 恢复 |
|---|---|---|
| 未登录 | `waiting_for_login` | 在专用 Chrome 完成 SSO/MFA，再刷新 status |
| Chrome 关闭 | `idle`/closed-browser message | 重新 launch，完成登录后重新 collect |
| 一个 board 失败 | run `partial`，其他 board 保留 | 修复页面/连接后重新 collect 全部 board |
| 单 posting 失败 | board error/count 增加，其他 posting 保留 | 下一次全量 collect；已知 Job ID 不重复写正文 |
| collector 被取消 | run failed/cancelled | 重新 collect；不依赖网页 checkpoint |
| application detail API 失败 | 列表字段保留，description failure 记录 | 后续 sync 重试 detail；Tracker 不删除 |

不要把 WaterlooWorks SQLite 记录迁移到 PostgreSQL 来“修复重复”；两个 source namespace 是设计上的隔离。

## 6. LLM 和确定性回退

| 功能 | 首选 | 回退/终止 | 数据影响 |
|---|---|---|---|
| 薪资 | source structured | regex → DeepSeek；失败保留缺失 | 不覆盖已有有效薪资 |
| Recruiting term | cache → regex | DeepSeek；失败记录 generation | 下次 input hash 变化/未完成时可再跑 |
| Recommendation | lexical + vector | 无 vector 时 lexical/skill overlap | 不让模型创造候选 |
| ATS score/match | deterministic | 不依赖 LLM | 分数不会因 provider 挂掉而消失 |
| ATS commentary | LLM | 保留 deterministic score，显示 commentary error | 不改主分数 |
| Cover letter | Writer/Reviewer ≤5 rounds | 返回最后非空草稿并标未通过 | 用户可检查，不宣称通过 |
| Interview STT | local faster-whisper | 输入文字优先；无音频/无语音返回可读错误 | 不把音频上传给模型作为必要条件 |
| Agent plan | bounded JSON plan | 安全 assistant reply；写入不会发生 | approval/write 不被错误输出触发 |

共享 gateway 只对 transport failure 做有限重试；JSON/业务校验失败不重试。cache lookup/store 失败本身被当作 cache miss/无 cache，不应阻断主功能。

## 7. Electron 桌面恢复

桌面启动失败时按顺序检查：

1. Electron 单实例和 packaged resource 是否完整；
2. PostgreSQL bundle 是否包含 `postgres/initdb/pg_ctl/pg_isready/createdb/psql` 和 `vector.control`；
3. `PG_VERSION` 是否为 16；major mismatch 不直接启动；
4. sidecar 是否在 60 秒内打印 `ready`；
5. loopback token、`WCFI_USER_DATA_DIR`、`WCFI_RESOURCE_DIR` 和 `DATABASE_URL` 是否存在；
6. migration 是否因 checksum 或 extension 权限失败。

恢复 PostgreSQL backup 的规则：

- 手动 restore 前创建 safety backup；
- restore 失败自动回退到 safety backup；
- restore 只覆盖 PostgreSQL data，不覆盖 WaterlooWorks、Chrome、models、secrets；
- restore 完成后重新启动，让 migrations 在 API listen 前执行；
- major version 升级使用兼容的 backup/restore 或 `pg_upgrade`，不把新版 server 指向旧 data directory。

## 8. 并发冲突处理

- 看到“campaign already running”：确认已有手动/定时/桌面任务，等待其完成；不要并行启动第二个 campaign。
- Agent approval conflict：approval 只允许从 pending 决策一次；刷新列表查看最终状态，不重复执行同一写入。
- Tracker unique conflict：按 source/job identity 读取现有记录；重复 Interested 应视为幂等成功。
- SQLite busy/Chrome target conflict：等待当前 WaterlooWorks task，确认浏览器仍连接后再操作。
- pool exhausted/timeout：降低 API worker 数、pool max、采集 batch/concurrency，检查慢 SQL。

## 9. 证据和日志

公共采集的 `logs/campaign_summary_latest.json` 保存状态、query/database/enrichment 统计和最多截断后的 failures。桌面模式把日志放在用户 data 的 `logs/`；collection status 以临时文件替换方式写入，启动时若发现上次 `running=true` 会显示 interrupted 并安排下一次运行。

日志不应包含 API key、密码、MFA、cookie 或完整简历。排查时优先收集时间、run id、source、board、attempts、last_error 和 HTTP 状态，不要把 raw secret 粘贴到 issue。
