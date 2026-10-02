"""桥梁定检·检测记录会签桌接口。

围绕桥面剖切图推进：检查员录入检测编号/桥梁名称、放置病害证据、提交会签；
主管选择限载结论；会签通过后才生成工程任务。支持撤回续届、凭会签号恢复
签认队列，以及随结论更新的风险看板。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.countersign import CountersignService

router = APIRouter(prefix="/api/countersign", tags=["检测记录会签桌"])

service = CountersignService()

LIST_COLUMNS = ["会议全称", "检测编号", "桥梁名称", "检查员", "主管", "限载结论", "技术等级", "风险等级", "评分冲突"]
STATUSES = ["草稿", "待主管会签", "已会签", "已生成任务", "已撤回"]


@router.get("", response_model=PageResult[dict])
def list_meetings(
    keyword: str | None = Query(default=None, description="按会签号或检测编号检索"),
    status: str | None = Query(default=None, description="草稿、待主管会签、已会签、已生成任务、已撤回"),
    bridge: str | None = Query(default=None, description="按桥梁名称或桥位编号检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出会签会议；撤回后的各届次都会保留，按会签号前缀可查全链路。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_meetings(keyword=keyword, status=status, bridge=bridge, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/risk-board")
def risk_board() -> dict[str, Any]:
    """风险看板：限载结论分布、风险等级、高风险桥位、在途签认队列。"""
    return service.risk_board()


@router.get("/queue")
def queue_snapshot(code: str = Query(..., description="会签号，如 CSD-2026-0003 或 CSD-2026-0005-R2")) -> dict[str, Any]:
    """连接中断后凭会签号查看队列处理情况。"""
    snapshot, message = service.queue_snapshot(code)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, **snapshot}


@router.post("/queue/resume", response_model=ActionResult)
def resume_queue(payload: EntryPayload) -> ActionResult:
    """凭会签号恢复签认：把断线前的决定（带同一请求号）重放，幂等不重复落结论。"""
    values = payload.values
    code = str(values.get("会签号") or values.get("会议全称") or "").strip()
    request_id = str(values.get("request_id") or values.get("请求号") or "").strip()
    if not code:
        return ActionResult(ok=False, message="请提供要恢复的会签号")
    meeting, message, applied = service.sign_by_code(code, values, request_id=request_id or None)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=applied, message=message, entry=meeting)


@router.get("/export")
def export_meetings() -> dict[str, Any]:
    items, total = service.list_meetings(page=1, size=10000)
    return {"module": "countersign", "total": total, "items": items, "risk_board": service.risk_board()}


@router.get("/{meeting_id}", response_model=dict)
def get_meeting(meeting_id: int) -> dict[str, Any]:
    meeting = service.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"会签 {meeting_id} 不存在")
    return meeting


@router.post("", response_model=ActionResult)
def create_meeting(payload: EntryPayload) -> ActionResult:
    """检查员建会：录入检测编号与桥梁名称，桥面剖切图随即作为主对象打开。"""
    meeting, missing = service.create_meeting(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"会签 {meeting['会议全称']} 已创建", entry=meeting)


@router.post("/{meeting_id}/evidence", response_model=ActionResult)
def place_evidence(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """在桥面剖切图上放置一处病害证据（x/y 为剖切图百分比坐标）。"""
    meeting, message = service.place_evidence(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="病害证据已放置到剖切图", entry=meeting)


@router.post("/{meeting_id}/inspection", response_model=ActionResult)
def update_inspection(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """草稿阶段补充桥位编号、日常评分等检测信息。"""
    meeting, message = service.update_inspection(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="检测信息已更新", entry=meeting)


@router.post("/{meeting_id}/submit", response_model=ActionResult)
def submit_for_sign(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """检查员提交会签：剖切图上至少有一处病害证据，之后进入主管阶段。"""
    meeting, message = service.submit_for_sign(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"{meeting['会议全称']} 已提交主管会签", entry=meeting)


@router.post("/{meeting_id}/sign", response_model=ActionResult)
def sign_meeting(
    meeting_id: int,
    payload: EntryPayload,
    request_id: str | None = Query(default=None, description="客户端生成的请求号，断网重传保持幂等"),
) -> ActionResult:
    """主管选择限载结论并签认；事务锁保证同一会只放行一份决定。"""
    meeting, message, applied = service.sign(meeting_id, payload.values, request_id=request_id)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=applied, message=message, entry=meeting)


@router.post("/{meeting_id}/task", response_model=ActionResult)
def create_task(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """已会签的会议最后一步：生成工程任务并同步工程清单。"""
    meeting, message = service.create_task(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=meeting)


@router.post("/{meeting_id}/withdraw", response_model=ActionResult)
def withdraw_meeting(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """撤回不回退状态图：旧会议置「已撤回」，同会签号届次 +1 生成新会议。"""
    meeting, message = service.withdraw(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=meeting)
