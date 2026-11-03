"""Deterministic candidate Gold generation.

Generated material is always marked as candidate. It cannot be frozen without
an independent verification step. Schema-derived facts and a supplied entity
contract are used; current query outputs from the product are never promoted
to an oracle.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adapters import create_adapter
from .config import ProjectConfig
from .errors import ConfigurationError
from .util import canonical_hash, read_json, sha256_file, utc_now, write_json


def _candidate_provenance(source_ids: List[str], rationale: str) -> Dict[str, Any]:
    return {
        "source_ids": source_ids,
        "rationale": rationale,
        "validation_status": "candidate",
        "verified_at": None,
        "verified_by": None,
    }


def _column_value(table: str, column: Dict[str, Any]) -> Any:
    declared_type = str(column.get("declared_type", "")).upper()
    name = str(column["name"])
    digest = hashlib.sha256(f"{table}.{name}".encode("utf-8")).hexdigest()[:10]
    if "INT" in declared_type:
        return 7
    if any(token in declared_type for token in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
        return 1.25
    if any(token in declared_type for token in ("DATE", "TIME")):
        return "2026-01-01"
    if "BOOL" in declared_type:
        return 1
    return f"dbeval-{table}-{name}-{digest}"


def generate_schema_cases(schema: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    tables = schema.get("tables", [])
    for table in tables:
        name = str(table["name"])
        quoted = name.replace('"', '""')
        cases.append(
            {
                "id": f"GEN.DB1.table.{name}",
                "dimension": "DB1",
                "indicator": "DB1.1",
                "title": f"表 {name} 存在",
                "type": "sql",
                "weight": 1.0,
                "steps": [
                    {
                        "sql": (
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'table' AND name = :table_name"
                        ),
                        "params": {"table_name": name},
                        "expect": {"kind": "rows_equal", "value": [[name]]},
                    }
                ],
                "provenance": _candidate_provenance(
                    [source_id],
                    "候选契约来自 Schema 目录；需独立确认该表属于要求的软件数据结构。",
                ),
            }
        )
        expected_columns = [
            [
                item["name"],
                item["declared_type"],
                1 if item["not_null"] else 0,
                item["primary_key_position"],
            ]
            for item in table.get("columns", [])
        ]
        cases.append(
            {
                "id": f"GEN.DB1.columns.{name}",
                "dimension": "DB1",
                "indicator": "DB1.1",
                "title": f"表 {name} 的列和类型符合目录",
                "type": "sql",
                "weight": 1.0,
                "steps": [
                    {
                        "sql": (
                            "SELECT name, type, \"notnull\", pk "
                            f'FROM pragma_table_info("{quoted}") ORDER BY cid'
                        ),
                        "expect": {"kind": "rows_equal", "value": expected_columns},
                    }
                ],
                "provenance": _candidate_provenance(
                    [source_id],
                    "候选结构断言来自 Schema 快照，但仍需与需求和数据设计基线核对。",
                ),
            }
        )
        primary_keys = [
            item["name"]
            for item in table.get("columns", [])
            if int(item.get("primary_key_position", 0)) > 0
        ]
        if primary_keys:
            partition = ", ".join(f'"{item.replace(chr(34), chr(34) * 2)}"' for item in primary_keys)
            cases.append(
                {
                    "id": f"GEN.DB1.pk.{name}",
                    "dimension": "DB1",
                    "indicator": "DB1.2",
                    "title": f"表 {name} 主键无重复",
                    "type": "sql",
                    "weight": 1.0,
                    "steps": [
                        {
                            "sql": (
                                f'SELECT COUNT(*) FROM (SELECT {partition} '
                                f'FROM "{quoted}" GROUP BY {partition} HAVING COUNT(*) > 1)'
                            ),
                            "expect": {"kind": "scalar", "value": 0},
                        }
                    ],
                    "provenance": _candidate_provenance(
                        [source_id],
                        "主键唯一性是通用完整性规则；现场结果只用于验证，不作为 Gold 来源。",
                    ),
                }
            )
        if table.get("foreign_keys"):
            cases.append(
                {
                    "id": f"GEN.DB1.fk.{name}",
                    "dimension": "DB1",
                    "indicator": "DB1.3",
                    "title": f"表 {name} 外键关联完整",
                    "type": "sql",
                    "weight": 1.0,
                    "steps": [
                        {
                            "sql": f'SELECT * FROM pragma_foreign_key_check("{quoted}")',
                            "expect": {"kind": "rows_equal", "value": []},
                        }
                    ],
                    "provenance": _candidate_provenance(
                        [source_id],
                        "外键检查是无违规预期；需要核对外键本身是否完整表达业务关系。",
                    ),
                }
            )
    cases.extend(
        [
            {
                "id": "GEN.DB8.integrity",
                "dimension": "DB8",
                "indicator": "DB8.1",
                "title": "数据库内部完整性检查",
                "type": "health",
                "check": "integrity",
                "expect": {"kind": "health_pass", "value": True},
                "provenance": _candidate_provenance(
                    [source_id],
                    "使用数据库原生完整性检查；该结果在 Gold 中只定义必须通过的通用健康规则。",
                ),
            },
            {
                "id": "GEN.DB8.foreign_keys",
                "dimension": "DB8",
                "indicator": "DB8.2",
                "title": "数据库关系健康检查",
                "type": "health",
                "check": "foreign_keys",
                "expect": {"kind": "health_pass", "value": True},
                "provenance": _candidate_provenance(
                    [source_id],
                    "外键健康是通用规则；需要人工确认数据库是否正确启用外键约束。",
                ),
            },
            {
                "id": "GEN.DB8.read_write",
                "dimension": "DB8",
                "indicator": "DB8.3",
                "title": "数据库基础读写可用",
                "type": "health",
                "check": "read_write",
                "expect": {"kind": "health_pass", "value": True},
                "provenance": _candidate_provenance(
                    [source_id],
                    "在事务探测中执行读写并回滚，验证基础可用性且不改变测试数据。",
                ),
            },
        ]
    )
    return cases


def _model_values(
    table: Dict[str, Any],
    entity: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    overrides = entity.get("create_values", {})
    values: Dict[str, Any] = {}
    primary_keys = {
        item["name"]
        for item in table.get("columns", [])
        if int(item.get("primary_key_position", 0)) > 0
    }
    for column in table.get("columns", []):
        name = str(column["name"])
        if name in overrides:
            values[name] = overrides[name]
        elif name in primary_keys:
            values[name] = 990000 + index
        elif column.get("not_null") and column.get("default") is None:
            values[name] = _column_value(str(table["name"]), column)
    return values


def generate_crud_case(
    table: Dict[str, Any],
    entity: Dict[str, Any],
    model_source_id: str,
    index: int,
) -> Dict[str, Any]:
    name = str(table["name"])
    quoted = name.replace('"', '""')
    values = _model_values(table, entity, index)
    if not values:
        raise ConfigurationError(f"实体 {name} 没有可生成的写入值")
    primary_keys = [
        item["name"]
        for item in table.get("columns", [])
        if int(item.get("primary_key_position", 0)) > 0
    ]
    update_column = entity.get("update_column")
    if not update_column:
        candidates = [item for item in table.get("columns", []) if item["name"] not in primary_keys]
        if not candidates:
            raise ConfigurationError(f"实体 {name} 没有非主键列可用于 UPDATE")
        update_column = candidates[0]["name"]
    update_column = str(update_column)
    if update_column not in values:
        values[update_column] = _column_value(name, next(
            item for item in table["columns"] if item["name"] == update_column
        ))
    current = values[update_column]
    if isinstance(current, (int, float)):
        updated_value = current + 2
    else:
        updated_value = f"{current}-updated"
    if "update_value" in entity:
        updated_value = entity["update_value"]

    columns = list(values)
    select_columns = ", ".join(f'"{item.replace(chr(34), chr(34) * 2)}"' for item in columns)
    placeholders = ", ".join(f":{item}" for item in columns)
    insert_columns = ", ".join(f'"{item.replace(chr(34), chr(34) * 2)}"' for item in columns)
    pk = primary_keys[0] if primary_keys else columns[0]
    pk_param = values[pk]
    expected_insert = [[values[item] for item in columns]]
    where = f'"{pk.replace(chr(34), chr(34) * 2)}" = :id_value'

    first_expected = [row.copy() for row in expected_insert]
    for row in first_expected:
        row[columns.index(update_column)] = updated_value
    return {
        "id": f"GEN.DB2.crud.{name}",
        "dimension": "DB2",
        "indicator": "DB2.1",
        "title": f"实体 {name} 的 CRUD 状态序列",
        "type": "sql",
        "weight": 1.0,
        "steps": [
            {
                "sql": f'INSERT INTO "{quoted}" ({insert_columns}) VALUES ({placeholders})',
                "params": values,
                "expect": {"kind": "affected_rows", "value": 1},
            },
            {
                "sql": f'SELECT {select_columns} FROM "{quoted}" WHERE {where}',
                "params": {"id_value": pk_param},
                "expect": {"kind": "rows_equal", "value": expected_insert},
            },
            {
                "sql": (
                    f'UPDATE "{quoted}" SET "{update_column.replace(chr(34), chr(34) * 2)}" '
                    f'= :updated_value WHERE {where}'
                ),
                "params": {"updated_value": updated_value, "id_value": pk_param},
                "expect": {"kind": "affected_rows", "value": 1},
            },
            {
                "sql": f'SELECT {select_columns} FROM "{quoted}" WHERE {where}',
                "params": {"id_value": pk_param},
                "expect": {"kind": "rows_equal", "value": first_expected},
            },
            {
                "sql": f'DELETE FROM "{quoted}" WHERE {where}',
                "params": {"id_value": pk_param},
                "expect": {"kind": "affected_rows", "value": 1},
            },
            {
                "sql": f'SELECT COUNT(*) FROM "{quoted}" WHERE {where}',
                "params": {"id_value": pk_param},
                "expect": {"kind": "scalar", "value": 0},
            },
        ],
        "provenance": _candidate_provenance(
            [model_source_id],
            (
                "基于人工或 LLM 提出后保存的实体契约生成写入、读取、更新和删除序列；"
                "不得以当前软件输出作为预期值，冻结前必须独立验证约束和业务含义。"
            ),
        ),
    }


def generate_draft(
    project: ProjectConfig,
    output_dir: Path,
    *,
    model_path: Optional[Path] = None,
) -> Dict[str, Any]:
    adapter = create_adapter(project)
    try:
        with tempfile.TemporaryDirectory(prefix="dbeval-generate-") as temporary:
            prepared = adapter.prepare(Path(temporary))
            schema = adapter.inspect_schema()
    finally:
        adapter.close()
    schema_hash = canonical_hash(schema)
    source_id = f"schema:{schema_hash}"
    cases = generate_schema_cases(schema, source_id)
    model_info = None
    if model_path:
        model = read_json(model_path.resolve())
        if model.get("schema_version") != "1.0":
            raise ConfigurationError("模型契约 schema_version 必须为 '1.0'")
        entities = model.get("entities")
        if not isinstance(entities, list):
            raise ConfigurationError("模型契约需要 entities 数组")
        model_source_id = f"model:{sha256_file(model_path.resolve())}"
        table_map = {str(item["name"]): item for item in schema["tables"]}
        for index, entity in enumerate(entities):
            table_name = str(entity.get("table", ""))
            if table_name not in table_map:
                raise ConfigurationError(f"模型契约引用不存在的表: {table_name}")
            cases.append(
                generate_crud_case(table_map[table_name], entity, model_source_id, index)
            )
        model_info = {
            "path": str(model_path.resolve()),
            "sha256": sha256_file(model_path.resolve()),
            "entity_count": len(entities),
        }

    raw = {
        "schema_version": "1.0",
        "bundle_id": f"{project.project_id}-generated-{utc_now()}",
        "software": project.raw["software"],
        "created_at": utc_now(),
        "created_by": "dbeval deterministic generator",
        "dimension_weights": project.raw.get(
            "dimension_weights", {f"DB{index}": 1.0 for index in range(1, 9)}
        ),
        "cases": cases,
        "generation": {
            "schema_hash": schema_hash,
            "schema_source_id": source_id,
            "model": model_info,
            "prepared": prepared,
            "status": "draft",
            "warning": "所有生成用例均为 candidate，必须独立验证后才能冻结。",
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "schema.json", schema)
    write_json(output_dir / "gold.json", raw)
    report = {
        "schema_version": "1.0",
        "case_count": len(cases),
        "indicators": sorted({item["indicator"] for item in cases}),
        "candidate_case_count": sum(
            1
            for item in cases
            if item.get("provenance", {}).get("validation_status") == "candidate"
        ),
        "requires_human_verification": True,
        "limitations": [
            "数据库应用业务语义无法仅从通用 Schema 可靠推导。",
            "生成的 CRUD 值可能不满足软件特有 CHECK 约束，需要通过实体契约覆盖。",
            "当前自动草稿不覆盖全部 DB1-DB8 指标，必须在 Gold 基线中补齐。",
        ],
    }
    write_json(output_dir / "generation-report.json", report)
    return report
