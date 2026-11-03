# 执行流程与实施计划

## 从输入到报告

### 1. 准备输入

- 被测软件版本和制品哈希。
- 数据库类型、DSN 或文件；测试凭据。
- Schema、迁移、Seed、需求、接口规范。
- 隔离环境配置。
- 经过验证并冻结的 Gold。

### 2. 预检

运行器校验：

- Project/Gold schema 版本。
- 软件名、版本和可选制品哈希一致。
- Gold 已冻结，证据文件哈希未变。
- Gold 断言具有显式值或可执行 oracle。
- 数据库配置必需字段存在。

### 3. 识别与建环境

Detector 收集数据库类型、版本和能力。Adapter 准备一次性模板或 Testcontainers 环境。初始化失败属于环境错误，不执行质量评分。

### 4. 逐用例执行

```text
clone clean template
→ execute setup
→ execute operation sequence
→ evaluate assertions
→ collect measurements/findings
→ persist raw evidence
→ destroy isolated copy
```

每个 case 从同一验证过的初始状态开始，防止用例互相污染。

### 5. 归一化和分类

结果分为 passed、failed、N/A、环境和工具错误。断言失败与执行错误分开。

### 6. 评分

按 case → indicator → dimension → total 四层计算，再单独计算覆盖率和执行完整性。安全发现应用严重级别上限。

### 7. 输出和门控

输出：

- 权威 JSON。
- 中文 Markdown。
- JUnit。
- 逐用例原始证据。
- 环境、版本和哈希。

CI 可设置最低总分、完整度和完整指标要求。

## 复现要求

一次正式运行至少保存：

- Project 配置哈希和脱敏快照。
- Gold 文件、内容哈希、证据清单、冻结审核人。
- 软件制品哈希。
- 数据库引擎版本、容器 digest 和驱动版本。
- CPU、内存、磁盘、操作系统。
- Seed/迁移哈希。
- 外部工具版本、规则集版本。
- 并发、预热、数据规模和超时。
- 原始测试日志与测量样本。

性能环境还应固定 CPU 限额、I/O 类型、网络位置和并行度。安全主动扫描只能对授权隔离目标执行。

## 实施优先级

### 阶段 0：可运行基线

当前仓库：

- 统一 Project/Gold/Result 数据模型。
- SQLite 隔离 Adapter。
- SQL、benchmark、concurrency、backup、health、command 执行。
- Gold 验证、证据哈希和冻结。
- DB1–DB8 指标和评分。
- JSON/Markdown/JUnit 报告。
- Schema/实体契约候选 Gold 生成。

退出标准：31 个参考用例可执行，冻结修改可检测，评分与覆盖率分开。

### 阶段 1：PostgreSQL/MySQL 与容器隔离

优先级最高，因为能最大化复用：

- psycopg/mysql 驱动 Adapter。
- Testcontainers 镜像 digest 固定。
- `pg_dump`/`pg_restore`、`pg_amcheck`、`pgbench`。
- `mysqldump`/`mysql`、`CHECK TABLE`、sysbench。
- 事务、隔离和备份恢复标准用例库。

退出标准：两种引擎至少各有一个完整参考项目，原始数据库零写入。

### 阶段 2：成熟工具 Adapter

- JUnit、JSON、SARIF 归一化。
- Flyway/Liquibase。
- Semgrep/CodeQL/SQLmap/ZAP 的授权与严重级别适配。
- EvoMaster/RESTler 结果到 API+DB 状态验证。

退出标准：工具缺失、执行失败、解析失败、产品失败四分法可追踪。

### 阶段 3：业务 Gold 生成

- OpenAPI/ORM/Schema 联合抽取。
- 实体状态机与不可变状态建模。
- Hypothesis stateful 生成和 seed 回放。
- 测试数据约束求解。
- LLM 候选测试安全审查和独立 oracle 验证。

退出标准：生成 candidate → 人工 verified 的比例、拒绝原因和误报率可度量。

### 阶段 4：故障、安全与性能深化

- 容器重启、网络中断、只读切换和磁盘故障注入。
- RPO 数据点与 WAL/binlog 恢复窗口验证。
- 角色、租户和对象级越权矩阵。
- 多规模性能曲线、资源限制和错误率。

退出标准：故障不污染原环境；恢复时间、数据损失和失败类型可复现。

### 阶段 5：Gold 变异与基准治理

- 自动生成数据库/业务变异体。
- 将 mutation 与被杀 case 归因。
- Gold 版本、需求变更和退役流程。
- 多软件题库校准和评分漂移检测。

退出标准：仅增加覆盖不提高质量分；Gold 变异得分与人工审计结果稳定对应。

## 落地建议

先做“数据库能提供证据”的测试，再做“业务断言”。不要一开始就要求 LLM 生成完整测试程序；先建立：

1. 冻结 Gold 契约。
2. 错误分类。
3. 隔离与哈希证据。
4. 代表性原生工具。

之后每天用真实项目补充业务 Gold，用变异测试淘汰不敏感的断言。
