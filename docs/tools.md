# 工具选型与复用边界

选型原则是先使用数据库原生能力和成熟工具，再开发业务编排。版本、许可证和 CI 可用性需要在落地时锁定。

| 维度 | 可直接调用/轻配置 | 需要测试用例或 Gold | 仅作为组件/需要开发 |
| --- | --- | --- | --- |
| DB1 | SQLite PRAGMA、数据库元数据 SQL、迁移工具 `validate` | pgTAP、tSQLt | 业务 Schema 规范抽取、约束变异器 |
| DB2 | 应用已有测试、SQL fixture | pgTAP、tSQLt、EvoMaster/RESTler | 业务状态机生成器、Gold Builder |
| DB3 | 数据库原生事务命令、已有并发测试 | pgbench/sysbench 自定义脚本、Hypothesis stateful | 并发不变量检查、死锁/重试编排 |
| DB4 | Testcontainers、ORM 自带迁移测试 | Flyway/Liquibase、应用集成测试 | 连接池观测、API-to-DB Adapter |
| DB5 | pg_dump/pg_restore、SQLite backup、pgBackRest | 恢复验证 SQL、容器重启脚本 | 故障注入、RPO/RTO 编排、恢复不变量 |
| DB6 | pgbench、sysbench、HammerDB | 业务查询脚本、目标 SLO | 连续评分、资源采样、规模曲线 |
| DB7 | Semgrep、JUnit 归一化 | SQLmap、OWASP ZAP、CodeQL | 授权边界、租户越权序列、严重级别归一化 |
| DB8 | SQLite PRAGMA、pg_amcheck、MySQL CHECK TABLE | 数据库健康计划 | 跨引擎 Adapter、损坏注入与恢复验证 |

## DB1：结构与完整性

- **pgTAP**：成熟 PostgreSQL 单元测试扩展。适合直接写 Schema、约束和函数断言，需要 Gold SQL；不是跨引擎。
- **tSQLt**：SQL Server T-SQL 测试框架。需要部署和专用测试数据库。
- **Schema introspection**：引擎事实来源；适合自动生成候选结构和字段契约。
- **Flyway/Liquibase**：适合迁移历史和目标版本验证。`validate`、`migrate` 可以调用，业务迁移预期仍需 Gold。

直接调用不能替代“字段是否属于需求”的判断，因此自动 Schema 快照只能是 candidate 证据。

## DB2：CRUD 与业务逻辑

- **pgTAP/tSQLt**：执行 SQL 断言，直接复用；需要提供 fixture、操作和断言。
- **Hypothesis**：属性测试与 stateful testing 生成器，适合 Python 数据访问层。必须提供模型状态和不变量，不能把当前实现当 oracle。
- **EvoMaster/RESTler**：生成 API 调用序列，适合应用后端。需要 OpenAPI/运行服务；发现的多步请求可利用 DB 前后状态验证。
- **DBGRILLER/TOGA/CoverUp**：研究思路来源，不是即插即用通用产品。可借鉴 Schema 感知的数据生成、约束覆盖和搜索式序列生成。

框架已实现确定性的实体契约 CRUD 状态序列生成器；任意业务规则仍需需求或领域模型。

## DB3：事务与并发

- 数据库原生事务和隔离级别能力直接可用。
- **pgbench** 和 **sysbench** 可承担并发负载和基础事务验证，需要自定义 SQL/脚本。
- **Hypothesis** stateful testing 适合生成操作序列和检查最终不变量。
- 故障注入、锁冲突、重试和幂等需要用 Runner 编排；没有单一工具能自动证明业务隔离正确。

## DB4：集成与兼容性

- **Testcontainers**：直接复用容器生命周期和隔离，需要锁定镜像版本。
- **Flyway/Liquibase**：迁移执行和重复执行验证。
- ORM/API 映射由应用 Adapter 或现有集成测试覆盖。驱动层 `typeof` 和序列化检查不能替代 ORM 字段映射。
- 版本矩阵应固定数据库主版本和驱动版本；每个组合记录版本证据。

## DB5：可靠性

- **SQLite backup API**：参考实现直接使用。
- **pg_dump/pg_restore**：适合逻辑备份恢复，需要恢复目标库和验证 SQL。
- **pgBackRest**：适合 PostgreSQL 物理备份、WAL 和恢复窗口测试，需要专用仓库和配置。
- **Testcontainers**：适合进程退出、容器重启和一次性恢复环境。
- RPO/RTO 和业务恢复正确性需 Runner 计算；备份工具成功返回码不等于恢复正确。

## DB6：性能与容量

- **pgbench**：PostgreSQL 协议级并发与 TPS，需要脚本和参数。
- **sysbench**：多数据库基础负载，需要专用 benchmark 脚本。
- **HammerDB**：适合更大规模事务负载和跨数据库比较。
- 框架必须记录预热、并发、数据量、硬件、错误率和百分位延迟。单次平均延迟不足以形成质量结论。

## DB7：安全

- **Semgrep**：规则化静态扫描，容易接入 JSON/JUnit；需要规则集和误报治理。
- **CodeQL**：数据流分析强，适合代码库；CI/许可证和构建成本需要在落地时评估。
- **OWASP ZAP**：应只针对授权隔离环境，主动扫描可能修改数据。
- **SQLmap**：只对明确授权的专用测试目标运行；不得用于生产或未经授权系统。

安全发现按严重级别进入分数上限。普通高通过率不能掩盖严重漏洞。

## DB8：内部健康

- SQLite：`PRAGMA integrity_check`、`PRAGMA quick_check`、`PRAGMA foreign_key_check`。
- PostgreSQL：`pg_amcheck`、`amcheck` 相关函数、原生完整性视图；需权限和专用环境。
- MySQL：`CHECK TABLE`/`mysqlcheck` 可发现部分表结构问题，但不能证明业务数据正确。

健康工具一般只读，但恢复或修理必须放在隔离副本。

## 工具结果归一化

建议为每个 Tool Adapter 固定：

1. 工具 ID、版本、许可证和规则集哈希。
2. 输入命令、目标、超时和授权范围。
3. 原始输出证据文件。
4. 归一化 `findings`、`measurements`、退出码。
5. 工具输出解析失败的处理规则。

解析失败是 `tool_error`，不是 `failed`。
