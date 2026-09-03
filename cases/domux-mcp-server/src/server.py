# -*- coding: utf-8 -*-
"""
server.py — Domux MCP Server 主入口（基于官方 mcp python-sdk 的 FastMCP）

暴露三个标准 MCP 工具：
    parse_command(text)   : 单条自然语言 -> 七字段槽位 JSON
    batch_parse(texts)    : 批量解析
    health_check()        : 后端健康状态

启动方式：
    # stdio 模式（供 Claude Desktop / Cline / 扣子等 MCP 宿主接入）
    python server.py

    # 或用官方 CLI 调试
    mcp dev server.py

环境变量：
    DOMUX_BACKEND       mock | vllm      （默认 mock）
    DOMUX_VLLM_BASE_URL vLLM 服务地址    （默认 http://127.0.0.1:8000/v1）
    DOMUX_VLLM_MODEL    模型名           （默认 iFlytekOpenSource/Domux）
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

# 保证直接 `python server.py` 与包内导入两种方式都能找到同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # type: ignore

from backend import ParseBackend, create_backend  # noqa: E402
from slots import (  # noqa: E402
    HIGH_RISK_ACTIONS,
    SLOT_KEYS,
    slots_summary,
    validate_slots,
)

# ---------------------------------------------------------------------------
# 全局单例：进程生命周期内复用一个后端实例（vLLM 场景避免重复建连）
# ---------------------------------------------------------------------------
_BACKEND: ParseBackend = create_backend()

#: 项目版本号（与比赛提交对应）
SERVER_VERSION = "0.1.0"

mcp = FastMCP(
    "domux-smart-home",
    instructions=(
        "Domux 智能家居指令解析 MCP Server。"
        "把中文自然语言指令解析为 action/device/attribute/value/unit/room/floor "
        "七字段槽位 JSON，可直接对接 Home Assistant 等家居平台。"
        "高危动作（开门锁/安防撤防/燃气阀）会带 high_risk=true 标记，"
        "上层应用必须走二次确认流程。"
    ),
)


def _enrich(result: Dict[str, Any]) -> Dict[str, Any]:
    """在解析结果上补充跨组件约定的元数据字段。"""
    slots = result.get("slots", {})
    result["high_risk"] = slots.get("action") in HIGH_RISK_ACTIONS
    result["slot_errors"] = validate_slots(slots, strict=False)
    return result


@mcp.tool()
def parse_command(text: str) -> str:
    """把一条智能家居自然语言指令解析为七字段槽位 JSON。

    Args:
        text: 中文指令，例如“把客厅空调调到26度”“给玄关门锁解锁放快递进来”

    Returns:
        JSON 字符串，包含 slots(七字段)、confidence、high_risk、warnings 等。
    """
    result = _BACKEND.parse(text)
    return json.dumps(_enrich(result), ensure_ascii=False, indent=2)


@mcp.tool()
def batch_parse(texts: List[str]) -> str:
    """批量解析多条智能家居指令。

    Args:
        texts: 指令列表，例如 ["打开客厅灯", "空调调到22度"]

    Returns:
        JSON 字符串：{"count": n, "results": [逐条解析结果]}
    """
    results = [_enrich(r) for r in _BACKEND.parse_batch(list(texts))]
    summary = {
        "count": len(results),
        "backend": _BACKEND.name,
        "results": results,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.tool()
def health_check() -> str:
    """检查 Domux 解析后端健康状态（mock 恒为 ok；vllm 会探测服务可达性）。"""
    info = _BACKEND.health_check()
    info.update({
        "server_version": SERVER_VERSION,
        "slot_schema": SLOT_KEYS,
        "high_risk_actions": sorted(HIGH_RISK_ACTIONS),
    })
    return json.dumps(info, ensure_ascii=False, indent=2)


@mcp.resource("domux://schema/slots")
def slot_schema() -> str:
    """MCP 资源：七字段槽位的枚举说明，供宿主 LLM 学习调用约定。"""
    from slots import ACTIONS, DEVICES, FLOORS, ROOMS, UNITS
    doc = {
        "description": "Domux 七字段槽位契约",
        "keys": SLOT_KEYS,
        "enums": {
            "action": sorted(ACTIONS),
            "device": sorted(DEVICES),
            "unit": sorted(UNITS),
            "room": sorted(ROOMS),
            "floor": sorted(FLOORS),
        },
        "example": {
            "input": "把客厅空调调到26度",
            "slots": {
                "action": "set_temperature", "device": "air_conditioner",
                "attribute": "temperature", "value": 26, "unit": "celsius",
                "room": "living_room", "floor": "floor_1",
            },
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # stdio 传输：MCP 宿主通过子进程 stdin/stdout 通信
    mcp.run()
