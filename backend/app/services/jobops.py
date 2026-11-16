"""流作业运维业务规则：状态流转、字段校验与筛选口径都收在这里。

作业异常计数（消费侧）只读流处理日志中心的共享日志处理数据结果：
- 已归档日志不再参与计数；同一份日志结果同一作业只消费一次，重复分析不会再次累加；
- 消费进度记在作业行的「已消费日志」里，兼容旧记录缺失该字段的情况。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "jobops"
LOG_MODULE = "log"
REQUIRED_FIELDS = ["实例编号", "作业名称", "运行节点"]
STATUS_ORDER = ["待启动", "运行中", "已暂停", "已停止"]
ACTION_RULES = {"启动作业": "待启动", "暂停作业": "运行中", "停止作业": "已暂停"}
NEGATIVE_ACTIONS = []
COUNT_FIELD = "异常次数"
CONSUMED_FIELD = "已消费日志"
CONCLUSION_FIELD = "日志处理结论"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


class JobopsService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("实例编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None
            self.sync_abnormal_count(entry)
            return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry[COUNT_FIELD] = _count(values.get(COUNT_FIELD))
        entry[CONSUMED_FIELD] = []
        entry[CONCLUSION_FIELD] = {}
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"作业实例 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于流作业运维可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"作业实例已{action}"

    def sync_abnormal_count(self, entry: dict[str, Any], *, current_key: str | None = None) -> int:
        """按共享日志处理数据结果累加异常计数，每份结果只消费一次。

        旧记录没有消费进度字段时，把已产生处理结论的日志视为历史已计入，
        只登记消费进度、不重复累加，避免升级后历史异常次数被抬高；
        本次触发的日志（current_key）不属于历史，仍按一份新结果计入。
        """
        with store.lock:
            return self._sync_abnormal_count(entry, current_key=current_key)

    def _sync_abnormal_count(self, entry: dict[str, Any], *, current_key: str | None = None) -> int:
        job_name = _text(entry.get("作业名称"))
        legacy = CONSUMED_FIELD not in entry
        conclusions_raw = entry.get(CONCLUSION_FIELD)
        conclusions = dict(conclusions_raw) if isinstance(conclusions_raw, dict) else {}
        consumed_raw = entry.get(CONSUMED_FIELD)
        consumed = [str(item) for item in consumed_raw] if isinstance(consumed_raw, list) else []
        total = _count(entry.get(COUNT_FIELD))
        for log in store.rows(LOG_MODULE):
            if _text(log.get("流作业名")) != job_name:
                continue
            # 已分析及之后的状态才产生处理结论；归档只是保留结论，不再参与计数。
            status = _text(log.get("status"))
            if status not in ("已分析", "已处理", "已归档"):
                continue
            conclusions[_text(log.get("记录编号"))] = {
                "处理状态": log.get("处理状态") or log.get("status"),
                "分析结论": log.get("分析结论", ""),
                "处理结论": log.get("处理结论", ""),
                "异常计数": _count(log.get("异常计数")),
            }
            key = f"{_text(log.get('记录编号'))}#{_count(log.get('异常计数'))}"
            if key in consumed or status == "已归档":
                continue
            consumed.append(key)
            if not legacy or key == current_key:
                total += _count(log.get("异常计数"))
        entry[CONSUMED_FIELD] = consumed
        entry[CONCLUSION_FIELD] = conclusions
        entry[COUNT_FIELD] = total
        return total
