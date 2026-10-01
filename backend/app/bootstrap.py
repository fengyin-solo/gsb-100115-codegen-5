"""检测记录会签桌示例数据引导。

不改动静态 seed.py 的通用样例；这里通过 service 真实走一遍
「放置证据 → 主管结论 → 派工 / 撤回新会签」流程，
让同步到定检档案、巡检通知、工程清单与既有桥位的数据天然一致。
"""
from __future__ import annotations

from app.services.countersign import service
from app.store import store

# 既有桥位：历史「上次评定等级」会一直保留，专项会签等级在结论时另挂，不覆盖
EXISTING_INFOS = [
    {"桥梁编号": "GJ-2026-007", "桥梁名称": "长江路跨线桥", "桥型结构": "预应力混凝土连续箱梁",
     "上次评定等级": "2类", "设计荷载": "公路-Ⅰ级", "建成年份": "2008"},
    {"桥梁编号": "GJ-2026-012", "桥梁名称": "黄河路立交桥", "桥型结构": "钢筋混凝土T梁",
     "上次评定等级": "3类", "设计荷载": "公路-Ⅰ级", "建成年份": "1996"},
    {"桥梁编号": "GJ-2026-018", "桥梁名称": "珠江路人行天桥", "桥型结构": "钢箱梁人行桥",
     "上次评定等级": "2类", "设计荷载": "人群荷载 4.0kPa", "建成年份": "2012"},
    {"桥梁编号": "GJ-2026-021", "桥梁名称": "淮河路高架桥", "桥型结构": "预应力混凝土小箱梁",
     "上次评定等级": "4类", "设计荷载": "公路-Ⅰ级", "建成年份": "1990"},
]

# 会签桌演示会议：(检测编号, 桥梁名称, 检查员, 专项评分, 日常评分, 证据, 结论, 决定主管)
MEETING_SEEDS = [
    (
        "GJ-2026-007", "长江路跨线桥", "检查员王平", 92.0, 84.0,
        [("桥面铺装", "纵向裂缝", "K1+220 行车道，缝宽 0.12mm", 28.0, 10.0)],
        "正常通行", "主管李工",
    ),
    (
        "GJ-2026-012", "黄河路立交桥", "检查员王平", 66.0, 81.0,
        [("主梁", "腹板斜裂缝", "3# 孔腹板斜向裂缝，缝宽 0.35mm", 40.0, 58.0),
         ("支座", "支座脱空", "2# 墩右侧支座脱空约 8mm", 66.0, 74.0)],
        "限载通行", "主管李工",
    ),
    (
        "GJ-2026-018", "珠江路人行天桥", "检查员赵磊", 52.0, 78.0,
        [("桥面板", "混凝土剥落露筋", "梯道口桥面板剥落面积约 0.6㎡", 55.0, 32.0),
         ("墩台盖梁", "钢筋锈蚀", "盖梁主筋锈蚀胀裂"),
         ("防水层", "老化渗水", "梯道平台防水失效渗水")],
        "封闭重建", "主管周总",
    ),
]

# 单独演示撤回链路：先定停用，再撤回生成新一轮会签
WITHDRAW_SEED = (
    "GJ-2026-025", "东环路跨河桥", "检查员赵磊", 58.0, 79.0,
    [("主梁", "跨中下挠", "中跨下挠超限，伴随横向裂缝", 50.0, 60.0)],
    "停用交通管制", "主管周总",
)

# 尚未提交证据的在途会议
OPEN_SEED = (
    "GJ-2026-031", "南湖大道桥", "检查员王平", None, None,
    [], None, None,
)


def _add_evidence(meeting_id: int, inspector: str, evidences: list[tuple]) -> None:
    for idx, item in enumerate(evidences, start=1):
        part, defect, desc = item[0], item[1], item[2]
        x = item[3] if len(item) > 3 else 20.0 + idx * 18.0
        y = item[4] if len(item) > 4 else None
        service.add_evidence(
            meeting_id,
            {"角色": "检查员", "部位": part, "病害类型": defect, "说明": desc,
             "x": x, "y": y, "signer": inspector},
        )


def seed_countersign() -> None:
    """幂等引导：仓库里已有会签数据时（如热重载）不再重复播种。"""
    if store.rows("countersign"):
        return

    info_rows = store.rows("bridge_info")
    for info in EXISTING_INFOS:
        info_rows.append({
            "id": max((int(r["id"]) for r in info_rows), default=0) + 1,
            "status": "正常", "pending": True, "abnormal": False,
            "桥梁状态": "在役",
            **info,
        })

    def open(seed: tuple) -> dict:
        test_no, bridge, inspector, special, daily, *_ = seed
        meeting, missing = service.open_meeting(
            {"检测编号": test_no, "桥梁名称": bridge, "检查员": inspector,
             "专项评分": special, "日常评分": daily}
        )
        assert not missing, f"会签播种缺字段：{missing}"
        return meeting

    # 走完到「已会签」
    decided = []
    for seed in MEETING_SEEDS:
        meeting = open(seed)
        _add_evidence(meeting["id"], seed[2], seed[5])
        service.run_action(meeting["id"], "检查员提交证据", {"角色": "检查员"})
        m, msg, _ = service.submit_decision(meeting["id"], {
            "角色": "主管", "结论": seed[6], "signer": seed[7],
            "token": f"SEED-{meeting['会签号']}", "意见": "会签桌示例结论",
        })
        assert m["status"] == "已会签", msg
        decided.append(meeting)

    # 停用结论形成后撤回，生成新一轮会签（旧会议留「已撤回」终态）
    withdrawn = open(WITHDRAW_SEED)
    _add_evidence(withdrawn["id"], WITHDRAW_SEED[2], WITHDRAW_SEED[5])
    service.run_action(withdrawn["id"], "检查员提交证据", {"角色": "检查员"})
    service.submit_decision(withdrawn["id"], {
        "角色": "主管", "结论": WITHDRAW_SEED[6], "signer": WITHDRAW_SEED[7],
        "token": f"SEED-{withdrawn['会签号']}",
    })
    service.run_action(withdrawn["id"], "撤回并新会签", {"角色": "主管"})

    # 只剩人行天桥一例走到最后一步「已派单」
    service.run_action(decided[2]["id"], "生成工程任务", {"角色": "主管"})

    # 在途：检查员刚录入、还没放证据
    open(OPEN_SEED)
