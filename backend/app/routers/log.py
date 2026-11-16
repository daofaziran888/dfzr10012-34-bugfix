"""流处理日志中心接口：维护日志记录，覆盖分析日志、标记处理、归档日志等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log import LogService

router = APIRouter(prefix="/api/log", tags=["流处理日志中心"])

service = LogService()

LIST_FIELDS = ["记录编号", "流作业名", "日志级别", "日志内容", "发生时间", "异常类型", "处理状态", "记录状态"]
STATUSES = ["已记录", "已分析", "已处理", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号或流作业名检索"),
    记录编号: str | None = Query(default=None, description="按记录编号检索"),
    流作业名: str | None = Query(default=None, description="按流作业名检索"),
    status: str | None = Query(default=None, description="已记录、已分析、已处理、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤流处理日志中心列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        record_code=记录编号,
        job_name=流作业名,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出流处理日志中心清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "log", "total": total, "items": items}


@router.get("/{entry_id}/result", response_model=dict)
def get_result(entry_id: int) -> dict:
    """日志处理数据结果：保留原始日志、处理状态与异常计数，供列表和详情侧栏共用。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志记录 {entry_id} 不存在或已归档")
    return entry

@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条日志记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="日志记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条日志记录执行分析日志、标记处理、归档日志；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
