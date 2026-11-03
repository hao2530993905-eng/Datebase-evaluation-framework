# Database Evaluation Framework

一套面向数据库及其数据访问层质量的独立评测框架。它评价数据库是否正确、可靠、安全、可恢复和高效，不以“与某个原软件的数据库实现完全相同”为正确性标准。

框架把两类内容严格分开：

- **通用部分**：检测、隔离、执行、证据收集、评分和报告。
- **软件专属部分**：经过验证并冻结的 Gold，包括初始状态、操作、预期结果、断言、来源证据和环境要求。

当前仓库提供可运行的 SQLite 参考实现，以及面向 PostgreSQL、MySQL 和其他测试工具的稳定扩展契约。PostgreSQL/MySQL 的原生连接适配器需要按 [扩展指南](docs/architecture.md#新增数据库适配器)实现；仓库不会把尚未执行的适配器宣称为已支持。

## 快速运行

要求 Python 3.9+，无第三方依赖。

```bash
export PYTHONPATH=src

python3 -m dbeval validate-project \
  examples/sqlite_inventory/project.json

python3 -m dbeval validate-gold \
  examples/sqlite_inventory/gold.json --require-frozen

python3 -m dbeval run \
  examples/sqlite_inventory/project.json \
  --gold examples/sqlite_inventory/gold.json \
  --output evaluation-output \
  --require-complete
```

参考 Gold 包含 31 个用例并覆盖全部 24 个指标。每次运行的重点输出：

- `report.json`：完整、机器可读、可审计的报告。
- `report.md`：适合人工审阅的中文摘要。
- `scores.json`：维度、指标和覆盖数据。
- `results.junit.xml`：CI 可消费的结果。
- `cases/<case-id>/result.json`：单用例断言、测量和错误证据。
- `environment.json`：版本、输入哈希、Schema 快照和环境证据。

运行目录在 `evaluation-output/runs/<run-id>/`，默认不会覆盖历史运行。

## 设计文档

按任务要求的设计顺序：

1. [总体架构与适配器接口](docs/architecture.md)
2. [DB1–DB8 指标、测试内容与判定](docs/metrics.md)
3. [工具选型与复用边界](docs/tools.md)
4. [测试生成机制与 Gold 结构](docs/generation-and-gold.md)
5. [可程序化评分公式](docs/scoring.md)
6. [执行流程与实施计划](docs/workflow-and-roadmap.md)

## 已实现的命令

```text
dbeval validate-project   校验软件与数据库配置
dbeval validate-gold      校验 Gold 结构、证据和冻结哈希
dbeval freeze-gold        验证通过后冻结 Gold，可选用 HMAC 签名
dbeval detect             识别数据库类型、版本、能力和 Schema
dbeval generate-gold      从 Schema/实体契约生成候选 Gold
dbeval run                隔离执行 Gold 并生成 JSON/Markdown/JUnit 报告
dbeval metrics            输出固定指标目录
dbeval mutation-report    将 Gold 变异测试结果单独汇总
```

冻结 Gold：

```bash
python3 -m dbeval freeze-gold \
  path/to/gold.unfrozen.json path/to/gold.json \
  --approved-by "database-reviewer@example.com"
```

如果设置了 `DBEVAL_GOLD_KEY`，冻结时还会生成 HMAC-SHA256 签名；验证和运行使用同一环境变量。

## 生成候选 Gold

从 Schema 生成基础结构与健康用例：

```bash
python3 -m dbeval generate-gold \
  examples/sqlite_inventory/project.json \
  --output generated-gold
```

可选实体契约可进一步生成 CRUD 状态序列：

```bash
python3 -m dbeval generate-gold \
  path/to/project.json \
  --model examples/sqlite_inventory/entity-contract.json \
  --output generated-gold
```

所有生成用例都带 `validation_status: candidate`。冻结命令不会自动把它改成已验证，因此必须先根据需求、Schema、接口规范或独立人工结论补充断言，并由人类审阅。

## 外部工具输入契约

命令型工具通过环境变量和占位符接收隔离资源：

- `${DB_PATH}`：当前用例的隔离数据库路径。
- `${CASE_DIR}`：当前用例证据目录。
- `${PROJECT_ROOT}`：投入评测的软件根目录。
- `${WORK_DIR}`：当前用例工作目录。

推荐工具输出 JSON：

```json
{
  "findings": [
    {
      "id": "SEC-001",
      "severity": "high",
      "title": "SQL injection",
      "evidence": "request and response summary",
      "location": "src/repository.py:42"
    }
  ],
  "measurements": [
    {
      "name": "p95_latency_ms",
      "value": 23.4,
      "unit": "ms",
      "sample_count": 100
    }
  ]
}
```

也可直接使用 JUnit XML。工具不可执行、超时或输出格式错误会记录为 `tool_error`，不会伪装成产品失败。

## 隔离与安全边界

- SQLite 每个 Gold case 都从初始化模板创建独立数据库副本。
- 原始数据库通过 SQLite 在线备份 API 读取；SQL 测试不写原库。
- setup 失败归类为环境错误，Gold 断言失败归类为产品失败。
- 外部命令只接收白名单基础环境变量和显式占位符，不默认把全部凭据传入子进程。
- 报告配置快照会脱敏密码、Token、凭据以及带账号密码的数据库 URL。
- 动态扫描仅允许对明确授权的隔离测试目标执行。

## 当前限制

- 参考应用是数据库层 fixture，不包含真实应用 API 或 ORM。因此样例的 DB2/DB4/DB7 只验证数据库层能力，不能据此给出应用层业务或越权结论。
- 内置适配器目前只有 SQLite；PostgreSQL、MySQL 需要原生 Adapter 或 Testcontainers 环境。
- 通用生成器可以从 Schema 和显式实体契约产生候选测试，但不能可靠地自动理解任意业务语义。
- LLM 只适合产生候选断言或测试程序；当前实现没有让 LLM 直接决定最终分数。
- Gold 变异测试汇总已实现，自动变异和用例归因执行器属于下一阶段。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
