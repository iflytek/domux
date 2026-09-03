# -*- coding: utf-8 -*-
"""
test_server.py — domux-mcp-server 冒烟测试

覆盖三层：
    1. 槽位契约层 slots.py     ：结构校验 / 枚举校验 / 高危标记
    2. 推理后端层 backend.py   ：MockBackend 规则解析 / 后端工厂切换
    3. MCP 服务层 server.py    ：三个工具函数的输入输出契约

运行：python test_server.py   （无需 GPU、无需网络）
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import MockBackend, VLLMBackend, create_backend  # noqa: E402
from slots import (  # noqa: E402
    HIGH_RISK_ACTIONS, empty_slots, validate_slots,
)

PASS, FAIL = "✅", "❌"
_results = {"passed": 0, "failed": 0}


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _results["passed"] += 1
        print(f"  {PASS} {name}")
    else:
        _results["failed"] += 1
        print(f"  {FAIL} {name}  {detail}")


# ===========================================================================
print("=" * 62)
print("[1/3] slots.py — 槽位契约")
print("=" * 62)
good = dict(action="set_temperature", device="air_conditioner",
            attribute="temperature", value=26, unit="celsius",
            room="living_room", floor="floor_1")
check("合法七字段通过校验", validate_slots(good) == [])
check("缺字段被检出", len(validate_slots({k: v for k, v in good.items() if k != "unit"})) == 1)
bad_enum = dict(good, action="fly_to_moon")
check("非法枚举(strict)被检出", len(validate_slots(bad_enum, strict=True)) >= 1)
check("高危动作识别", good | {"action": "unlock_door"} and
      (lambda s: s["action"] in HIGH_RISK_ACTIONS)({**good, "action": "unlock_door"}))
check("高危动作必须有device", any("device" in p for p in
      validate_slots({**empty_slots(), "action": "unlock_door", "device": None})))

# ===========================================================================
print()
print("=" * 62)
print("[2/3] backend.py — MockBackend 解析与工厂切换")
print("=" * 62)
mb = MockBackend()

CASES = [
    # (输入, 期望断言字典: slot->value)
    ("把客厅空调调到26度", {"action": "set_temperature", "device": "air_conditioner",
                            "room": "living_room", "value": 26.0, "unit": "celsius"}),
    ("打开主卧的灯", {"action": "turn_on", "device": "light", "room": "master_bedroom"}),
    ("把窗帘开到50%", {"action": "set_position", "device": "curtain", "value": 50.0}),
    ("帮我锁门", {"action": "lock_door", "device": "door_lock"}),
    ("给玄关门锁解锁放快递进来", {"action": "unlock_door", "device": "door_lock",
                                  "room": "entryway"}),
    ("关闭厨房燃气阀门", {"action": "gas_valve_off", "device": "gas_valve",
                          "room": "kitchen"}),
    ("客厅空调制热到24度", {"action": "set_temperature", "attribute": "hvac_mode",
                            "value": "heat"}),
    ("安防撤防", {"action": "disarm_security"}),
    ("扫地机器人开始清扫", {"action": "start_cleaning", "device": "robot_vacuum"}),
]
for text, expect in CASES:
    r = mb.parse(text)
    ok = all(r["slots"].get(k) == v for k, v in expect.items())
    check(f"解析[{text}]", ok,
          f"got={json.dumps(r['slots'], ensure_ascii=False)}")

batch = mb.parse_batch([c[0] for c in CASES])
check("批量解析条数一致", len(batch) == len(CASES))
check("所有结果含7槽位+延迟元数据",
      all(set(r["slots"]) == {"action", "device", "attribute", "value", "unit",
                              "room", "floor"} and "latency_ms" in r for r in batch))

# 工厂：mock / vllm / 非法值
check("工厂创建 mock", create_backend("mock").name == "mock")
vb = create_backend("vllm")
check("工厂创建 vllm(仅构造不请求)", isinstance(vb, VLLMBackend)
      and vb.base_url.endswith("/v1"))
hc = vb.health_check()  # 无服务时应优雅返回 unreachable 而非抛异常
check("vLLM健康检查离线降级", hc.get("status") == "unreachable" or hc.get("status") == "ok")
try:
    create_backend("nope")
    check("非法后端名报错", False)
except ValueError:
    check("非法后端名报错", True)

# 环境变量切换
os.environ["DOMUX_BACKEND"] = "vllm"
check("环境变量DOMUX_BACKEND=vllm生效", create_backend().name == "vllm")
os.environ["DOMUX_BACKEND"] = "mock"
check("环境变量DOMUX_BACKEND=mock生效", create_backend().name == "mock")

# ===========================================================================
print()
print("=" * 62)
print("[3/3] server.py — MCP 工具契约（直接调用工具函数）")
print("=" * 62)
import server  # noqa: E402


def raw(tool):
    """FastMCP 装饰后原函数存于 .fn 属性；兼容不同版本。"""
    return getattr(tool, "fn", tool)


out1 = json.loads(raw(server.parse_command)("把客厅灯打开"))
check("parse_command 返回slots", out1["slots"]["device"] == "light"
      and out1["slots"]["action"] == "turn_on")
check("parse_command 附带high_risk标记", out1["high_risk"] is False)

out2 = json.loads(raw(server.parse_command)("给玄关门锁解锁"))
check("高危指令 high_risk=true", out2["high_risk"] is True
      and out2["slots"]["action"] == "unlock_door")

out3 = json.loads(raw(server.batch_parse)(["打开客厅灯", "安防撤防"]))
check("batch_parse 计数正确", out3["count"] == 2)
check("batch_parse 第二条为高危", out3["results"][1]["high_risk"] is True)

out4 = json.loads(raw(server.health_check)())
check("health_check 状态ok", out4["status"] == "ok"
      and out4["backend"] == "mock")
check("health_check 暴露高危清单", "unlock_door" in out4["high_risk_actions"])

schema = json.loads(raw(server.slot_schema)())
check("slot_schema 资源可读", schema["keys"][0] == "action"
      and "set_temperature" in schema["enums"]["action"])

# ===========================================================================
print()
print("=" * 62)
total, failed = (_results["passed"], _results["failed"])
print(f"冒烟测试完成：{PASS} 通过 {total} 项，{FAIL} 失败 {failed} 项")
print("=" * 62)
sys.exit(1 if failed else 0)
