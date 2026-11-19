# [Feature] 使用 GitHub Actions、Supabase 和 Cloudflare R2 实现集中式岗位采集与分发

## 概述（Overview）

将 WeCanFindIntern 的公共岗位采集流程迁移至基于 **GitHub Actions + Supabase PostgreSQL + Cloudflare R2** 的集中式云端架构。

目前，岗位采集程序和 PostgreSQL 数据库均运行在用户本地的桌面应用中。这不仅增加了不必要的资源消耗，还会导致多个用户重复采集相同岗位，并且当用户关闭应用或电脑关机时，岗位数据便无法继续更新。

新的架构应当在云端集中采集公共岗位信息，将标准化后的岗位记录存储到 Supabase，并通过 Cloudflare R2 分发带版本管理的完整数据快照和增量更新。

桌面应用将自动把岗位数据同步到本地数据库，同时保留 AI Agent 的执行、用户专属数据以及个性化检索等本地功能。

## 架构设计（Proposed Architecture）

```text
GitHub Actions（定时调度）
          |
          v
Python 岗位采集器
（JobSpy / 现有采集流程）
          |
          v
Supabase PostgreSQL
- 标准化岗位记录
- 数据去重
- 变更追踪
          |
          v
快照 / 增量数据导出器
          |
          v
Cloudflare R2
- manifest.json
- 完整数据快照
- 增量更新包
          |
          v
桌面应用
- 自动同步
- 本地岗位数据库
- 本地 AI Agent / RAG
```

## 实现任务（Implementation Tasks）

### 1. 集中式岗位采集（Centralize Job Collection）

- [ ] 复用现有 Python 采集流程，包括 JobSpy、数据标准化、去重、缓存和失败重试机制。
- [ ] 创建 GitHub Actions 定时工作流，每隔 3–6 小时执行一次岗位采集。
- [ ] 通过 `workflow_dispatch` 支持手动触发采集任务。
- [ ] 使用 GitHub Actions Secrets 安全配置 Supabase 数据库凭证。
- [ ] 防止多个采集任务同时运行。
- [ ] 持久化采集进度，并支持任务中断或失败后的重试。
- [ ] 验证采集程序在 GitHub 托管 Runner 上的运行可靠性。

### 2. 配置 Supabase PostgreSQL

- [ ] 将必要的公共岗位数据表及索引迁移至 Supabase。
- [ ] 保留现有的数据标准化和去重逻辑。
- [ ] 将采集时间与实际内容更新时间分开记录：
  - `last_seen_at`：采集器最后一次发现该岗位的时间。
  - `sync_seq` / `content_version`：仅在客户端可见的岗位内容或状态发生变化时更新。
- [ ] 追踪新增、更新、过期和关闭的岗位。
- [ ] 为原始岗位快照及不必要的历史记录制定数据保留策略。
- [ ] 确保用户专属数据不会进入公共岗位数据库。
- [ ] 限制数据库访问权限，避免向桌面客户端暴露高权限数据库凭证。

### 3. 实现 R2 快照与增量数据分发

- [ ] 配置 Cloudflare R2 Bucket，用于分发公共岗位数据。
- [ ] 定期导出带版本号的完整岗位数据快照。
- [ ] 生成仅包含实际岗位变更的增量更新包。
- [ ] 在增量更新中包含岗位关闭和删除事件。
- [ ] 维护 `manifest.json`，记录当前数据版本、快照信息以及可用的增量更新包。
- [ ] 使用 gzip 或其他合适的压缩格式减少数据包体积。
- [ ] 为数据包添加校验和（Checksum）及记录数量，以验证数据完整性。
- [ ] 先上传数据包，再原子化发布更新后的 Manifest，避免客户端读取到不完整的数据版本。
- [ ] 通过自定义域名配置公共只读访问。
- [ ] 制定数据保留和清理策略，同时确保仍受支持的客户端版本能够获取所需的数据包。

R2 目录结构示例：

```text
wecanfindintern/
├── manifest.json
├── snapshots/
│   └── jobs-000100.json.gz
└── deltas/
    ├── jobs-000101.json.gz
    ├── jobs-000102.json.gz
    └── jobs-000103.json.gz
```

### 4. 实现桌面端增量同步（Desktop Incremental Synchronization）

- [ ] 桌面应用启动时自动检查 R2 Manifest。
- [ ] 应用运行期间定期检查更新。
- [ ] 首次初始化时下载完整数据快照。
- [ ] 后续更新仅下载本地缺失的增量数据包。
- [ ] 将下载的变更应用到本地岗位数据库。
- [ ] 仅在数据库变更成功提交后，持久化同步游标（Synchronization Cursor）。
- [ ] 支持失败重试、下载中断恢复，以及增量包缺失或过期时的恢复。
- [ ] 当无法继续增量同步时，回退到完整快照同步。
- [ ] 确保离线状态下仍可访问此前已经同步的岗位。
- [ ] 避免同步过程阻塞用户界面。

初始实现应兼容现有的 Electron + Python 架构。SQLite 和 Tauri 的迁移应作为独立任务处理。

### 5. 保留本地功能（Preserve Local-Only Functionality）

- [ ] WaterlooWorks 的岗位采集继续在本地运行，因为它依赖用户个人身份认证。
- [ ] 简历、申请记录、用户偏好和 Agent Memory 继续保存在本地。
- [ ] 保留现有 AI Agent、RAG 和个性化岗位排名功能。
- [ ] 确保公共岗位同步不会覆盖用户本地的专属数据。

### 6. 测试与可观测性（Testing and Observability）

- [ ] 测试定时采集和手动触发的工作流。
- [ ] 验证内容未变化的岗位不会生成不必要的增量记录。
- [ ] 测试新增、更新、过期和关闭岗位的同步。
- [ ] 测试首次完整同步和后续增量同步。
- [ ] 测试应用关闭、下载中断以及长时间离线后的恢复能力。
- [ ] 验证重复执行同步不会产生重复记录。
- [ ] 记录采集耗时、岗位数量、同步数据包大小及失败率。
- [ ] 监控 Supabase 数据库容量、出口流量，以及 R2 存储空间和请求使用量。

## 验收标准（Acceptance Criteria）

- 公共岗位能够自动定期采集，无需任何桌面客户端保持运行。
- GitHub Actions 能够按计划成功执行现有 Python 采集流程。
- Supabase 能够存储标准化公共岗位数据，并避免重复记录未变化的岗位。
- R2 能够提供带版本管理的完整快照和增量更新包。
- 桌面客户端能够自动下载并应用岗位更新。
- 增量同步中只包含实际发生变化的岗位。
- 客户端在离线数天后仍能够正确恢复同步。
- 重复或中断的同步操作不会破坏本地数据库。
- 现有 AI Agent 和本地 WaterlooWorks 功能继续正常运行。
- 根据预期工作负载，验证整个方案符合 Supabase Free 和 R2 Free 的免费额度限制。

## 不包含的工作（Out of Scope）

- 将 Python AI Agent 重写为 Rust。
- 将 Electron 迁移至 Tauri。
- 将整个本地 PostgreSQL 数据库迁移至 SQLite。
- 将 WaterlooWorks 或其他需要用户身份认证的数据源迁移到云端采集。
- 将简历、Agent Memory 或其他用户私有数据同步到云端。
- 为新增岗位实现实时推送通知。

## 预期成果（Expected Outcome）

构建一套集中式、低成本的岗位采集与分发系统，使 WeCanFindIntern 能够在用户设备离线或关机时持续采集岗位信息。

该架构将减少重复爬取和数据库带宽消耗，并为桌面客户端提供可靠的增量同步能力，同时保留现有的本地 AI Agent 和个性化岗位检索功能。