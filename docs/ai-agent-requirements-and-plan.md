# AI Agent Section：需求文档与实现计划

## 1. 目标

在 WeCanFindIntern 中增加一个独立的 `AI Agent` section，让用户可以用自然语言完成岗位整理、Tracker 管理、Profile 补充和岗位推荐。

Agent 负责理解意图、查找上下文、提出操作计划和解释结果；真正的数据修改必须通过受控的业务工具完成。模型不能直接修改数据库，也不能自行执行任意 SQL。

## 2. MVP 范围

### 2.1 用户入口

- 顶部导航增加 `AI Agent` section。
- 页面包含对话区、当前上下文区和操作确认区。
- 对话可以引用当前打开的岗位、岗位 ID、公司名、职位名或 Tracker 中的申请记录。
- Agent 必须显示它使用了哪些岗位、Profile 字段或 Tracker 记录，避免用户无法判断上下文来源。
- MVP 只处理单个用户的本地工作区，不设计多人协作、团队权限或公开 Agent。

### 2.2 必须支持的写操作

#### A. 添加 Interested

用户可以说“把这几个岗位加入 Interested”“把 Acme 的 Software Intern 加入感兴趣”。

要求：

- 先解析岗位引用；若匹配到多个岗位，必须让用户选择，不能猜测。
- 生成待执行列表，显示职位、公司、来源和岗位 ID。
- 用户确认后调用 `add_interested`。
- 已经处于 Interested 的岗位应保持幂等，不创建重复 Tracker 记录。
- 每个岗位返回 `added`、`already_interested` 或 `failed`，不能把批量结果合并成一个模糊状态。

#### B. 修改 Tracker 进度

用户可以说“把这个岗位改成 Applied”“把上周申请的岗位都改成 Interview”。

MVP 支持现有阶段：`Interested`、`Applied`、`Interview`、`Offer`、`Refused`。

要求：

- 修改前展示目标记录、原阶段、新阶段和受影响数量。
- 批量修改必须一次确认；部分匹配或高风险歧义必须先澄清。
- 使用现有 Tracker 的阶段规则，并正确记录阶段变更事件和相关时间字段。
- 不允许 Agent 通过自然语言创建未定义的自定义阶段。

#### C. 移除 Interested

用户可以说“不要再关注这个岗位”“从 Interested 中移除这些岗位”。

要求：

- 如果记录已经进入 Applied、Interview、Offer 或 Refused，Agent 必须提醒用户，不得静默删除重要申请记录。
- 对仍处于 Interested 的记录，确认后移除 Interested；默认优先使用可恢复的归档/取消关注语义，而不是物理删除。
- 如果用户明确要求永久删除，必须单独确认并显示不可恢复提示。
- 操作结果必须区分 `removed`、`protected`、`not_found` 和 `failed`。

#### D. 填写或补充 Profile

用户可以说“根据我的简历补充 Profile”“把我的 Python 项目加到 Projects”“补充我的毕业时间”。

要求：

- Agent 先读取当前 Profile，再生成结构化的变更草稿。
- 草稿必须显示字段级 diff、推断来源和置信度；不能直接覆盖已保存内容。
- 用户确认后调用 `update_profile`，并保留 Profile version/history。
- 对姓名、联系方式、教育经历、工作经历等高影响字段，必须要求明确确认。
- 缺少证据时应询问用户，不能用模型常识补全个人事实。
- Profile 的简历上传、解析和确认流程继续使用现有安全校验，Agent 不绕过文件验证。

#### E. 根据 Profile 推荐岗位

用户可以说“根据我的 Profile 推荐适合的岗位”“找最适合我的 WaterlooWorks 岗位”。

要求：

- Agent 读取已确认的 Profile 和岗位数据，先使用确定性筛选，再进行排序和解释。
- 推荐结果显示匹配理由、缺口、来源、岗位 ID、截止日期和原始链接。
- 公共岗位与 WaterlooWorks 岗位必须标记不同来源；WaterlooWorks 继续从独立数据库读取，不能与公共岗位跨源去重。
- 推荐只产生候选列表，不自动添加 Interested、不自动修改 Tracker、不自动申请岗位。
- 用户可以进一步要求“把第 1、3 个加入 Interested”，再进入确认和写工具流程。
- 对缺失的职位描述或 Profile 字段明确标注“不足以判断”，不得编造匹配理由。

## 3. 工具调用设计

### 3.1 MVP 工具

| 工具 | 类型 | 用途 | 是否修改数据 |
|---|---|---|---|
| `get_profile` | 读取 | 获取已确认 Profile | 否 |
| `search_jobs` | 读取 | 按关键词、来源、地点、技能、岗位 ID 查询岗位 | 否 |
| `get_job_details` | 读取 | 获取单个岗位完整信息 | 否 |
| `list_tracker` | 读取 | 查询 Tracker、阶段和现有 Interested 记录 | 否 |
| `add_interested` | 写入 | 将一个或多个岗位加入 Interested | 是 |
| `update_tracker_stage` | 写入 | 修改一个或多个 Tracker 阶段 | 是 |
| `remove_interested` | 写入 | 移除 Interested 或归档记录 | 是 |
| `propose_profile_update` | 草稿 | 生成 Profile 字段变更草稿 | 否 |
| `update_profile` | 写入 | 保存用户确认后的 Profile 变更 | 是 |
| `recommend_jobs` | 读取/计算 | 基于 Profile 返回可解释推荐 | 否 |

工具返回结构化结果，包括输入参数摘要、匹配对象、每项结果、错误类型和可展示消息。模型只负责选择工具和组织语言，业务规则由工具和领域服务负责。

### 3.2 建议的后续工具

- `list_tracker_events`：解释某条申请为什么处于当前阶段。
- `save_recommendation`：保存一次推荐结果和排序依据，便于回顾。
- `compare_jobs`：比较两到五个岗位的要求、地点、截止日期和匹配度。
- `generate_application_checklist`：根据岗位和 Profile 生成申请准备清单，不执行申请。

这些工具不应在第一版同时实现，避免 Agent section 变成无边界的万能操作入口。

## 4. WaterlooWorks 与 Tracker 的数据边界

当前 WaterlooWorks 岗位位于独立 SQLite 数据库，使用 WaterlooWorks Job ID 去重；公共岗位位于 PostgreSQL。Agent 必须保留这一边界。

为了让 WaterlooWorks 岗位也能被 Tracker 管理，建议增加“来源引用”而不是复制岗位：Tracker 记录保留自身的 application ID，同时增加来源类型和外部岗位 ID，例如 `waterloo_work + Job ID`。岗位详情由对应的来源仓库读取，不能把 WaterlooWorks 原始岗位插入公共 `jobs` 表，也不能使用公共岗位的跨源 deduplication 管线。

来源引用必须具备唯一约束和幂等行为：同一个来源类型加同一个外部 Job ID 只能对应一条 Tracker 记录。若该扩展不在 MVP 第一阶段完成，则 MVP 明确限制 Agent 只能对已有公共岗位执行 Tracker 写操作，并在 WaterlooWorks 岗位上给出清晰提示，而不是创建不完整的 Tracker 记录。

## 5. 安全、确认与失败处理

- 所有写工具默认需要用户确认；批量操作先显示预览和数量。
- 读取工具可以自动执行，但必须受查询范围、数量和超时限制。
- Profile 写入永远采用草稿审阅，不允许隐式覆盖。
- Agent 不接收密码、MFA、浏览器 Cookie 或 OAuth secret。
- 不开放模型生成任意 SQL。若未来需要 SQL 工具，只允许后端维护的只读、参数化、白名单查询模板，并限制表、列、行数和执行时间；写操作仍必须走领域工具。
- 每次工具调用记录审计信息：会话 ID、用户意图、工具名、参数摘要、确认状态、结果和错误；个人简历正文等敏感内容应脱敏或最小化记录。
- 工具必须幂等，重复消息不能重复添加 Interested 或产生重复阶段事件。
- 数据库错误、岗位不存在、来源不可用、歧义匹配和权限不足必须分别返回，Agent 不能把它们都说成“操作失败”。
- Agent 不自动发送邮件、不自动申请岗位、不自动修改 WaterlooWorks 页面。

## 6. 推荐架构

### 6.1 组件

1. **AI Agent UI**：对话、上下文引用、工具执行预览、确认按钮、结果摘要。
2. **Agent API**：创建会话、发送消息、获取消息和执行确认后的动作。
3. **Agent Orchestrator**：维护会话状态、调用模型、选择工具、处理中断和确认。
4. **Domain Tool Layer**：Profile、Tracker、公共岗位和 WaterlooWorks 的受控服务接口。
5. **Repositories**：继续复用现有 PostgreSQL repository 与 WaterlooWorks SQLite repository。
6. **Audit/Conversation Store**：保存最小必要的会话状态、工具调用和操作审计。

### 6.2 LangGraph/LangChain 选型

建议使用 LangGraph 作为编排层，LangChain 仅用于模型适配、tool schema 和检索组件。

原因是本 Agent 需要明确的状态和中断：识别意图 → 读取上下文 → 形成操作计划 → 等待确认 → 执行写入 → 汇报结果。LangGraph 更适合表达有状态、可恢复、需要人工确认的流程；单独使用 LangChain agent loop 容易让确认、重试和幂等边界分散在提示词中。

Agent 状态至少包括会话 ID、当前用户消息、解析后的岗位引用、读取上下文、待确认变更、确认结果、工具结果和最终响应。状态中不保存密码、Cookie 或完整的敏感简历内容。

## 7. 实现计划

### Phase 0：契约和数据边界

- 明确 Agent session、message、tool call、approval 和 audit 的数据模型。
- 为岗位引用定义统一格式，区分公共岗位 UUID 与 WaterlooWorks Job ID。
- 为 Tracker 增加来源引用方案，或明确第一版只支持公共岗位写入。
- 固定工具输入输出、错误类型、幂等规则和确认规则。

验收：任何一个自然语言岗位引用都能被解析为明确来源和稳定 ID；歧义时会询问用户。

### Phase 1：只读 Agent MVP

- 新增 AI Agent section 和基础对话状态。
- 实现 `get_profile`、`search_jobs`、`get_job_details`、`list_tracker`。
- 支持岗位详情解释、Tracker 查询和基于 Profile 的只读推荐。
- 推荐使用确定性过滤和可解释排序，模型只负责表达理由。

验收：Agent 能回答“我有哪些 Interested 岗位”“根据 Profile 推荐岗位”，并且不产生任何写入。

### Phase 2：安全写操作

- 实现 add Interested、阶段修改和 remove Interested。
- 增加变更预览、用户确认、幂等处理和逐项结果。
- 复用现有 Tracker repository 和事件记录，不在 Agent 中重复实现业务规则。
- 对已进入申请流程的记录增加保护逻辑。

验收：用户确认后写入结果与 Tracker 页面一致；重复执行不会产生重复记录；拒绝确认不会改变数据。

### Phase 3：Profile 草稿与确认

- 实现 Profile 读取、字段级变更草稿、证据和置信度展示。
- 实现用户确认后的 Profile 保存与版本记录。
- 将简历解析结果作为可引用证据，而不是直接作为事实覆盖 Profile。

验收：Agent 能补充缺失字段，但任何已保存字段都必须先显示 diff 并得到确认。

### Phase 4：跨来源推荐与 WaterlooWorks Tracker 引用

- 让推荐同时读取公共岗位和 WaterlooWorks 独立库，并在结果中保留来源标签。
- 实现 WaterlooWorks Job ID 到 Tracker 的来源引用，保证不写入公共岗位表、不跨源去重。
- 增加按 board、Job ID 和来源筛选的上下文工具。

验收：同一个 WaterlooWorks Job ID 在多个 board 中只形成一条 WaterlooWorks 内部岗位记录；公共岗位和 WaterlooWorks 岗位不会被 Agent 合并。

### Phase 5：可靠性和产品化

- 增加流式响应、工具调用进度、失败重试和会话恢复。
- 增加成本、延迟、工具成功率、歧义率和用户取消率监控。
- 增加权限边界、敏感日志脱敏、速率限制和模型故障降级。
- 后续再评估只读 SQL 模板、岗位比较和申请清单工具。

## 8. MVP 完成标准

- 用户可以在 AI Agent section 中用自然语言查询岗位和 Tracker。
- 用户确认后可以添加 Interested、修改阶段、移除 Interested。
- 用户可以审阅并确认 Profile 字段变更。
- Agent 可以根据 Profile 返回带理由的岗位推荐。
- 所有写操作都有确认、幂等和逐项结果。
- 公共岗位与 WaterlooWorks 岗位保持数据库和去重边界。
- Agent 不执行任意 SQL，不自动申请岗位，不处理用户认证秘密。
- 现有 Jobs、Profile、Tracker、WaterlooWorks section 的功能不被 Agent 改坏。
