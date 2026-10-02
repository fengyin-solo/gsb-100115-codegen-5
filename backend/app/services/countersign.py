"""桥梁定检·检测记录会签桌业务规则。

以桥面剖切图为主对象：检查员录入检测编号与桥梁名称、放置病害证据，
主管选择限载结论后才允许生成工程任务。

约束都收在这一层：
- 单向状态图：草稿 → 待主管会签 → 已会签 → 已生成任务；撤回不回退，
  旧会议置「已撤回」终态，并按同一会签号生成届次 +1 的新会议。
- 会签结果同步到定检档案、巡检通知、工程清单三处，风险看板随结论更新。
- 专项评分与日常评分冲突时标记冲突，以专项会签结论为准，桥位历史等级只追加不覆盖。
- 每把会签一把事务锁，签认请求进 FIFO 队列，只放行一份决定；
  同一请求号幂等，连接中断后可凭会签号恢复队列并取回处理结果。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "countersign"
BRIDGE_INFO_MODULE = "bridge_info"
PATROL_MODULE = "patrol"
PROJECT_MODULE = "project"

# 单向状态图：key 为当前状态，value 为允许迁往的状态集合。
STATE_DRAFT = "草稿"
STATE_WAITING = "待主管会签"
STATE_SIGNED = "已会签"
STATE_TASKED = "已生成任务"
STATE_WITHDRAWN = "已撤回"

TRANSITIONS: dict[str, set[str]] = {
    STATE_DRAFT: {STATE_WAITING},
    STATE_WAITING: {STATE_SIGNED},
    STATE_SIGNED: {STATE_TASKED},
    STATE_TASKED: set(),
    STATE_WITHDRAWN: set(),  # 终态：撤回不回退，只能开新会议
}
ACTIVE_STATES = [STATE_DRAFT, STATE_WAITING, STATE_SIGNED, STATE_TASKED]
WITHDRAWABLE_STATES = {STATE_DRAFT, STATE_WAITING, STATE_SIGNED, STATE_TASKED}

# 主管可选择的限载结论，同时决定技术等级口径与风险等级。
CONCLUSIONS = ["正常通行", "限速通行", "限载通行", "禁止通行"]
CONCLUSION_GRADE = {
    "正常通行": "1类",
    "限速通行": "3类",
    "限载通行": "4类",
    "禁止通行": "5类",
}
CONCLUSION_RISK = {
    "正常通行": "低",
    "限速通行": "中",
    "限载通行": "高",
    "禁止通行": "极高",
}
CONCLUSION_ARCHIVE_STATUS = {
    "正常通行": "正常",
    "限速通行": "限速",
    "限载通行": "限载",
    "禁止通行": "禁行",
}
RISK_ORDER = ["极高", "高", "中", "低"]

# 检查员录入阶段必须齐备的字段。
REQUIRED_FIELDS = ["检测编号", "桥梁名称"]
# 提交会签前剖切图上至少要放置一处病害证据。
MIN_EVIDENCE = 1


class CountersignService:
    def __init__(self) -> None:
        # 每会一把锁：保护单场会议的状态推进与签认队列；
        # _locks_guard 只保护锁表本身的增取。
        self._locks_guard = threading.Lock()
        self._meeting_locks: dict[int, threading.RLock] = {}

    # ------------------------------------------------------------------ 查询

    def list_meetings(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        bridge: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("会议全称", ""))
                or keyword in str(row.get("检测编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if bridge:
            rows = [
                row for row in rows
                if bridge in str(row.get("桥梁名称", ""))
                or bridge in str(row.get("桥位编号", ""))
            ]
        # 列表按创建顺序倒序，新会议在最上面。
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_meeting(self, meeting_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, meeting_id)

    def find_by_code(self, code: str) -> dict[str, Any] | None:
        """按会签号（含届次全称）定位会议，断网恢复队列时用。"""
        code = code.strip()
        for row in store.rows(MODULE):
            if str(row.get("会议全称", "")) == code:
                return row
        return None

    def queue_snapshot(self, code: str) -> tuple[dict[str, Any] | None, str]:
        meeting = self.find_by_code(code)
        if meeting is None:
            return None, f"会签号 {code} 不存在，请核对后重试"
        return {
            "会议全称": meeting["会议全称"],
            "会签号": meeting["会签号"],
            "届次": meeting["届次"],
            "status": meeting["status"],
            "队列": meeting.get("队列", []),
            "限载结论": meeting.get("限载结论"),
        }, ""

    # ------------------------------------------------------------------ 建会

    def create_meeting(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        seq = max((int(str(row.get("会签号", "")).split("-")[-1]) for row in rows
                   if str(row.get("会签号", "")).count("-") == 2), default=0) + 1
        year = datetime.now().year
        base_code = f"CSD-{year}-{seq:04d}"
        now = self._now()
        inspector = str(values.get("检查员") or "检查员·李工").strip()
        meeting = {
            "id": self._next_id(rows),
            "status": STATE_DRAFT,
            "pending": True,
            "abnormal": False,
            "会签号": base_code,
            "届次": 1,
            "会议全称": base_code,
            "检测编号": str(values.get("检测编号")).strip(),
            "桥梁名称": str(values.get("桥梁名称")).strip(),
            "桥位编号": str(values.get("桥位编号") or "").strip(),
            "检查员": inspector,
            "主管": "",
            # 桥面剖切图：主对象，前端按百分比坐标在上面放置病害证据点。
            "剖切图": {
                "名称": f"{str(values.get('桥梁名称')).strip()}桥面剖切图",
                "宽度": 100,
                "高度": 100,
            },
            "病害证据": [],
            "专项评分": None,
            "日常评分": str(values.get("日常评分") or "").strip() or None,
            "评分冲突": False,
            "限载结论": None,
            "技术等级": None,
            "风险等级": None,
            "版本": 1,
            "接续会议": None,
            "被接续": None,
            "同步": {"档案": None, "巡检通知": None, "工程清单": None},
            "队列": [],
            "日志": [
                {"时间": now, "角色": inspector, "动作": "创建会议",
                 "说明": "录入检测编号与桥梁名称，打开桥面剖切图"},
            ],
            "创建时间": now,
        }
        rows.append(meeting)
        self._register_lock(meeting["id"])
        return meeting, []

    # ----------------------------------------------------- 检查员：剖切图作业

    def place_evidence(self, meeting_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在"
        if meeting["status"] != STATE_DRAFT:
            return None, "剖切图已提交会签，证据不能再增改；如需修正请撤回后由新会议承接"
        try:
            x = float(values.get("x"))
            y = float(values.get("y"))
        except (TypeError, ValueError):
            return None, "证据坐标必须是剖切图上的百分比数值"
        if not 0 <= x <= 100 or not 0 <= y <= 100:
            return None, "证据坐标超出剖切图范围（0-100）"
        if not str(values.get("病害类型") or "").strip():
            return None, "请先选择病害类型再放置证据"
        evidence = meeting.setdefault("病害证据", [])
        item = {
            "证据号": f"E{len(evidence) + 1}",
            "构件": str(values.get("构件") or "未指定构件").strip(),
            "x": x,
            "y": y,
            "病害类型": str(values.get("病害类型")).strip(),
            "严重度": str(values.get("严重度") or "中").strip(),
            "描述": str(values.get("描述") or "").strip(),
            "照片": str(values.get("照片") or "").strip(),
        }
        evidence.append(item)
        meeting["版本"] = int(meeting.get("版本", 1)) + 1
        self._log(meeting, meeting["检查员"], "放置病害证据",
                  f"{item['证据号']} 定位到「{item['构件']}」：{item['病害类型']}")
        return meeting, ""

    def update_inspection(self, meeting_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """草稿阶段补充日常评分、桥位编号等检测信息。"""
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在"
        if meeting["status"] != STATE_DRAFT:
            return None, "会议已离开草稿，检测信息以提交时为准；如需修改请撤回重开会议"
        for field in ("桥位编号", "日常评分"):
            if field in values and str(values.get(field) or "").strip():
                meeting[field] = str(values.get(field)).strip()
        meeting["版本"] = int(meeting.get("版本", 1)) + 1
        return meeting, ""

    def submit_for_sign(self, meeting_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在"
        moved, reason = self._move(meeting, STATE_WAITING)
        if not moved:
            return None, reason
        if str(values.get("日常评分") or "").strip():
            meeting["日常评分"] = str(values.get("日常评分")).strip()
        if len(meeting.get("病害证据", [])) < MIN_EVIDENCE:
            # 状态推进失败要回退，保持单向状态机不被旁路。
            meeting["status"] = STATE_DRAFT
            meeting["pending"] = True
            return None, "剖切图上还没有病害证据，请至少放置一处后再提交会签"
        self._log(meeting, meeting["检查员"], "提交会签",
                  f"{len(meeting['病害证据'])} 处病害证据随剖切图送主管选择限载结论")
        return meeting, ""

    # --------------------------------------------------------- 主管：会签决定

    def sign(
        self,
        meeting_id: int,
        values: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """主管在会上给出限载结论。

        返回 (会议, 说明, 是否已生效)。并发签认串行化：队首决定放行，
        后到请求进队列但不再改变结论；同一请求号重复提交按幂等处理。
        """
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在", False
        lock = self._lock_for(meeting_id)
        with lock:
            return self._sign_locked(meeting, values, request_id)

    def sign_by_code(
        self,
        code: str,
        values: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """凭会签号签认/恢复：连接中断后重连用这个入口，保证幂等。"""
        meeting = self.find_by_code(code)
        if meeting is None:
            return None, f"会签号 {code} 不存在，请核对后重试", False
        return self.sign(int(meeting["id"]), values, request_id=request_id)

    def _sign_locked(
        self,
        meeting: dict[str, Any],
        values: dict[str, Any],
        request_id: str | None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        code = str(meeting["会议全称"])
        queue = meeting.setdefault("队列", [])
        request_id = (request_id or str(values.get("request_id") or "")).strip()
        supervisor = str(values.get("主管") or "主管·周建国").strip()
        conclusion = str(values.get("限载结论") or "").strip()

        # 幂等：同一请求号重放（含断网恢复）直接给回原结论。
        if request_id:
            for item in queue:
                if item.get("请求号") == request_id:
                    if item.get("状态") == "adopted":
                        return meeting, f"请求 {request_id} 的决定已生效（{code}），无需重复签认", True
                    return None, f"请求 {request_id} 已在队列中被队首决定覆盖，未放行", False

        if meeting["status"] == STATE_WAITING:
            if conclusion not in CONCLUSIONS:
                return None, f"限载结论必须是：{'、'.join(CONCLUSIONS)}", False
            expected_version = values.get("version")
            if expected_version is not None and int(expected_version) != int(meeting.get("版本")):
                return None, "会议版本已变化（队列里有新的提交），请刷新后再签认", False

            entry = {
                "请求号": request_id or f"REQ-{code}-{len(queue) + 1}",
                "主管": supervisor,
                "结论": conclusion,
                "专项评分": str(values.get("专项评分") or "").strip() or None,
                "意见": str(values.get("意见") or "").strip(),
                "状态": "adopted",
                "说明": "队首决定，事务锁放行",
                "时间": self._now(),
            }
            queue.append(entry)
            self._apply_decision(meeting, entry)
            return meeting, f"{code} 会签完成：{conclusion}，已同步定检档案与巡检通知", True

        if meeting["status"] in (STATE_SIGNED, STATE_TASKED):
            # 会议已有决定：仍把请求留档在队列里，但标记未放行，保证“只放行一份决定”。
            entry = {
                "请求号": request_id or f"REQ-{code}-{len(queue) + 1}",
                "主管": supervisor,
                "结论": conclusion or "（未给出结论）",
                "专项评分": str(values.get("专项评分") or "").strip() or None,
                "意见": str(values.get("意见") or "").strip(),
                "状态": "skipped",
                "说明": f"会议已有决定（{meeting.get('限载结论')}），本请求未放行",
                "时间": self._now(),
            }
            queue.append(entry)
            meeting["版本"] = int(meeting.get("版本", 1)) + 1
            return None, f"{code} 已有生效决定「{meeting.get('限载结论')}」，本份签认未放行，已留档队列", False

        return None, f"{code} 当前为{meeting['status']}，只有待主管会签的会议能签认", False

    def _apply_decision(self, meeting: dict[str, Any], entry: dict[str, Any]) -> None:
        conclusion = entry["结论"]
        supervisor = entry["主管"]
        special_score = entry.get("专项评分")
        daily_score = meeting.get("日常评分")

        grade = CONCLUSION_GRADE[conclusion]
        risk = CONCLUSION_RISK[conclusion]
        special_grade = self.grade_from_score(special_score) if special_score else grade
        daily_grade = self.grade_from_score(daily_score) if daily_score else None
        conflict = bool(special_grade and daily_grade and special_grade != daily_grade)

        meeting["status"] = STATE_SIGNED
        meeting["pending"] = True
        meeting["abnormal"] = conclusion == "禁止通行"
        meeting["主管"] = supervisor
        meeting["专项评分"] = special_score
        meeting["限载结论"] = conclusion
        meeting["技术等级"] = grade
        meeting["风险等级"] = risk
        meeting["评分冲突"] = conflict
        meeting["版本"] = int(meeting.get("版本", 1)) + 1

        conflict_note = ""
        if conflict:
            conflict_note = f"；专项{special_score}分（{special_grade}）与日常{daily_score}分（{daily_grade}）冲突，以专项会签为准"
        self._log(meeting, supervisor, "会签",
                  f"{conclusion}，技术等级{grade}，风险{risk}{conflict_note}")

        notice_no = self._sync_archive(meeting, grade, risk, conflict)
        meeting["同步"]["巡检通知"] = notice_no
        self._log(meeting, "系统", "同步", f"会签结果写入定检档案，巡检通知 {notice_no} 已下发")

    # ----------------------------------------------------------- 生成工程任务

    def create_task(self, meeting_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在"
        with self._lock_for(meeting_id):
            moved, reason = self._move(meeting, STATE_TASKED)
            if not moved:
                return None, reason
            task_no = self._sync_project(meeting, values or {})
            meeting["同步"]["工程清单"] = task_no
            meeting["pending"] = False
            meeting["版本"] = int(meeting.get("版本", 1)) + 1
            self._log(meeting, "系统", "生成工程任务",
                      f"按限载结论「{meeting['限载结论']}」生成工程清单 {task_no}，三处同步完成")
            return meeting, f"工程任务 {task_no} 已生成并进入工程清单"

    # --------------------------------------------------------------- 撤回续届

    def withdraw(self, meeting_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """撤回不允许在原会议上回退：旧会议置终态，另开同会签号的新届次会议。"""
        meeting = store.find(MODULE, meeting_id)
        if meeting is None:
            return None, f"会签 {meeting_id} 不存在"
        with self._lock_for(meeting_id):
            if meeting["status"] not in WITHDRAWABLE_STATES:
                return None, f"{meeting['会议全称']} 已{meeting['status']}，不能撤回"
            reason_text = str(values.get("原因") or "资料需补充复核").strip()
            operator = str(values.get("操作人") or meeting.get("主管") or meeting["检查员"]).strip()

            old_code = str(meeting["会议全称"])
            meeting["status"] = STATE_WITHDRAWN
            meeting["pending"] = False
            meeting["版本"] = int(meeting.get("版本", 1)) + 1
            self._log(meeting, operator, "撤回",
                      f"{reason_text}；单向状态图不回退，生成同会签号新会议承接")
            self._revoke_sync(meeting)

            rows = store.rows(MODULE)
            new_revision = int(meeting.get("届次", 1)) + 1
            now = self._now()
            full_code = f"{meeting['会签号']}-R{new_revision}"
            new_meeting = {
                "id": self._next_id(rows),
                "status": STATE_DRAFT,
                "pending": True,
                "abnormal": False,
                "会签号": meeting["会签号"],
                "届次": new_revision,
                "会议全称": full_code,
                "检测编号": meeting["检测编号"],
                "桥梁名称": meeting["桥梁名称"],
                "桥位编号": meeting.get("桥位编号", ""),
                "检查员": meeting["检查员"],
                "主管": "",
                "剖切图": dict(meeting.get("剖切图", {})),
                # 继承剖切图与病害证据，检查员可在新会议上修正后重新提交。
                "病害证据": [dict(item) for item in meeting.get("病害证据", [])],
                "专项评分": None,
                "日常评分": meeting.get("日常评分"),
                "评分冲突": False,
                "限载结论": None,
                "技术等级": None,
                "风险等级": None,
                "版本": 1,
                "接续会议": None,
                "被接续": meeting["id"],
                "同步": {"档案": None, "巡检通知": None, "工程清单": None},
                "队列": [],
                "日志": [
                    {"时间": now, "角色": "系统", "动作": "撤回续届",
                     "说明": f"承接 {old_code} 的剖切图与病害证据，重新走单向会签流程"},
                ],
                "创建时间": now,
            }
            rows.append(new_meeting)
            meeting["接续会议"] = new_meeting["id"]
            self._register_lock(new_meeting["id"])
            return new_meeting, f"已撤回 {old_code}，新会议 {full_code} 已生成，请重新提交会签"

    # ----------------------------------------------------------------- 风险看板

    def risk_board(self) -> dict[str, Any]:
        """随限载结论实时汇总：结论分布、风险等级、高风险桥位、在途队列。"""
        rows = store.rows(MODULE)
        decided = [row for row in rows if row.get("status") in (STATE_SIGNED, STATE_TASKED)]

        conclusion_counts = {name: 0 for name in CONCLUSIONS}
        risk_counts = {name: 0 for name in RISK_ORDER}
        bridge_items: list[dict[str, Any]] = []
        for row in decided:
            conclusion = str(row.get("限载结论"))
            risk = str(row.get("风险等级"))
            conclusion_counts[conclusion] = conclusion_counts.get(conclusion, 0) + 1
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            bridge_items.append({
                "会签号": row["会议全称"],
                "桥梁名称": row["桥梁名称"],
                "桥位编号": row.get("桥位编号", ""),
                "限载结论": conclusion,
                "技术等级": row.get("技术等级"),
                "风险等级": risk,
                "专项评分": row.get("专项评分"),
                "日常评分": row.get("日常评分"),
                "评分冲突": bool(row.get("评分冲突")),
                "任务编号": row.get("同步", {}).get("工程清单"),
            })

        high_risk = [item for item in bridge_items if item["风险等级"] in ("极高", "高")]
        waiting = [row for row in rows if row.get("status") == STATE_WAITING]
        queued = sum(
            1 for row in rows
            for item in row.get("队列", []) if item.get("状态") == "skipped"
        )
        return {
            "结论分布": conclusion_counts,
            "风险等级分布": risk_counts,
            "高风险桥位": sorted(high_risk, key=lambda item: RISK_ORDER.index(item["风险等级"])),
            "桥位明细": sorted(
                bridge_items,
                key=lambda item: RISK_ORDER.index(item["风险等级"]),
            ),
            "待主管会签": [
                {"会签号": row["会议全称"], "桥梁名称": row["桥梁名称"],
                 "检测编号": row["检测编号"], "证据数": len(row.get("病害证据", []))}
                for row in sorted(waiting, key=lambda r: int(r.get("id", 0)))
            ],
            "未放行签认": queued,
            "已撤回会议": sum(1 for row in rows if row.get("status") == STATE_WITHDRAWN),
        }

    # ------------------------------------------------------------- 三处同步

    def _sync_archive(self, meeting: dict[str, Any], grade: str, risk: str, conflict: bool) -> str:
        """签认时同步定检档案并下发巡检通知。

        既有桥位只追加等级历史，上次评定等级（历史等级）原样保留；
        匹配不到桥位时补建档案行，避免会签结果没有落点。
        """
        bridge = self._match_bridge(meeting)
        archive_status = CONCLUSION_ARCHIVE_STATUS[meeting["限载结论"]]
        code = str(meeting["会议全称"])
        history_line = f"{grade}（专项会签 {code}）"
        if bridge is None:
            bridge_rows = store.rows(BRIDGE_INFO_MODULE)
            bridge = {
                "id": self._next_id(bridge_rows),
                "桥梁编号": meeting.get("桥位编号") or f"BRID-NEW-{meeting['id']}",
                "桥梁名称": meeting["桥梁名称"],
                "桥型结构": "待补录",
                "跨径组合": "",
                "设计荷载": "",
                "建成年份": "",
                "上次评定等级": "未评定",
                "桥梁状态": archive_status,
                "等级历史": [],
            }
            bridge_rows.append(bridge)
        history = bridge.setdefault("等级历史", [])
        if history_line not in history:
            history.append(history_line)
        bridge["最新专项等级"] = grade
        bridge["限载结论"] = meeting["限载结论"]
        bridge["最新会签号"] = code
        bridge["status"] = archive_status
        bridge["pending"] = meeting["风险等级"] in ("高", "极高")
        bridge["abnormal"] = risk == "极高"
        meeting["同步"]["档案"] = {"桥位编号": bridge.get("桥梁编号", ""), "桥梁名称": bridge["桥梁名称"]}

        notice_rows = store.rows(PATROL_MODULE)
        notice_no = self._next_doc_no(notice_rows, "巡查编号", "NOTI")
        daily_part = (
            f"专项评分{meeting.get('专项评分')}，日常评分{meeting.get('日常评分')}，以专项会签为准"
            if conflict else f"技术等级{grade}"
        )
        notice_rows.append({
            "id": self._next_id(notice_rows),
            "status": "已通知",
            "pending": False,
            "abnormal": risk == "极高",
            "巡查编号": notice_no,
            "巡查路段": meeting["桥梁名称"],
            "巡查日期": datetime.now().strftime("%Y-%m-%d"),
            "巡查人员": meeting["主管"],
            "巡查车辆": "—",
            "发现问题": f"定检会签结论：{meeting['限载结论']}，{daily_part}",
            "处置措施": self._notice_measure(meeting["限载结论"]),
            "巡查状态": "已通知",
            "通知类型": "定检会签巡检通知",
            "关联会签号": code,
            "桥位编号": bridge.get("桥梁编号", ""),
            "通知生效": True,
        })
        return notice_no

    def _sync_project(self, meeting: dict[str, Any], values: dict[str, Any]) -> str:
        """会签通过后才允许落工程清单，保证任务一定带着限载结论。"""
        rows = store.rows(PROJECT_MODULE)
        task_no = self._next_doc_no(rows, "工程编号", "TASK")
        conclusion = str(meeting["限载结论"])
        task_type = str(values.get("工程类型") or "").strip() or {
            "正常通行": "日常养护",
            "限速通行": "限速通行保障",
            "限载通行": "限载观测与维修",
            "禁止通行": "应急抢险加固",
        }[conclusion]
        rows.append({
            "id": self._next_id(rows),
            "status": "待开工",
            "pending": True,
            "abnormal": conclusion == "禁止通行",
            "工程编号": task_no,
            "工程名称": str(values.get("工程名称") or "").strip()
            or f"{meeting['桥梁名称']}{conclusion}处治工程（{meeting['会议全称']}）",
            "工程类型": task_type,
            "施工路段": f"{meeting['桥梁名称']}（{meeting.get('桥位编号') or '桥位待补'}）",
            "承建单位": "待定",
            "开工日期": "",
            "竣工日期": "",
            "工程状态": "待开工",
            "来源": "定检会签工程清单",
            "关联会签号": meeting["会议全称"],
        })
        return task_no

    def _revoke_sync(self, meeting: dict[str, Any]) -> None:
        """撤回后处理既有同步物：巡检通知作废，档案摘掉当前结论但保留历史等级，
        已生成的工程任务不删除（已对外派单），转为跟踪并在日志说明。"""
        code = str(meeting["会议全称"])
        for notice in store.rows(PATROL_MODULE):
            if notice.get("关联会签号") == code and notice.get("通知生效"):
                notice["通知生效"] = False
                notice["status"] = "已作废"
                notice["巡查状态"] = "已作废"
                notice["处置措施"] = f"会签 {code} 已撤回，通知作废，等待新会议结论"

        bridge = self._match_bridge(meeting)
        if bridge is not None and bridge.get("最新会签号") == code:
            bridge["最新专项等级"] = None
            bridge["限载结论"] = None
            bridge["最新会签号"] = None
            bridge["status"] = "正常"
            bridge["pending"] = False
            bridge["abnormal"] = False
            history = bridge.setdefault("等级历史", [])
            history.append(f"原{meeting.get('技术等级')}结论随 {code} 撤回，历史等级留存")

        for task in store.rows(PROJECT_MODULE):
            if task.get("关联会签号") == code:
                task["跟踪说明"] = f"来源会签 {code} 已撤回，任务保留并转跟踪，以新会议结论为准"
                task["abnormal"] = True

    # ------------------------------------------------------------- 内部工具

    def _move(self, meeting: dict[str, Any], target: str) -> tuple[bool, str]:
        current = str(meeting.get("status"))
        if target not in TRANSITIONS.get(current, set()):
            allowed = "、".join(sorted(TRANSITIONS.get(current, set()))) or "无下一状态"
            return False, f"{meeting['会议全称']} 当前为{current}，单向状态图只允许迁往：{allowed}"
        meeting["status"] = target
        meeting["版本"] = int(meeting.get("版本", 1)) + 1
        return True, ""

    def _match_bridge(self, meeting: dict[str, Any]) -> dict[str, Any] | None:
        bridge_no = str(meeting.get("桥位编号") or "").strip()
        bridge_name = str(meeting.get("桥梁名称") or "").strip()
        for row in store.rows(BRIDGE_INFO_MODULE):
            if bridge_no and str(row.get("桥梁编号", "")) == bridge_no:
                return row
        for row in store.rows(BRIDGE_INFO_MODULE):
            if bridge_name and str(row.get("桥梁名称", "")) == bridge_name:
                return row
        return None

    @staticmethod
    def grade_from_score(score: Any) -> str | None:
        """按城市桥梁养护技术规范的五类制口径，把评分折算到等级区间。"""
        try:
            value = float(score)
        except (TypeError, ValueError):
            return None
        if value >= 90:
            return "1类"
        if value >= 80:
            return "2类"
        if value >= 60:
            return "3类"
        if value >= 40:
            return "4类"
        return "5类"

    @staticmethod
    def _notice_measure(conclusion: str) -> str:
        return {
            "正常通行": "正常通行，按周期开展日常巡查",
            "限速通行": "限速40km/h通行，加密日常巡查并跟踪病害发展",
            "限载通行": "限载20t并设岗值守，编制限载通行专项方案",
            "禁止通行": "立即封闭交通，布设绕行指引并启动应急处治",
        }[conclusion]

    def _lock_for(self, meeting_id: int) -> threading.RLock:
        with self._locks_guard:
            return self._meeting_locks.setdefault(meeting_id, threading.RLock())

    def _register_lock(self, meeting_id: int) -> None:
        with self._locks_guard:
            self._meeting_locks.setdefault(meeting_id, threading.RLock())

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    @staticmethod
    def _next_doc_no(rows: list[dict[str, Any]], field: str, prefix: str) -> str:
        year = datetime.now().year
        seq = 0
        for row in rows:
            value = str(row.get(field, ""))
            if value.startswith(f"{prefix}-{year}-"):
                tail = value.rsplit("-", 1)[-1]
                if tail.isdigit():
                    seq = max(seq, int(tail))
        return f"{prefix}-{year}-{seq + 1:04d}"

    @staticmethod
    def _log(meeting: dict[str, Any], role: str, action: str, detail: str) -> None:
        meeting.setdefault("日志", []).append(
            {"时间": CountersignService._now(), "角色": role, "动作": action, "说明": detail}
        )

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


service = CountersignService()
