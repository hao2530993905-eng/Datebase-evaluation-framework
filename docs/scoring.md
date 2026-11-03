# 评分机制

## 分层

评分对象包含四类不同信息：

1. **数据库质量**：DB1–DB8 的 0–100 分。
2. **Gold 覆盖率**：已设计指标和用例数量。
3. **执行完整性**：有多少适用用例真正得到产品结果。
4. **Gold 敏感度**：变异测试检出率。

这四类不合并成一个隐式分数。

## 用例点位

普通通过/失败 case：

```text
s_k = 1  断言全部通过
s_k = 0  任一 Gold 断言失败
s_k = null  环境/工具/框架错误或 N/A
```

性能、RTO 等连续指标可使用阈值曲线。低值优：

```text
s = clamp((floor - x) / (floor - target), 0, 1)
```

高值优：

```text
s = clamp((x - floor) / (target - floor), 0, 1)
```

其中 `target` 为满分目标，`floor` 为 0 分边界。端点关系必须满足 `floor > target`（低值优）或 `target > floor`（高值优）。

## 指标得分

对指标 `ij` 的所有可评用例：

```text
S_ij = 100 × Σ(w_k × s_k) / Σ(w_k)
```

仅 `passed`/`failed` 且 `point_score != null` 的适用用例进入分母。环境错误和工具错误不计为产品失败，但会降低执行完整性。

## 一级维度

```text
D_i = Σ(W_ij × S_ij) / Σ(W_ij)
```

仅对已评估指标归一化，避免把覆盖率直接当质量分。若某指标没有可评结果，维度标记 `incomplete`，并显示指标覆盖数。

默认指标权重：

| 维度 | 指标权重 |
| --- | --- |
| DB1 | 0.35 / 0.40 / 0.25 |
| DB2 | 0.35 / 0.35 / 0.30 |
| DB3 | 0.40 / 0.45 / 0.15 |
| DB4 | 0.35 / 0.35 / 0.30 |
| DB5 | 0.45 / 0.30 / 0.25 |
| DB6 | 0.35 / 0.35 / 0.30 |
| DB7 | 0.35 / 0.35 / 0.30 |
| DB8 | 0.45 / 0.25 / 0.30 |

## 总分

初始维度等权，也允许项目配置。对可评维度做加权宏平均：

```text
Total = Σ(P_i × D_i) / Σ(P_i)
```

如果维度没有分数，`D_i = null`。报告必须同时给出 `quality_status=incomplete`，不能把缺失维度静默重归一化为“完整”。

## 安全门控

安全扫描发现漏洞时，DB7 先按普通用例计算原始分，再应用最高严重级别上限：

| 最高严重级别 | DB7 分数上限 |
| --- | ---: |
| critical | 20 |
| high | 40 |
| medium | 65 |
| low | 85 |

```text
D_DB7_final = min(D_DB7_raw, cap)
```

上限是保守门控。严重发现不能被大量通过用例平均掉。未来可以把认证绕过、任意文件读取、跨租户数据泄漏定义为额外硬失败。

## 执行完整度

```text
Completeness = (passed + failed) / applicable_cases
```

N/A 不计入分母，但必须保留适用性理由。`environment_error`、`tool_error`、`framework_error` 单独列出；正式评测建议要求完整度 100%。

## Gold 覆盖率

```text
IndicatorCoverage = 有用例的指标数 / 指标目录总数
```

覆盖率只描述 Gold 设计，不提高或降低产品质量分。建议同时报告：

- 每维度覆盖指标比例。
- case 数量。
- 已验证 vs candidate 数量。
- 需求覆盖率，如果存在需求 ID 映射。

## RPO/RTO

RPO 可以是精确行集合、业务不变量或数据快照哈希。推荐先判 correctness：

```text
RPO_pass = recovered_state == verified_backup_state
```

数据不一致直接使恢复正确性用例失败。RTO 使用低值优曲线：

```text
RTO_score = ramp(recovery_seconds, target, floor)
```

小数据恢复时间不能外推为生产 RTO；报告必须记录数据规模和环境。

## Mutation score

```text
MutationScore = killed / (killed + survived + uncovered)
```

`equivalent` 和 `invalid` 排除。未覆盖 mutation 计入分母，因为它说明 Gold 对重要行为不敏感。Mutation score 只属于 Gold Quality 报告。

## 状态与门控

- `score_complete=true` 要求全部维度、全部指标都有可评分数。
- `quality_status` 区分 `complete` / `incomplete`。
- CLI `--require-complete` 可在不完整时返回非零退出码。
- `--min-execution-completeness` 要求实际执行比例。
- `--fail-under` 设置产品质量门槛。

环境失败不判处产品质量失败，但正式门控不应接受不完整评测。

## 报告去重

防止重复计分：

- 一次全局 FK 扫描只给 DB8.2。
- DB1.3 使用独立写入/业务关系用例。
- 同一性能查询不应同时给延迟、吞吐和扩展三个指标，除非分别测量了对应属性。
- 安全工具同一 finding ID 需去重后再应用 severity cap。
- 已有应用集成测试可作为多个 case 的来源，但每个断言只属于一个 indicator。
