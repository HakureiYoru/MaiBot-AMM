"""内存 SQLite 回归：只执行查询函数，不导入应用或访问运行数据。"""

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterator, List, NamedTuple, Optional, cast

import ast
import textwrap
import unittest

from sqlalchemy import Column, LargeBinary, event, func
from sqlmodel import Field, SQLModel, Session, col, create_engine, select


class UsageFixture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime
    request_type: str = "replyer.chat"
    model_api_provider_name: str = "provider"
    model_assign_name: Optional[str] = None
    model_name: str = "model"
    session_id: str = "test-session"
    prompt_tokens: int = 10
    completion_tokens: int = 5
    prompt_cache_enabled: bool = False
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    cost: float = 0.01
    time_cost: float = 1.0


class MessageFixture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime
    platform: str = "qq"
    user_id: str = "test-user"
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    user_nickname: Optional[str] = None
    raw_content: bytes = Field(default=b"", sa_column=Column(LargeBinary))
    additional_config: str = ""


def load_functions(root: Path, namespace: dict) -> dict:
    """提取待测原函数，隔离模块级数据库/配置初始化副作用。"""
    path = root / "src/services/statistics_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        "fetch_model_usage_since", "fetch_model_duration_aggregates_since",
        "StatisticsMessageRow", "fetch_messages_since",
    }
    selected = [node for node in tree.body if getattr(node, "name", None) in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class StatisticsMemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(self.engine)
        self.start = datetime(2026, 7, 1)
        self.queries = []
        event.listen(self.engine, "before_cursor_execute", self._capture_query)

        @contextmanager
        def session_factory(*, auto_commit=False):
            self.assertFalse(auto_commit)
            with Session(self.engine) as session:
                yield session
                self.assertEqual(len(session.identity_map), 0, "统计不应物化 ORM 实体")

        self.ns = load_functions(self.root, {
            "datetime": datetime, "Iterator": Iterator, "List": List,
            "NamedTuple": NamedTuple, "ModelUsage": UsageFixture,
            "Messages": MessageFixture, "get_db_session": session_factory,
            "select": select, "col": col, "func": func, "MODEL_USAGE_BATCH_SIZE": 1000,
        })

    def tearDown(self):
        self.engine.dispose()

    def _capture_query(self, connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            self.queries.append(statement)

    def add(self, *records):
        with Session(self.engine) as session:
            session.add_all(records)
            session.commit()

    def test_bounded_batches_preserve_boundary_and_freeze_upper_id(self):
        self.add(UsageFixture(timestamp=self.start - timedelta(seconds=1)),
                 *[UsageFixture(timestamp=self.start + timedelta(seconds=i), time_cost=float(i))
                   for i in range(5)])
        rows = self.ns["fetch_model_usage_since"](self.start, batch_size=2)
        first = next(rows)
        # 查询启动后新增的记录留到下一轮统计，避免游标永远追赶新数据。
        self.add(UsageFixture(timestamp=self.start, time_cost=99.0))
        result = [first, *rows]
        self.assertEqual([row["time_cost"] for row in result], [0., 1., 2., 3., 4.])
        self.assertTrue(all(row["session_id"] == "test-session" for row in result))
        detail_queries = [query for query in self.queries if "max(" not in query]
        self.assertEqual(len(detail_queries), 3)
        self.assertTrue(all("LIMIT" in query for query in detail_queries))

    def test_empty_and_invalid_batch(self):
        self.assertEqual(list(self.ns["fetch_model_usage_since"](self.start)), [])
        for size in (0, -1):
            with self.assertRaises(ValueError):
                list(self.ns["fetch_model_usage_since"](self.start, batch_size=size))

    def test_sql_duration_aggregate_and_caller_preserve_mean_and_std(self):
        self.add(UsageFixture(timestamp=self.start - timedelta(seconds=1), time_cost=100.),
                 *[UsageFixture(timestamp=self.start, time_cost=value) for value in (1., 3., 0., -1.)],
                 UsageFixture(timestamp=self.start, request_type="planner.plan", time_cost=2.,
                              model_assign_name=None, model_name="fallback"))
        rows = self.ns["fetch_model_duration_aggregates_since"](self.start)
        self.assertEqual(sum(row["count"] for row in rows), 3)
        path = self.root / "src/chat/utils/statistic.py"
        source = path.read_text(encoding="utf-8")
        task_class = next(node for node in ast.parse(source).body
                          if isinstance(node, ast.ClassDef) and node.name == "StatisticOutputTask")
        method = next(node for node in task_class.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_refresh_all_time_duration_stats")
        code = textwrap.dedent("\n".join(source.splitlines()[method.lineno - 1:method.end_lineno]))
        namespace = {"defaultdict": defaultdict, "cast": cast, "StatPeriodData": Dict,
                     "fetch_model_duration_aggregates_since": self.ns["fetch_model_duration_aggregates_since"]}
        data = {}
        for category in ("TYPE", "USER", "MODEL", "MODULE"):
            for prefix in ("AVG", "STD"):
                key = f"{prefix}_TIME_COST_BY_{category}"
                namespace[key] = key
                data[key] = defaultdict(float)
        exec(compile(code, str(path), "exec"), namespace)
        namespace["_refresh_all_time_duration_stats"](SimpleNamespace(all_time_start_time=self.start), data)
        self.assertEqual(data["AVG_TIME_COST_BY_TYPE"], {"replyer.chat": 2., "planner.plan": 2.})
        self.assertEqual(data["STD_TIME_COST_BY_TYPE"], {"replyer.chat": 1., "planner.plan": 0.})
        self.assertEqual(data["AVG_TIME_COST_BY_MODEL"], {"model": 2., "fallback": 2.})

    def test_message_projection_never_selects_large_content(self):
        self.add(MessageFixture(timestamp=self.start, raw_content=b"x" * 1024 * 1024,
                                additional_config="large", group_id="group", group_name="test"))
        rows = self.ns["fetch_messages_since"](self.start)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].group_name, "test")
        self.assertEqual(len(rows[0]), 6)
        self.assertNotIn("raw_content", self.queries[-1])
        self.assertNotIn("additional_config", self.queries[-1])


if __name__ == "__main__":
    unittest.main()
