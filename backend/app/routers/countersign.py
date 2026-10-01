"""检测记录会签桌接口。

以桥面剖切图为主对象，按「检查员录入 → 放置病害证据 → 主管限载结论 → 生成工程任务」
单向推进；撤回只能生成新一轮会签。会签结果由服务层同步到定检档案、巡检通知、
工程清单与既有桥位，风险看板随结论刷新。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.countersign import CONCLUSIONS, SECTION_LAYERS, service

router = APIRouter(prefix="/api/countersign", tags=["检测记录会签桌"])

STATES = ["待证据", "待结论", "已会签", "已派单", "已撤回"]


@router.get("", response_model=PageResult[dict])
def list_meetings(
    keyword: str | None = Query(default=None, description="按会签号、检测编号或桥梁名称检索"),
    status: str | None = Query(default=None, description="待证据、待结论、已会签、已派单、已撤回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """会签桌会议列表，默认按最新会议在前。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATES:
        raise HTTPException(status_code=400, detail=f"状态必须是：{'、'.join(STATES)}")
    items, total = service.list_meetings(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/risk-board")
def risk_board() -> dict[str, Any]:
    """风险看板：随主管会签结论实时汇总风险分布、交通管制与专项覆盖日常数量。"""
    return service.risk_board()


@router.get("/recover")
def recover_queue(
    meeting_no: str = Query(..., alias="会签号", description="连接中断前持有的会签号"),
    token: str | None = Query(default=None, description="签认令牌，带上后回放该份签认的去向"),
) -> dict[str, Any]:
    """连接中断后凭会签号恢复等待队列；带令牌时恢复本人签认结果。"""
    meeting, message, data = service.recover_queue(meeting_no, token)
    if meeting is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "meeting": meeting, "recovery": data}


@router.get("/export")
def export_meetings() -> dict[str, Any]:
    """导出会签桌全量会议及其单向流转结果。"""
    items, total = service.list_meetings(page=1, size=10000)
    return {"module": "countersign", "total": total, "items": items}


@router.get("/meta")
def meta() -> dict[str, Any]:
    """前端渲染所需的固定口径：限载结论、剖切图层位、状态序列。"""
    return {"conclusions": CONCLUSIONS, "layers": SECTION_LAYERS, "states": STATES}


@router.get("/{meeting_id}", response_model=dict)
def get_meeting(meeting_id: int) -> dict[str, Any]:
    """读取单次会签（含剖切图证据、等待队列、同步落点与流转历史）。"""
    meeting = service.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"会签 {meeting_id} 不存在")
    return meeting


@router.post("", response_model=ActionResult)
def open_meeting(payload: EntryPayload) -> ActionResult:
    """检查员录入检测编号与桥梁名称，开启一轮会签（会签号由服务端发放）。"""
    meeting, missing = service.open_meeting(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"会签桌已开启，会签号 {meeting['会签号']}", entry=meeting)


@router.post("/{meeting_id}/evidence", response_model=ActionResult)
def add_evidence(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """检查员在桥面剖切图上放置一份病害证据（部位、病害类型、图面坐标）。"""
    meeting, message = service.add_evidence(meeting_id, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=meeting)


@router.post("/{meeting_id}/actions", response_model=ActionResult)
def run_action(meeting_id: int, payload: EntryPayload) -> ActionResult:
    """会签桌单向动作：检查员提交证据 / 主管生成工程任务 / 撤回并新会签。"""
    action = str(payload.values.get("action") or "").strip()
    meeting, message, extra = service.run_action(meeting_id, action, payload.values)
    if meeting is None:
        return ActionResult(ok=False, message=message)
    result = ActionResult(ok=True, message=message, entry=meeting)
    if extra:
        result.message = message
    return result


@router.post("/{meeting_id}/decision")
def submit_decision(meeting_id: int, payload: EntryPayload) -> dict[str, Any]:
    """主管选择限载结论并签认。

    并发请求按会议级事务锁串行：只放行一份决定，其余进入等待队列；
    同一签认令牌重发为幂等回放，连接中断后可用 GET /recover 恢复。
    """
    meeting, message, data = service.submit_decision(meeting_id, payload.values)
    if meeting is None:
        raise HTTPException(status_code=400, detail=message)
    ok = data is not None and data.get("state") == "放行决定"
    return {"ok": ok, "message": message, "meeting": meeting, "decision": data}
