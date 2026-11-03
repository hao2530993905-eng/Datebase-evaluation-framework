"""SQLite adapter implemented with the Python standard library."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import AdapterError, EnvironmentError
from ..util import percentile, sha256_file
from .base import AdapterCapabilities, DatabaseAdapter, DatabaseIdentity, DatabaseSession


def _normalize_row(row: sqlite3.Row) -> List[Any]:
    return [row[index] for index in range(len(row))]


class SQLiteAdapter(DatabaseAdapter):
    def __init__(self, project):
        self.project = project
        self.work_dir: Optional[Path] = None
        self.template_path: Optional[Path] = None
        self.prepared_info: Dict[str, Any] = {}

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            sql_execution=True,
            transactions=True,
            concurrency=True,
            native_backup_restore=True,
            internal_integrity_check=True,
            foreign_key_check=True,
            read_write_probe=True,
            schema_introspection=True,
        )

    def _source_path(self) -> Optional[Path]:
        return self.project.database_path

    def _configured_initialize(self) -> Dict[str, Any]:
        value = self.project.database.get("initialize", {})
        return value if isinstance(value, dict) else {}

    def _connect(self, path: Path, *, readonly: bool = False) -> sqlite3.Connection:
        uri = f"file:{path}?mode=ro&cache=private" if readonly else f"file:{path}?mode=rwc"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=float(self.project.execution.get("busy_timeout_seconds", 5)),
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {int(float(self.project.execution.get('busy_timeout_seconds', 5)) * 1000)}"
        )
        return connection

    def prepare(self, work_dir: Path) -> Dict[str, Any]:
        self.work_dir = work_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        template = self.work_dir / "template.sqlite3"
        initialize = self._configured_initialize()
        source = self._source_path()
        source_hash = None

        if initialize.get("schema") or initialize.get("seed"):
            if template.exists():
                template.unlink()
            connection = sqlite3.connect(str(template))
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                for key in ("schema", "seed"):
                    filename = initialize.get(key)
                    if filename:
                        from ..util import resolve_path

                        path = resolve_path(self.project.root, str(filename))
                        with path.open("r", encoding="utf-8") as handle:
                            connection.executescript(handle.read())
                connection.commit()
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise EnvironmentError(
                        f"初始化数据库完整性检查失败: {list(result) if result else 'no result'}"
                    )
            finally:
                connection.close()
            source_kind = "initialized_template"
            source_hash = sha256_file(template)
        else:
            if source is None or not source.is_file():
                raise EnvironmentError(f"SQLite 数据库文件不存在: {source}")
            source_hash = sha256_file(source)
            # Use SQLite's online backup API rather than copying a potentially
            # live database in a way that can omit WAL content.
            source_connection = self._connect(source, readonly=True)
            target_connection = sqlite3.connect(str(template))
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
                source_connection.close()
            source_kind = "online_copy"

        self.template_path = template
        self.prepared_info = {
            "source_kind": source_kind,
            "source_sha256": source_hash,
            "isolated_template_sha256": sha256_file(template),
            "configured_source_path": str(source) if source else None,
        }
        return self.prepared_info

    def clone_case_database(self, case_dir: Path) -> Path:
        if self.template_path is None:
            raise AdapterError("适配器尚未 prepare")
        case_dir.mkdir(parents=True, exist_ok=True)
        database = case_dir / "case.sqlite3"
        source_connection = self._connect(self.template_path, readonly=True)
        target_connection = sqlite3.connect(str(database))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        return database

    def open_session(self, case_dir: Path) -> "SQLiteSession":
        path = self.clone_case_database(case_dir)
        return SQLiteSession(
            path,
            busy_timeout=float(self.project.execution.get("busy_timeout_seconds", 5)),
        )

    def detect(self) -> DatabaseIdentity:
        source = self._source_path()
        metadata: Dict[str, Any] = {
            "python_sqlite_module": sqlite3.sqlite_version,
            "source_exists": bool(source and source.is_file()),
        }
        if source and source.is_file():
            try:
                with source.open("rb") as handle:
                    header = handle.read(16)
                metadata["file_header"] = header.decode("ascii", errors="replace")
                metadata["is_sqlite_file"] = header == b"SQLite format 3\x00"
            except OSError as exc:
                metadata["read_error"] = str(exc)
        return DatabaseIdentity(
            backend="sqlite",
            version=sqlite3.sqlite_version,
            location=str(source) if source else "temporary initialized database",
            metadata=metadata,
        )

    def inspect_schema(self) -> Dict[str, Any]:
        if self.template_path is None:
            raise AdapterError("适配器尚未 prepare")
        connection = self._connect(self.template_path, readonly=True)
        tables: List[Dict[str, Any]] = []
        try:
            table_rows = connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for row in table_rows:
                name = str(row["name"])
                columns = []
                for column in connection.execute(f'PRAGMA table_info("{name}")').fetchall():
                    columns.append(
                        {
                            "name": column["name"],
                            "declared_type": column["type"],
                            "not_null": bool(column["notnull"]),
                            "primary_key_position": int(column["pk"]),
                            "default": column["dflt_value"],
                        }
                    )
                foreign_keys = []
                for foreign_key in connection.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
                    foreign_keys.append(
                        {
                            "id": foreign_key["id"],
                            "from": foreign_key["from"],
                            "table": foreign_key["table"],
                            "to": foreign_key["to"],
                            "on_update": foreign_key["on_update"],
                            "on_delete": foreign_key["on_delete"],
                        }
                    )
                indexes = []
                for index in connection.execute(f'PRAGMA index_list("{name}")').fetchall():
                    index_name = str(index["name"])
                    columns_in_index = [
                        item["name"]
                        for item in connection.execute(
                            f'PRAGMA index_info("{index_name}")'
                        ).fetchall()
                    ]
                    indexes.append(
                        {
                            "name": index_name,
                            "unique": bool(index["unique"]),
                            "origin": index["origin"],
                            "columns": columns_in_index,
                        }
                    )
                foreign_key_violations = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM pragma_foreign_key_check("{name}")'
                    ).fetchone()[0]
                )
                tables.append(
                    {
                        "name": name,
                        "sql": row["sql"],
                        "columns": columns,
                        "foreign_keys": foreign_keys,
                        "indexes": indexes,
                        "foreign_key_violations": foreign_key_violations,
                    }
                )
            return {
                "backend": "sqlite",
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "tables": tables,
            }
        finally:
            connection.close()

    def tool_versions(self) -> Dict[str, Optional[str]]:
        values: Dict[str, Optional[str]] = {"python_sqlite": sqlite3.sqlite_version}
        executable = shutil.which("sqlite3")
        values["sqlite3_cli_path"] = executable
        if executable:
            try:
                completed = subprocess.run(
                    [executable, "--version"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                values["sqlite3_cli"] = (completed.stdout or completed.stderr).strip()
            except (OSError, subprocess.SubprocessError):
                values["sqlite3_cli"] = None
        return values


class SQLiteSession(DatabaseSession):
    def __init__(self, path: Path, *, busy_timeout: float = 5.0):
        self.path = path.resolve()
        self.busy_timeout = busy_timeout
        self.connection = self._new_connection()
        try:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.DatabaseError:
            # Read-only databases cannot enter WAL mode; real operation will
            # expose read-only semantics where a case expects writes.
            pass

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout * 1000)}")
        return connection

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        started = time.perf_counter()
        cursor = self.connection.execute(sql, params or {})
        if cursor.description:
            rows = [_normalize_row(row) for row in cursor.fetchall()]
            kind = "query"
        else:
            rows = []
            kind = "command"
        if kind == "command" and not self.connection.in_transaction:
            self.connection.commit()
        return {
            "kind": kind,
            "rows": rows,
            "row_count": len(rows),
            "affected_rows": cursor.rowcount,
            "lastrowid": cursor.lastrowid,
            "duration_ms": (time.perf_counter() - started) * 1000.0,
        }

    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[List[Any]]:
        cursor = self.connection.execute(sql, params or {})
        return [_normalize_row(row) for row in cursor.fetchall()]

    def benchmark(
        self,
        sql: str,
        *,
        warmup: int,
        repeat: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if warmup < 0 or repeat <= 0:
            raise ValueError("benchmark 参数必须满足 warmup >= 0 且 repeat > 0")
        for _ in range(warmup):
            cursor = self.connection.execute(sql, params or {})
            cursor.fetchall()

        latencies: List[float] = []
        errors = 0
        started = time.perf_counter()
        for _ in range(repeat):
            operation_started = time.perf_counter()
            try:
                cursor = self.connection.execute(sql, params or {})
                cursor.fetchall()
            except sqlite3.Error:
                errors += 1
            latencies.append((time.perf_counter() - operation_started) * 1000.0)
        elapsed = time.perf_counter() - started
        successful = len(latencies) - errors
        return {
            "repeat": repeat,
            "warmup": warmup,
            "error_count": errors,
            "elapsed_seconds": elapsed,
            "throughput_ops_s": successful / elapsed if elapsed > 0 else 0.0,
            "mean_latency_ms": sum(latencies) / len(latencies),
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "p99_latency_ms": percentile(latencies, 0.99),
            "max_latency_ms": max(latencies),
        }

    def backup_restore(self, destination: Path) -> Dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        restored = sqlite3.connect(str(destination))
        started = time.perf_counter()
        try:
            self.connection.backup(restored)
            elapsed = time.perf_counter() - started
            integrity = restored.execute("PRAGMA integrity_check").fetchone()
        finally:
            restored.close()
        return {
            "status": "ok" if integrity and integrity[0] == "ok" else "corrupt",
            "backup_path": str(destination),
            "backup_sha256": sha256_file(destination),
            "backup_seconds": elapsed,
        }

    def query_backup(
        self,
        backup_path: Path,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[List[Any]]:
        connection = sqlite3.connect(str(backup_path))
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(sql, params or {})
            return [_normalize_row(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def health_probe(self, check: str) -> Dict[str, Any]:
        started = time.perf_counter()
        if check == "integrity":
            rows = self.query("PRAGMA integrity_check")
            passed = rows == [["ok"]]
            return {
                "check": check,
                "passed": passed,
                "details": rows,
                "duration_ms": (time.perf_counter() - started) * 1000.0,
            }
        if check == "foreign_keys":
            rows = self.query("PRAGMA foreign_key_check")
            return {
                "check": check,
                "passed": not rows,
                "details": rows,
                "duration_ms": (time.perf_counter() - started) * 1000.0,
            }
        if check == "read_write":
            try:
                tables = self.query(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
                )
                read_ok = True
                if tables:
                    self.query(f'SELECT * FROM "{tables[0][0]}" LIMIT 1')
                self.connection.execute("SAVEPOINT dbeval_rw_probe")
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS __dbeval_rw_probe "
                    "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                self.connection.execute(
                    "INSERT INTO __dbeval_rw_probe(value) VALUES ('probe')"
                )
                self.connection.execute("ROLLBACK TO dbeval_rw_probe")
                self.connection.execute("RELEASE dbeval_rw_probe")
                return {
                    "check": check,
                    "passed": read_ok,
                    "details": {"read": read_ok, "write": True, "rolled_back": True},
                    "duration_ms": (time.perf_counter() - started) * 1000.0,
                }
            except sqlite3.Error as exc:
                try:
                    self.connection.execute("ROLLBACK TO dbeval_rw_probe")
                    self.connection.execute("RELEASE dbeval_rw_probe")
                except sqlite3.Error:
                    self.connection.rollback()
                return {
                    "check": check,
                    "passed": False,
                    "details": {"error": str(exc)},
                    "duration_ms": (time.perf_counter() - started) * 1000.0,
                }
        raise ValueError(f"未知 health check: {check}")

    def concurrent_workers(
        self,
        worker_sql: str,
        *,
        workers: int,
        iterations: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import threading

        barrier = threading.Barrier(workers)
        failures: List[str] = []
        lock = threading.Lock()
        started = time.perf_counter()

        def worker(worker_id: int) -> None:
            connection = self._new_connection()
            try:
                barrier.wait(timeout=self.busy_timeout)
                for iteration in range(iterations):
                    local_params = dict(params or {})
                    local_params.update({"worker": worker_id, "iteration": iteration})
                    try:
                        connection.execute(worker_sql, local_params)
                        connection.commit()
                    except sqlite3.Error as exc:
                        with lock:
                            failures.append(f"worker={worker_id} iteration={iteration}: {exc}")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=self.busy_timeout + 30)
        elapsed = time.perf_counter() - started
        alive = [thread.name for thread in threads if thread.is_alive()]
        if alive:
            failures.append(f"超时未结束线程: {', '.join(alive)}")
        operations = workers * iterations
        return {
            "workers": workers,
            "iterations": iterations,
            "operations": operations,
            "failure_count": len(failures),
            "failures": failures[:50],
            "elapsed_seconds": elapsed,
            "throughput_ops_s": (operations - len(failures)) / elapsed if elapsed > 0 else 0.0,
        }

    def export_environment(self) -> Dict[str, str]:
        return {
            "DBEVAL_DB_PATH": str(self.path),
            "DBEVAL_CASE_DIR": str(self.path.parent),
        }

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
