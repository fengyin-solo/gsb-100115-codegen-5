"""检测记录会签桌业务规则。

会签桌以桥面剖切图为主对象：检查员先录入检测编号与桥梁名称、在剖切图上放置病害证据，
主管再选择限载结论，最后才生成工程任务。

状态图是单向的：

    待证据 ──检查员提交证据──▶ 待结论 ──主管签认──▶ 已会签 ──生成工程任务──▶ 已派单
                                   │                    │
                                   └──── 撤回只能生成新会议（旧会议进「已撤回」终态）

会签结论在主管签认时同步到定检档案（bridge）与巡检通知（patrol），
派工时同步到工程清单（project）；既有桥位（bridge_info）继续展示历史评定等级，
另挂专项会签等级。专项评分与日常评分分档冲突时，以专项会签结论为准。

多人同时签认时，每个会议一把事务锁，锁只放行一份决定，其余签认进入等待队列；
连接中断后凭「会签号 + 签认令牌」恢复队列与最终结果。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "countersign"

STATES = ["待证据", "待结论", "已会签", "已派单"]
WITHDRAWN_STATE = "已撤回"

# 主管可选择的限载结论：结论即决定，风险看板随结论更新
CONCLUSIONS = ["正常通行", "限速通行", "限载通行", "停用交通管制", "封闭重建"]
CONCLUSION_GRADE = {
    "正常通行": "1类",
    "限速通行": "2类",
    "限载通行": "3类",
    "停用交通管制": "4类",
    "封闭重建": "5类",
}
CONCLUSION_RISK = {
    "正常通行": "低",
    "限速通行": "中",
    "限载通行": "较高",
    "停用交通管制": "高",
    "封闭重建": "极高",
}
RISK_ORDER = {"低": 0, "中": 1, "较高": 2, "高": 3, "极高": 4}
HIGH_RISKS = {"较高", "高", "极高"}

PROJECT_TYPE = {
    "正常通行": "日常保养工程",
    "限速通行": "小修工程",
    "限载通行": "限载中修工程",
    "停用交通管制": "加固大修工程",
    "封闭重建": "拆除重建工程",
}
PATROL_MEASURE = {
    "正常通行": "按日常频次巡查，定期复测",
    "限速通行": "加密巡查频次，现场布设限速标志",
    "限载通行": "布设限载标志，监控并劝返超限车辆",
    "停用交通管制": "实施交通管制，安排值守与变形监测",
    "封闭重建": "封闭交通，启动重建应急预案",
}
INFO_CONTROL = {
    "正常通行": "正常",
    "限速通行": "限速",
    "限载通行": "限载",
    "停用交通管制": "停用",
    "封闭重建": "重建",
}

# 桥面剖切图的分层（自上而下），放置证据时按部位给出默认纵向位置
SECTION_LAYERS = ["桥面铺装", "防水层", "桥面板", "主梁", "支座", "墩台盖梁"]
LAYER_Y = {"桥面铺装": 14, "防水层": 19, "桥面板": 28, "主梁": 48, "支座": 63, "墩台盖梁": 78}

DECISION_HOLD_SECONDS = 0.25  # 放大并发窗口：决定落库前的临界区持锁时间

REQUIRED_OPEN_FIELDS = ["检测编号", "桥梁名称"]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def grade_of_score(score: float) -> str:
    """技术状况评分分档（JTG 桥梁技术状况评定口径的简化版）。"""
    if score >= 90:
        return "1类"
    if score >= 80:
        return "2类"
    if score >= 70:
        return "3类"
    if score >= 60:
        return "4类"
    return "5类"


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class CountersignService:
    def __init__(self) -> None:
        # 结构锁保护会议表与派生数据；每个会议另持一把事务锁，保证只放行一份决定
        self._global = threading.RLock()
        self._meeting_locks: dict[int, threading.Lock] = {}

    # ------------------------------------------------------------------ 基础查询

    def list_meetings(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("会签号", ""))
                or keyword in str(row.get("检测编号", ""))
                or keyword in str(row.get("桥梁名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows = sorted(rows, key=lambda row: int(row["id"]), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_meeting(self, meeting_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, meeting_id)

    def find_by_no(self, meeting_no: str) -> dict[str, Any] | None:
        meeting_no = meeting_no.strip()
        for row in store.rows(MODULE):
            if str(row.get("会签号", "")) == meeting_no:
                return row
        return None

    # ------------------------------------------------------------------ 检查员：发起会议/放置证据

    def open_meeting(
        self, values: dict[str, Any], *, supersedes: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_OPEN_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing

        with self._global:
            rows = store.rows(MODULE)
            meeting = {
                "id": max((int(r["id"]) for r in rows), default=0) + 1,
                "会签号": self._next_meeting_no(),
                "检测编号": str(values["检测编号"]).strip(),
                "桥梁名称": str(values["桥梁名称"]).strip(),
                "检查员": str(values.get("检查员") or "值班检查员").strip() or "值班检查员",
                "专项评分": _to_float(values.get("专项评分")) or (supersedes or {}).get("专项评分"),
                "日常评分": _to_float(values.get("日常评分")) or (supersedes or {}).get("日常评分"),
                "evidence": [],
                "status": STATES[0],
                "pending": True,
                "abnormal": False,
                "conclusion": None,
                "限载结论": None,
                "评定等级": None,
                "专项建议等级": None,
                "日常评分等级": None,
                "等级冲突": False,
                "风险": None,
                "decision_by": None,
                "decided_at": None,
                "dispatched_at": None,
                "deciding_token": None,
                "queue": [],
                "links": {},
                "supersedes": (supersedes or {}).get("会签号"),
                "history": [],
            }
            if meeting["专项评分"] is not None:
                meeting["专项建议等级"] = grade_of_score(meeting["专项评分"])
            if meeting["日常评分"] is not None:
                meeting["日常评分等级"] = grade_of_score(meeting["日常评分"])
            if supersedes:
                meeting["links"]["source_meeting_id"] = supersedes["id"]
                meeting["history"].append(
                    {"at": now_str(), "事件": f"撤回会签 {supersedes['会签号']} 后生成的新一轮会签，证据需重新放置"}
                )
            meeting["history"].append(
                {"at": now_str(), "事件": f"检查员 {meeting['检查员']} 录入检测编号与桥梁名称，会签桌开启"}
            )
            rows.append(meeting)
            self._meeting_locks[meeting["id"]] = threading.Lock()
            return meeting, []

    def add_evidence(self, meeting_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        role = str(values.get("角色") or "检查员").strip()
        if role != "检查员":
            return None, "只有检查员可以在桥面剖切图上放置病害证据"
        part = str(values.get("部位") or "").strip()
        defect = str(values.get("病害类型") or "").strip()
        if not part or not defect:
            return None, "病害证据需要指明剖切图部位与病害类型"
        if part not in SECTION_LAYERS:
            return None, f"部位「{part}」不在桥面剖切图层位内"
        with self._global:
            meeting = store.find(MODULE, meeting_id)
            if meeting is None:
                return None, f"会签 {meeting_id} 不存在"
            if meeting["status"] != "待证据":
                return None, f"会签当前为「{meeting['status']}」，证据只能在检查员阶段放置"
            x = _to_float(values.get("x"))
            y = _to_float(values.get("y"))
            evidence = {
                "id": f"EV-{len(meeting['evidence']) + 1:03d}",
                "部位": part,
                "病害类型": defect,
                "说明": str(values.get("说明") or "").strip(),
                "x": min(max(x if x is not None else 50.0, 0.0), 100.0),
                "y": min(max(y if y is not None else float(LAYER_Y[part]), 0.0), 100.0),
                "照片": str(values.get("照片") or "").strip() or f"{part}-{defect}.jpg",
                "placed_by": str(values.get("signer") or meeting["检查员"]).strip(),
                "at": now_str(),
            }
            meeting["evidence"].append(evidence)
            meeting["history"].append(
                {"at": now_str(), "事件": f"检查员在{part}放置病害证据：{defect}（{evidence['id']}）"}
            )
            return meeting, f"病害证据 {evidence['id']} 已放置到桥面剖切图"

    # ------------------------------------------------------------------ 状态动作

    def run_action(
        self, meeting_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        values = values or {}
        if action == "检查员提交证据":
            return self._submit_evidence(meeting_id, values)
        if action == "生成工程任务":
            return self._dispatch_project(meeting_id, values)
        if action == "撤回并新会签":
            return self._withdraw_to_new_meeting(meeting_id, values)
        return None, f"动作「{action}」不属于会签桌可执行范围", None

    def _submit_evidence(
        self, meeting_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        role = str(values.get("角色") or "检查员").strip()
        if role != "检查员":
            return None, "证据归集由检查员完成，主管不能代为提交", None
        with self._global:
            meeting = store.find(MODULE, meeting_id)
            if meeting is None:
                return None, f"会签 {meeting_id} 不存在", None
            if meeting["status"] != "待证据":
                return None, f"当前为「{meeting['status']}」，证据只能提交一次", None
            if not meeting["evidence"]:
                return None, "剖切图上还没有病害证据，不能提交给主管", None
            meeting["status"] = "待结论"
            meeting["history"].append(
                {"at": now_str(), "事件": f"检查员提交 {len(meeting['evidence'])} 份病害证据，等待主管选择限载结论"}
            )
            return meeting, "证据已提交，会签进入主管结论阶段", None

    def _dispatch_project(
        self, meeting_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        role = str(values.get("角色") or "主管").strip()
        if role != "主管":
            return None, "工程任务由主管在会签结论后派发", None
        with self._meeting_lock(meeting_id), self._global:
            meeting = store.find(MODULE, meeting_id)
            if meeting is None:
                return None, f"会签 {meeting_id} 不存在", None
            if meeting["status"] == "已派单":
                return None, "工程任务已经生成，会签桌单向流转不可重复派单", None
            if meeting["status"] != "已会签":
                return None, f"当前为「{meeting['status']}」，必须先完成主管会签才能生成工程任务", None
            project_id = self._sync_project(meeting)
            self._mark_archive(meeting)
            meeting["status"] = "已派单"
            meeting["pending"] = False
            meeting["dispatched_at"] = now_str()
            meeting["links"]["project_id"] = project_id
            meeting["history"].append(
                {"at": now_str(), "事件": f"主管派发工程任务（工程清单 #{project_id}），会签流程结束"}
            )
            return meeting, f"工程任务已生成并同步工程清单（#{project_id}）", None

    def _withdraw_to_new_meeting(
        self, meeting_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        role = str(values.get("角色") or "主管").strip()
        if role != "主管":
            return None, "撤回会签只能由主管操作", None
        with self._meeting_lock(meeting_id), self._global:
            meeting = store.find(MODULE, meeting_id)
            if meeting is None:
                return None, f"会签 {meeting_id} 不存在", None
            if meeting["status"] not in ("已会签", "已派单"):
                return None, f"当前为「{meeting['status']}」，只有已形成结论的会签可以撤回", None
            old_no = meeting["会签号"]
            meeting["status"] = WITHDRAWN_STATE
            meeting["pending"] = False
            meeting["history"].append({"at": now_str(), "事件": "主管撤回会签，本轮结论作废，改由新一轮会签决定"})
            self._mark_synced_rows_withdrawn(meeting)

            new_meeting, missing = self.open_meeting(
                {
                    "检测编号": meeting["检测编号"],
                    "桥梁名称": meeting["桥梁名称"],
                    "检查员": meeting["检查员"],
                    "专项评分": meeting["专项评分"],
                    "日常评分": meeting["日常评分"],
                },
                supersedes=meeting,
            )
            if missing or new_meeting is None:  # pragma: no cover - 字段来自既有会议，不会缺
                return None, f"撤回后生成新会议失败：缺少 {'、'.join(missing)}", None
            return new_meeting, f"会签 {old_no} 已撤回，已生成新一轮会签 {new_meeting['会签号']}", {
                "old_meeting_no": old_no,
                "new_meeting_id": new_meeting["id"],
                "new_meeting_no": new_meeting["会签号"],
            }

    # ------------------------------------------------------------------ 主管：限载结论（事务锁 + 队列）

    def submit_decision(
        self, meeting_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        role = str(values.get("角色") or "").strip()
        token = str(values.get("token") or "").strip()
        signer = str(values.get("signer") or "").strip() or "值班主管"
        conclusion = str(values.get("结论") or "").strip()
        opinion = str(values.get("意见") or "").strip()

        if role != "主管":
            return None, "只有主管可以选择限载结论", None
        if not token:
            return None, "签认缺少会签令牌，中断后将无法恢复队列", None
        if conclusion not in CONCLUSIONS:
            return None, f"限载结论必须是：{'、'.join(CONCLUSIONS)}", None

        with self._global:
            meeting = store.find(MODULE, meeting_id)
            if meeting is None:
                return None, f"会签 {meeting_id} 不存在", None

            # 断线重发：同一令牌幂等，直接回放当前结果，不重复入队
            existing = self._queue_entry(meeting, token)
            if existing is not None:
                return self._decision_replay(meeting, existing)

            if meeting["status"] != "待结论":
                return None, self._closed_message(meeting), None

            lock = self._meeting_locks[meeting["id"]]
            entry = {
                "token": token,
                "signer": signer,
                "结论": conclusion,
                "意见": opinion,
                "at": now_str(),
            }
            if lock.acquire(blocking=False):
                # 事务锁放行：本份签认成为唯一决定
                entry["state"] = "放行决定"
                meeting["queue"].append(entry)
                meeting["deciding_token"] = token
                meeting["history"].append(
                    {"at": now_str(), "事件": f"主管 {signer} 的签认取得事务锁（{token[:8]}），进入决定"}
                )
                locked = True
            else:
                # 已有一份签认在临界区，其余主管排队等待，凭会签号可恢复
                entry["state"] = "排队中"
                meeting["queue"].append(entry)
                position = sum(1 for e in meeting["queue"] if e["state"] == "排队中")
                meeting["history"].append(
                    {"at": now_str(), "事件": f"主管 {signer} 的并发签认进入等待队列（第 {position} 位）"}
                )
                locked = False

        if not locked:
            data = {"state": "排队中", "queue_position": position, "token": token}
            return meeting, f"已有主管签认正在处理，您排在第 {position} 位；连接中断可凭会签号 {meeting['会签号']} 恢复", data

        try:
            time.sleep(DECISION_HOLD_SECONDS)  # 临界区持锁：模拟结论落库与多方同步
            with self._global:
                # 持锁期间不会有其他决定/撤回改状态，这里仍做防御性校验
                if meeting["status"] != "待结论":
                    return meeting, self._closed_message(meeting), {"state": "放行决定", "token": token}
                self._apply_decision(meeting, signer, conclusion, opinion)
                queued = [e for e in meeting["queue"] if e["state"] == "排队中"]
                for entry in queued:
                    entry["state"] = "未中签"
                if queued:
                    meeting["history"].append(
                        {"at": now_str(), "事件": f"事务锁释放，队列中 {len(queued)} 份并发签认未中签"}
                    )
                meeting["deciding_token"] = None
        finally:
            lock.release()

        data = {
            "state": "放行决定",
            "token": token,
            "结论": conclusion,
            "评定等级": meeting["评定等级"],
            "风险": meeting["风险"],
        }
        return meeting, f"您的签认已放行，限载结论「{conclusion}」，评定 {meeting['评定等级']}，风险{meeting['风险']}", data

    def _decision_replay(
        self, meeting: dict[str, Any], entry: dict[str, Any]
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """同一令牌的重放请求：按队列项状态与会议现状回放，绝不产生第二份决定。"""
        data = {"state": entry["state"], "token": entry["token"]}
        if entry["state"] == "排队中":
            position = sum(
                1
                for e in meeting["queue"]
                if e["state"] == "排队中" and meeting["queue"].index(e) <= meeting["queue"].index(entry)
            )
            data["queue_position"] = position
            return meeting, f"您的签认仍在第 {position} 位等待，会签号 {meeting['会签号']}", data
        if entry["state"] == "放行决定":
            if meeting["status"] == "待结论":
                return meeting, "您的签认正在临界区处理中，请稍后凭会签号恢复", data
            data.update({"结论": meeting["conclusion"], "评定等级": meeting["评定等级"], "风险": meeting["风险"]})
            return meeting, f"恢复成功：您的签认是最终决定，结论「{meeting['conclusion']}」", data
        data.update({"结论": meeting["conclusion"], "决定人": meeting["decision_by"]})
        return meeting, (
            f"恢复成功：会议已由主管 {meeting['decision_by']} 作出「{meeting['conclusion']}」结论，"
            f"您的并发签认未中签，如不认可请走「撤回并新会签」"
        ), data

    def recover_queue(
        self, meeting_no: str, token: str | None = None
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """连接中断后凭会签号恢复队列；带令牌时回放该份签认的最终去向。"""
        with self._global:
            meeting = self.find_by_no(meeting_no)
            if meeting is None:
                return None, f"会签号 {meeting_no} 不存在", None
            snapshot = {
                "会签号": meeting["会签号"],
                "status": meeting["status"],
                "conclusion": meeting["conclusion"],
                "decision_by": meeting["decision_by"],
                "deciding_token": meeting["deciding_token"],
                "queue": [dict(entry) for entry in meeting["queue"]],
            }
            if not token:
                return meeting, f"队列恢复成功：会签 {meeting_no} 当前「{meeting['status']}」", snapshot
            entry = self._queue_entry(meeting, token)
            if entry is None:
                return meeting, f"会签 {meeting_no} 下未找到该签认令牌的入队记录", snapshot
            _, message, data = self._decision_replay(meeting, entry)
            data["queue"] = snapshot["queue"]
            return meeting, message, data

    def _closed_message(self, meeting: dict[str, Any]) -> str:
        status = meeting["status"]
        if status == "待证据":
            return "检查员尚未提交病害证据，主管还不能选择限载结论"
        if status == "已撤回":
            return "该会签已撤回，单向状态图不可回退；请在新一轮会签上签认"
        return f"会签已由 {meeting['decision_by']} 作出「{meeting['conclusion']}」结论，一份会签只放行一份决定"

    @staticmethod
    def _queue_entry(meeting: dict[str, Any], token: str) -> dict[str, Any] | None:
        for entry in meeting["queue"]:
            if entry["token"] == token:
                return entry
        return None

    # ------------------------------------------------------------------ 结论同步

    def _apply_decision(
        self, meeting: dict[str, Any], signer: str, conclusion: str, opinion: str
    ) -> None:
        grade = CONCLUSION_GRADE[conclusion]
        risk = CONCLUSION_RISK[conclusion]
        special = meeting.get("专项评分")
        daily = meeting.get("日常评分")
        special_grade = grade_of_score(special) if special is not None else None
        daily_grade = grade_of_score(daily) if daily is not None else None
        # 专项评分与日常评分分档冲突时，以专项会签结论为准
        conflict = bool(special_grade and daily_grade and special_grade != daily_grade)

        meeting["status"] = "已会签"
        meeting["pending"] = True  # 工程任务还没生成，会签桌仍未收尾
        meeting["abnormal"] = risk in HIGH_RISKS
        meeting["conclusion"] = conclusion
        meeting["限载结论"] = conclusion
        meeting["评定等级"] = grade
        meeting["专项建议等级"] = special_grade
        meeting["日常评分等级"] = daily_grade
        meeting["等级冲突"] = conflict
        meeting["风险"] = risk
        meeting["decision_by"] = signer
        meeting["decided_at"] = now_str()
        meeting["history"].append(
            {
                "at": now_str(),
                "事件": (
                    f"主管 {signer} 选定限载结论「{conclusion}」，评定 {grade}、风险{risk}"
                    + ("；专项与日常评分分档冲突，以专项会签为准" if conflict else "")
                    + (f"。意见：{opinion}" if opinion else "")
                ),
            }
        )

        bridge_id = self._sync_bridge_record(meeting, special_grade, daily_grade, grade, risk, conflict)
        patrol_id = self._sync_patrol_notice(meeting, signer, grade, risk, conflict)
        info_id = self._sync_bridge_info(meeting, grade, special_grade, daily_grade, risk, conflict)
        meeting["links"].update({"bridge_id": bridge_id, "patrol_id": patrol_id, "bridge_info_id": info_id})
        meeting["history"].append(
            {"at": now_str(), "事件": "会签结果已同步：定检档案、巡检通知、既有桥位（风险看板同步刷新）"}
        )

    def _sync_bridge_record(
        self,
        meeting: dict[str, Any],
        special_grade: str | None,
        daily_grade: str | None,
        grade: str,
        risk: str,
        conflict: bool,
    ) -> int:
        rows = store.rows("bridge")
        record = next((r for r in rows if str(r.get("检测编号", "")) == meeting["检测编号"]), None)
        defects = "、".join(f"{e['部位']}{e['病害类型']}" for e in meeting["evidence"]) or "未见明显病害"
        if record is None:
            record = {"id": max((int(r["id"]) for r in rows), default=0) + 1}
            rows.append(record)
        record.update(
            {
                "检测编号": meeting["检测编号"],
                "桥梁名称": meeting["桥梁名称"],
                "检测类型": "专项检测",
                "检测日期": now_str()[:10],
                "技术状况评分": meeting.get("专项评分"),
                "主要病害": defects,
                "检测单位": f"会签桌 {meeting['会签号']}",
                "检测状态": "专项会签已评定",
                "status": "已评定",
                "pending": True,
                "abnormal": risk in HIGH_RISKS,
                "会签号": meeting["会签号"],
                "限载结论": meeting["conclusion"],
                "专项会签等级": grade,
                "专项建议等级": special_grade,
                "日常评分等级": daily_grade,
                "等级冲突": conflict,
                "风险等级": risk,
            }
        )
        return int(record["id"])

    def _sync_patrol_notice(
        self, meeting: dict[str, Any], signer: str, grade: str, risk: str, conflict: bool
    ) -> int:
        rows = store.rows("patrol")
        defects = "、".join(e["病害类型"] for e in meeting["evidence"]) or "未见明显病害"
        notice = {
            "id": max((int(r["id"]) for r in rows), default=0) + 1,
            "巡查编号": f"XJTZ-{max((int(r['id']) for r in rows), default=0) + 1:04d}",
            "巡查路段": meeting["桥梁名称"],
            "巡查日期": now_str()[:10],
            "巡查人员": f"{signer}（会签通知）",
            "巡查车辆": "—",
            "发现问题": f"专项会签结论「{meeting['conclusion']}」/ 评定{grade} / 风险{risk}：{defects}",
            "处置措施": PATROL_MEASURE[meeting["conclusion"]],
            "巡查状态": "会签巡检通知",
            "status": "待巡查",
            "pending": risk != "低",
            "abnormal": risk in HIGH_RISKS,
            "会签号": meeting["会签号"],
            "检测编号": meeting["检测编号"],
            "限载结论": meeting["conclusion"],
            "风险等级": risk,
            "等级冲突": conflict,
            "来源": "会签桌同步",
        }
        rows.append(notice)
        return int(notice["id"])

    def _sync_bridge_info(
        self,
        meeting: dict[str, Any],
        grade: str,
        special_grade: str | None,
        daily_grade: str | None,
        risk: str,
        conflict: bool,
    ) -> int:
        """既有桥位继续展示历史等级，专项会签等级另挂一列，不覆盖「上次评定等级」。"""
        rows = store.rows("bridge_info")
        info = next(
            (r for r in rows if str(r.get("桥梁名称", "")) == meeting["桥梁名称"]),
            None,
        )
        if info is None:
            info = next(
                (r for r in rows if str(r.get("桥梁编号", "")) == meeting["检测编号"]),
                None,
            )
        if info is None:
            info = {"id": max((int(r["id"]) for r in rows), default=0) + 1, "桥梁编号": meeting["检测编号"]}
            rows.append(info)
            # 新桥位没有历史，用日常评分等级补一条历史基线
            info["上次评定等级"] = daily_grade or "未定级"
        info.update(
            {
                "桥梁名称": meeting["桥梁名称"],
                "桥梁状态": f"{INFO_CONTROL[meeting['conclusion']]}（会签）",
                "status": INFO_CONTROL[meeting["conclusion"]],
                "pending": risk != "低",
                "abnormal": risk in HIGH_RISKS,
                "专项会签等级": grade,
                "专项建议等级": special_grade,
                "日常评分等级": daily_grade,
                "等级冲突": conflict,
                "限载结论": meeting["conclusion"],
                "风险等级": risk,
                "会签号": meeting["会签号"],
            }
        )
        return int(info["id"])

    def _sync_project(self, meeting: dict[str, Any]) -> int:
        rows = store.rows("project")
        project_type = PROJECT_TYPE[meeting["conclusion"]]
        risk = meeting["风险"]
        project = {
            "id": max((int(r["id"]) for r in rows), default=0) + 1,
            "工程编号": f"PROJ-HQ-{max((int(r['id']) for r in rows), default=0) + 1:04d}",
            "工程名称": f"{meeting['桥梁名称']}｜{project_type}",
            "工程类型": project_type,
            "施工路段": meeting["桥梁名称"],
            "承建单位": "待招标",
            "开工日期": "待定",
            "竣工日期": "待定",
            "工程状态": "会签派单·待开工",
            "status": "待开工",
            "pending": True,
            "abnormal": risk in {"高", "极高"},
            "会签号": meeting["会签号"],
            "检测编号": meeting["检测编号"],
            "限载结论": meeting["conclusion"],
            "评定等级": meeting["评定等级"],
            "风险等级": risk,
            "来源": "会签桌派单",
        }
        rows.append(project)
        return int(project["id"])

    def _mark_archive(self, meeting: dict[str, Any]) -> None:
        record = store.find("bridge", meeting["links"]["bridge_id"])
        if record is not None:
            record["status"] = "已归档"
            record["pending"] = False
            record["检测状态"] = "会签派单·已归档"

    def _mark_synced_rows_withdrawn(self, meeting: dict[str, Any]) -> None:
        links = meeting["links"]
        record = store.find("bridge", links.get("bridge_id", -1))
        if record is not None:
            record["检测状态"] = f"原会签{meeting['会签号']}已撤回，等待新会签"
            record["status"] = "待检测"
            record["pending"] = True
        info = store.find("bridge_info", links.get("bridge_info_id", -1))
        if info is not None:
            info["桥梁状态"] = "会签已撤回·待新会签"
            info["status"] = "待新会签"
            info["pending"] = True
        notice = store.find("patrol", links.get("patrol_id", -1))
        if notice is not None:
            notice["巡查状态"] = "会签已撤回·通知作废"
            notice["status"] = "通知作废"
            notice["pending"] = False
            notice["abnormal"] = False
            notice["处置措施"] = f"（原会签 {meeting['会签号']} 已撤回）"
        project = store.find("project", links.get("project_id", -1))
        if project is not None:
            project["工程状态"] = "会签已撤回·停止派工"
            project["status"] = "已撤回"
            project["pending"] = False

    # ------------------------------------------------------------------ 风险看板

    def risk_board(self) -> dict[str, Any]:
        with self._global:
            meetings = store.rows(MODULE)
            active = [m for m in meetings if m["status"] != WITHDRAWN_STATE]
            decided = [m for m in active if m["status"] in ("已会签", "已派单")]
            risk_counts = {label: 0 for label in RISK_ORDER}
            for meeting in decided:
                risk_counts[meeting["风险"]] += 1
            control_types = {"限载": 0, "限速": 0, "停用/封闭": 0}
            for meeting in decided:
                if meeting["conclusion"] == "限载通行":
                    control_types["限载"] += 1
                elif meeting["conclusion"] == "限速通行":
                    control_types["限速"] += 1
                elif meeting["conclusion"] in ("停用交通管制", "封闭重建"):
                    control_types["停用/封闭"] += 1
            cards = [
                {"label": "待主管结论", "value": sum(1 for m in active if m["status"] == "待结论")},
                {"label": "较高风险及以上桥梁", "value": sum(1 for m in decided if m["风险"] in HIGH_RISKS)},
                {"label": "限载/管制桥梁", "value": sum(control_types.values())},
                {"label": "会签已派工程", "value": sum(1 for m in decided if m["status"] == "已派单")},
                {"label": "专项覆盖日常（等级冲突）", "value": sum(1 for m in decided if m["等级冲突"])},
            ]
            items = [
                {
                    "会签号": m["会签号"],
                    "检测编号": m["检测编号"],
                    "桥梁名称": m["桥梁名称"],
                    "status": m["status"],
                    "限载结论": m["conclusion"],
                    "评定等级": m["评定等级"],
                    "风险": m["风险"],
                    "等级冲突": m["等级冲突"],
                    "历史等级": self._history_grade(m),
                    "decision_by": m["decision_by"],
                }
                for m in sorted(decided, key=lambda m: RISK_ORDER[m["风险"]], reverse=True)
            ]
            return {
                "cards": cards,
                "risk_counts": risk_counts,
                "control_types": control_types,
                "items": items,
                "waiting": [
                    {"会签号": m["会签号"], "桥梁名称": m["桥梁名称"], "检查员": m["检查员"]}
                    for m in active
                    if m["status"] in ("待证据", "待结论")
                ],
            }

    @staticmethod
    def _history_grade(meeting: dict[str, Any]) -> str | None:
        info_id = meeting.get("links", {}).get("bridge_info_id")
        if info_id is None:
            return None
        info = store.find("bridge_info", info_id)
        return str(info.get("上次评定等级")) if info is not None else None

    # ------------------------------------------------------------------ 工具

    def _meeting_lock(self, meeting_id: int) -> threading.Lock:
        with self._global:
            lock = self._meeting_locks.get(meeting_id)
            if lock is None:
                lock = threading.Lock()
                self._meeting_locks[meeting_id] = lock
            return lock

    def _next_meeting_no(self) -> str:
        year = datetime.now().year
        seq = len(store.rows(MODULE)) + 1
        return f"HQ-{year}-{seq:04d}"


service = CountersignService()
