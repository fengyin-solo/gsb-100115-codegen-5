"""会签桌业务规则自测：单向状态机、并发事务锁、断线恢复、撤回新会签、专项覆盖日常。

运行：cd backend && python3 tests/test_countersign.py
"""
from __future__ import annotations

import concurrent.futures
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.countersign import service
from app.store import store

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def new_meeting(special=72.0, daily=88.0):
    meeting, missing = service.open_meeting({
        "检测编号": f"TEST-{len(store.rows('countersign')) + 1:03d}",
        "桥梁名称": f"测试桥{len(store.rows('countersign')) + 1}",
        "检查员": "测试检查员",
        "专项评分": special,
        "日常评分": daily,
    })
    assert not missing
    return meeting


def to_conclusion(meeting, conclusion="限载通行", token=None, signer=None):
    service.add_evidence(meeting["id"], {
        "角色": "检查员", "部位": "主梁", "病害类型": "裂缝", "x": 40, "y": 55,
    })
    service.run_action(meeting["id"], "检查员提交证据", {"角色": "检查员"})
    return service.submit_decision(meeting["id"], {
        "角色": "主管", "结论": conclusion,
        "token": token or f"T-{meeting['会签号']}",
        "signer": signer or "测试主管",
    })


print("1. 单向状态图：正向推进、跳步与回退都被拒绝")
m = new_meeting()
check("无证据不能提交", service.run_action(m["id"], "检查员提交证据", {"角色": "检查员"})[0] is None)
service.add_evidence(m["id"], {"角色": "检查员", "部位": "桥面铺装", "病害类型": "车辙", "x": 10})
mm, msg, _ = service.run_action(m["id"], "检查员提交证据", {"角色": "检查员"})
check("提交证据 → 待结论", mm["status"] == "待结论", msg)
check("检查员不能下结论", service.submit_decision(m["id"], {"角色": "检查员", "结论": "正常通行", "token": "X"})[0] is None)
check("待结论不能直接派工", service.run_action(m["id"], "生成工程任务", {"角色": "主管"})[0] is None)
check("重复提交证据被拒", service.run_action(m["id"], "检查员提交证据", {"角色": "检查员"})[0] is None)
m2, msg, data = service.submit_decision(m["id"], {"角色": "主管", "结论": "限载通行", "token": "T1", "signer": "甲"})
check("主管签认 → 已会签", m2["status"] == "已会签", msg)
check("第二份结论不能覆盖（只放行一份决定）", service.submit_decision(m["id"], {"角色": "主管", "结论": "封闭重建", "token": "T2", "signer": "乙"})[0] is None)
check("结论仍是第一份", m["conclusion"] == "限载通行")
m3, msg, _ = service.run_action(m["id"], "生成工程任务", {"角色": "主管"})
check("生成工程任务 → 已派单（终态）", m3["status"] == "已派单", msg)
check("已派单不可再次派工", service.run_action(m["id"], "生成工程任务", {"角色": "主管"})[0] is None)

print("2. 专项评分与日常评分冲突时以专项会签为准")
m = new_meeting(special=72.0, daily=88.0)  # 专项3类、日常2类
mm, _, _ = to_conclusion(m, "限载通行", token="C1")
check("冲突被标记", mm["等级冲突"] is True)
check("专项建议3类", mm["专项建议等级"] == "3类")
check("日常2类", mm["日常评分等级"] == "2类")
check("最终评定取会签结论3类", mm["评定等级"] == "3类")
info = store.find("bridge_info", mm["links"]["bridge_info_id"])
check("既有桥位历史等级仍保留", info["上次评定等级"] in ("2类", "3类"))
check("专项会签等级另挂不覆盖历史", info["专项会签等级"] == "3类")

print("3. 会签结果同步定检档案、巡检通知、工程清单")
bridge = store.find("bridge", mm["links"]["bridge_id"])
check("定检档案已同步结论", bridge["限载结论"] == "限载通行" and bridge["会签号"] == mm["会签号"])
notice = store.find("patrol", mm["links"]["patrol_id"])
check("巡检通知已生成", notice is not None and "会签巡检通知" in notice["巡查状态"])
before_projects = len(store.rows("project"))
service.run_action(mm["id"], "生成工程任务", {"角色": "主管"})
check("派工后工程清单 +1", len(store.rows("project")) == before_projects + 1)
check("档案在派工后归档", store.find("bridge", mm["links"]["bridge_id"])["status"] == "已归档")

print("4. 撤回只能生成新会议，旧会议留终态、同步落点标记作废")
old_no = mm["会签号"]
nm, msg, extra = service.run_action(mm["id"], "撤回并新会签", {"角色": "主管"})
check("撤回成功并生成新会议", nm is not None and nm["status"] == "待证据", msg)
check("新会议带上来源会签号", nm["supersedes"] == old_no and nm["links"]["source_meeting_id"] == mm["id"])
check("旧会议为已撤回终态", mm["status"] == "已撤回")
check("旧会议不能再下结论", service.submit_decision(mm["id"], {"角色": "主管", "结论": "正常通行", "token": "Z"})[0] is None)
check("旧巡检通知作废", store.find("patrol", mm["links"]["patrol_id"])["status"] == "通知作废")
check("旧工程派工停止", store.find("project", mm["links"]["project_id"])["status"] == "已撤回")
check("待证据阶段不能撤回", service.run_action(nm["id"], "撤回并新会签", {"角色": "主管"})[0] is None)
check("非主管不能撤回", service.run_action(m["id"], "撤回并新会签", {"角色": "检查员"})[0] is None)

print("5. 多人同时签认：事务锁只放行一份决定，其余排队不中签")
m = new_meeting()
service.add_evidence(m["id"], {"角色": "检查员", "部位": "主梁", "病害类型": "斜裂缝"})
service.run_action(m["id"], "检查员提交证据", {"角色": "检查员"})
conclusions = ["正常通行", "限速通行", "限载通行", "停用交通管制", "封闭重建"]


def decide(i: int):
    return service.submit_decision(m["id"], {
        "角色": "主管", "结论": conclusions[i % len(conclusions)],
        "token": f"PARALLEL-{i}", "signer": f"主管{i}",
    })


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(decide, range(8)))
released = [r for r in results if r[2] and r[2]["state"] == "放行决定"]
queued = [r for r in results if r[2] and r[2]["state"] == "排队中"]
check("恰好一份决定放行", len(released) == 1, f"实际 {len(released)}")
check("其余并发签认进入队列", len(queued) == 7, f"实际 {len(queued)}")
winner = released[0][2]["token"]
check("会议只形成一个结论", m["conclusion"] == conclusions[int(winner.split('-')[1]) % 5])
check("队列全部已结算且只有一个放行", sum(1 for e in m["queue"] if e["state"] == "放行决定") == 1)
check("队列 7 份未中签", sum(1 for e in m["queue"] if e["state"] == "未中签") == 7)

print("6. 连接中断：凭会签号与令牌恢复队列/结果，同令牌重放幂等")
r1 = service.recover_queue(m["会签号"])
check("凭会签号恢复队列快照", r1[0] is not None and len(r1[2]["queue"]) == 8)
winner_entry = next(e for e in m["queue"] if e["state"] == "放行决定")
loser_entry = next(e for e in m["queue"] if e["state"] == "未中签")
rm, msg, data = service.recover_queue(m["会签号"], winner_entry["token"])
check("赢家令牌恢复最终决定", data["state"] == "放行决定" and data["结论"] == m["conclusion"])
rm, msg, data = service.recover_queue(m["会签号"], loser_entry["token"])
check("输家令牌恢复未中签且不会改结论", data["state"] == "未中签" and m["conclusion"] == m["conclusion"])
m_before, _, _ = service.recover_queue(m["会签号"], winner_entry["token"])
check("同令牌重放幂等，不新增队列项", len(m["queue"]) == 8)
_, msg, _ = service.recover_queue("HQ-9999-9999")
check("不存在的会签号给可读错误", "不存在" in msg)

print("7. 风险看板随结论更新")
board = service.risk_board()
labels = {c["label"] for c in board["cards"]}
check("看板含核心指标", {"较高风险及以上桥梁", "限载/管制桥梁", "会签已派工程", "专项覆盖日常（等级冲突）"} <= labels)
check("风险分布五档齐全", set(board["risk_counts"]) == {"低", "中", "较高", "高", "极高"})
check("看板条目都是已定论会议", all(it["限载结论"] for it in board["items"]))
check("看板按风险倒序", [board["items"].index(it) for it in board["items"]] == list(range(len(board["items"]))))
risks = [{"低": 0, "中": 1, "较高": 2, "高": 3, "极高": 4}[it["风险"]] for it in board["items"]]
check("看板风险等级单调不增", all(a >= b for a, b in zip(risks, risks[1:])))

print(f"\n结果：{PASS} 通过，{FAIL} 失败")
sys.exit(1 if FAIL else 0)
