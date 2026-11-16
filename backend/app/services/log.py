"""流处理日志中心业务规则：状态流转、原始日志保留、分析结论回写与筛选口径都收在这里。

一条日志在「日志处理数据结果」里只有一份共享记录（就是 store 里的这一行）：
- 原始日志字段（日志级别、日志内容、日志内容原文、异常类型、发生时间）登记后不再被动作覆盖；
- 处理状态只允许沿 已记录 → 已分析 → 已处理 → 已归档 前进，不能回退；
- 重复分析幂等：同一条日志的同一异常只生效一次，异常计数只增一次；
- 分析结论按流作业名回写到流作业运维的异常次数（消费侧），已归档的日志不再参与消费计数。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "log"
JOB_MODULE = "jobops"
REQUIRED_FIELDS = ["记录编号", "流作业名", "日志级别"]
STATUS_ORDER = ["已记录", "已分析", "已处理", "已归档"]
ACTION_RULES = {"分析日志": "已分析", "标记处理": "已处理", "归档日志": "已归档"}
# 原始日志字段：登记时冻结，后续任何动作都不得覆盖（兼容旧记录用 get 兜底）。
ORIGINAL_SNAPSHOT_FIELDS = {"原始日志级别": "日志级别", "原始日志内容": "日志内容", "原始异常类型": "异常类型"}
COUNT_FIELD = "异常计数"
LIST_FIELDS = ["记录编号", "流作业名", "日志级别", "日志内容", "发生时间", "异常类型", "处理状态", "记录状态"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _stage(entry: dict[str, Any]) -> int:
    """当前状态在状态序列里的位置；未知状态按「已记录」兜底，避免越级。"""
    status = _text(entry.get("status"))
    return STATUS_ORDER.index(status) if status in STATUS_ORDER else 0


class LogService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        record_code: str | None = None,
        job_name: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("记录编号", "")) or keyword in str(row.get("流作业名", ""))
            ]
        if record_code:
            rows = [row for row in rows if record_code in str(row.get("记录编号", ""))]
        if job_name:
            rows = [row for row in rows if job_name in str(row.get("流作业名", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._result(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            return self._result(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        with store.lock:
            # 同一记录编号只登记一份，重复登记返回已有记录而不是再插入一条。
            existing = self._by_code(_text(values.get("记录编号")))
            if existing is not None:
                return self._result(existing), []
            return self._create(values)

    def _create(self, values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in LIST_FIELDS:
            if field not in entry and values.get(field) is not None:
                entry[field] = values.get(field)
        entry["流作业名"] = _text(values.get("流作业名"))
        entry["记录状态"] = _text(values.get("记录状态")) or STATUS_ORDER[0]
        entry["处理状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = self._is_abnormal(entry)
        entry[COUNT_FIELD] = 0
        entry["分析结论"] = ""
        self._freeze_original(entry)
        rows.append(entry)
        return self._result(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            return self._run_action(entry_id, action)

    def _run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于流处理日志中心可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        current = _stage(entry)
        target_stage = STATUS_ORDER.index(target)
        # 旧记录可能没有补过原始字段，动作前先冻结，后续任何写入都不会再覆盖原文。
        self._freeze_original(entry)

        if target_stage <= current:
            if target_stage == current:
                return self._result(entry), self._repeat_message(entry, action)
            return None, (
                f"处理状态只能按 {' → '.join(STATUS_ORDER)} 推进，"
                f"当前「{entry.get('status')}」不能回退到「{target}」"
            )
        if target_stage > current + 1:
            next_status = STATUS_ORDER[current + 1]
            next_action = next(
                (name for name, stage in ACTION_RULES.items() if stage == next_status), ""
            )
            return None, (
                f"处理状态只能按 {' → '.join(STATUS_ORDER)} 逐级推进，"
                f"当前「{entry.get('status')}」需先执行「{next_action}」进入「{next_status}」"
            )

        counted = False
        if action == "分析日志":
            if _text(entry.get("分析动作")):
                # 重复分析只生效一次：原文不动、计数不增。
                return self._result(entry), self._repeat_message(entry, action)
            entry["分析动作"] = action
            entry["分析结论"] = self._analysis_conclusion(entry)
            entry[COUNT_FIELD] = _as_count(entry.get(COUNT_FIELD)) + (1 if self._is_abnormal(entry) else 0)
            counted = True
        elif action == "标记处理":
            entry["处理结论"] = "已处理"
        else:
            entry["处理结论"] = "已归档"

        entry["status"] = target
        entry["处理状态"] = target
        entry["记录状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = self._is_abnormal(entry)
        self._sync_consumer(entry, new_result=counted)
        return self._result(entry), f"日志记录已{action}"

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _sync_consumer(entry: dict[str, Any], *, new_result: bool) -> None:
        """跨流程链路同步：消费侧只依据这一份日志处理数据结果回写异常计数。

        new_result 只在本次真正完成分析时为真：其余动作只同步结论与消费进度，
        不会把同一条异常再算一遍。
        """
        from app.services.jobops import JobopsService

        job_service = JobopsService()
        job_name = _text(entry.get("流作业名"))
        current_key = (
            f"{_text(entry.get('记录编号'))}#{_as_count(entry.get(COUNT_FIELD))}" if new_result else None
        )
        for job in store.rows(JOB_MODULE):
            if _text(job.get("作业名称")) == job_name:
                job_service.sync_abnormal_count(job, current_key=current_key)

    def _by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if _text(row.get("记录编号")) == code:
                return row
        return None

    @staticmethod
    def _is_abnormal(entry: dict[str, Any]) -> bool:
        level = _text(entry.get("日志级别")).upper()
        return level in {"ERROR", "FATAL", "CRITICAL"} or bool(_text(entry.get("异常类型")))

    @staticmethod
    def _analysis_conclusion(entry: dict[str, Any]) -> str:
        level = _text(entry.get("日志级别"))
        kind = _text(entry.get("异常类型")) or "无异常类型"
        content = _text(entry.get("日志内容"))
        return f"{level or '未知级别'}/{kind}：{content or '内容为空'}"

    def _repeat_message(self, entry: dict[str, Any], action: str) -> str:
        return f"日志记录已处于「{entry.get('status')}」，重复{action}未重复生效，原始日志保持不变"

    @staticmethod
    def _freeze_original(entry: dict[str, Any]) -> None:
        """把原始日志冻结在独立字段里；已存在的快照永不覆盖。"""
        for snapshot, source in ORIGINAL_SNAPSHOT_FIELDS.items():
            if snapshot not in entry:
                entry[snapshot] = entry.get(source)

    @staticmethod
    def _result(entry: dict[str, Any]) -> dict[str, Any]:
        """日志处理数据结果：原始日志、处理状态、异常计数缺一不可，兼容旧字段。"""
        result = dict(entry)
        result.setdefault(COUNT_FIELD, 0)
        result.setdefault("分析结论", "")
        result.setdefault("处理状态", entry.get("status"))
        result.setdefault("记录状态", entry.get("status"))
        result["原始日志"] = {
            "记录编号": entry.get("记录编号"),
            "流作业名": entry.get("流作业名"),
            **{
                snapshot.removeprefix("原始"): entry.get(snapshot, entry.get(source))
                for snapshot, source in ORIGINAL_SNAPSHOT_FIELDS.items()
            },
        }
        return result
