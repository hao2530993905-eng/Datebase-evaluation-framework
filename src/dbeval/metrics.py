"""Canonical metric and indicator catalog for DB1-DB8."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


DIMENSION_NAMES: Dict[str, str] = {
    "DB1": "数据库结构与完整性",
    "DB2": "数据操作与业务逻辑",
    "DB3": "事务与并发控制",
    "DB4": "数据库集成与兼容性",
    "DB5": "数据库可靠性",
    "DB6": "数据库性能与容量",
    "DB7": "数据库安全性",
    "DB8": "数据库内部健康",
}


@dataclass(frozen=True)
class Indicator:
    id: str
    dimension: str
    name: str
    description: str
    default_weight: float


INDICATORS: Tuple[Indicator, ...] = (
    Indicator("DB1.1", "DB1", "结构与类型契约", "表、列、数据类型、键和索引符合已验证契约。", 0.35),
    Indicator("DB1.2", "DB1", "约束与数据范围", "主键、唯一、非空、检查约束及数据范围有效。", 0.40),
    Indicator("DB1.3", "DB1", "关联数据完整性", "外键和业务关联在写入及现有数据中保持完整。", 0.25),
    Indicator("DB2.1", "DB2", "CRUD 语义", "创建、读取、更新和删除行为符合已验证预期。", 0.35),
    Indicator("DB2.2", "DB2", "查询与聚合", "筛选、排序、分页、聚合和数据转换结果正确。", 0.35),
    Indicator("DB2.3", "DB2", "业务操作序列", "多步骤业务逻辑及状态变化保持正确。", 0.30),
    Indicator("DB3.1", "DB3", "原子性与回滚", "事务失败、回滚和提交边界符合预期。", 0.40),
    Indicator("DB3.2", "DB3", "并发与隔离", "并发读写不产生丢失更新或不可接受的不一致。", 0.45),
    Indicator("DB3.3", "DB3", "死锁与重试", "锁冲突、死锁检测和有限重试行为可控。", 0.15),
    Indicator("DB4.1", "DB4", "连接与交互", "连接、连接池、事务边界和错误映射可用。", 0.35),
    Indicator("DB4.2", "DB4", "ORM/API 映射", "模型、字段转换及接口与数据库之间的映射一致。", 0.35),
    Indicator("DB4.3", "DB4", "迁移与兼容性", "Schema 迁移可重复执行且版本兼容。", 0.30),
    Indicator("DB5.1", "DB5", "备份恢复正确性", "备份可恢复且数据内容保持一致。", 0.45),
    Indicator("DB5.2", "DB5", "故障恢复能力", "连接中断或异常退出后服务可恢复。", 0.30),
    Indicator("DB5.3", "DB5", "RPO/RTO", "恢复点目标和恢复时间目标满足预设阈值。", 0.25),
    Indicator("DB6.1", "DB6", "查询延迟", "典型读取和写入延迟满足 SLO。", 0.35),
    Indicator("DB6.2", "DB6", "吞吐能力", "在设定并发与数据规模下达到目标 TPS。", 0.35),
    Indicator("DB6.3", "DB6", "扩展与资源效率", "规模增长时性能退化及 CPU、内存、I/O 控制在阈值内。", 0.30),
    Indicator("DB7.1", "DB7", "注入防护", "参数化和数据访问边界能够抵御 SQL 注入。", 0.35),
    Indicator("DB7.2", "DB7", "访问控制", "身份、权限和越权访问规则有效。", 0.35),
    Indicator("DB7.3", "DB7", "敏感数据与凭据", "敏感数据、凭据和日志不产生非必要暴露。", 0.30),
    Indicator("DB8.1", "DB8", "存储与索引完整性", "数据库文件、页、内部结构和索引没有损坏。", 0.45),
    Indicator("DB8.2", "DB8", "关系健康", "外键及约束检查未发现结构异常。", 0.25),
    Indicator("DB8.3", "DB8", "基础读写可用性", "数据库可以完成最基本读写操作。", 0.30),
)

INDICATOR_MAP: Dict[str, Indicator] = {indicator.id: indicator for indicator in INDICATORS}
DIMENSION_INDICATORS: Dict[str, Tuple[Indicator, ...]] = {
    dimension: tuple(item for item in INDICATORS if item.dimension == dimension)
    for dimension in DIMENSION_NAMES
}


def validate_metric_catalog() -> None:
    for dimension, indicators in DIMENSION_INDICATORS.items():
        total = sum(item.default_weight for item in indicators)
        if abs(total - 1.0) > 1e-9:
            raise AssertionError(f"{dimension} 指标权重之和不是 1: {total}")
        for indicator in indicators:
            assert indicator.id.startswith(dimension + ".")


validate_metric_catalog()
