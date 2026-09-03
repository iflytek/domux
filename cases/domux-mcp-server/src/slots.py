# -*- coding: utf-8 -*-
"""
slots.py — Domux 七字段槽位的统一定义与校验

Domux（HF: iFlytekOpenSource/Domux，Gemma 基座 2B）把自然语言指令解析为 7 个槽位：
    action | device | attribute | value | unit | room | floor

示例：
    "把客厅空调调到26度"
      -> action=set_temperature, device=air_conditioner, attribute=temperature,
         value=26, unit=celsius, room=living_room, floor=floor_1

本模块是全项目的"契约层"：MockBackend / VLLMBackend / 风险引擎 / HA 适配层
都依赖这里的常量与校验函数，保证各组件之间槽位语义一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 槽位合法枚举值（对齐 Domux 官方 COMMAND_SPEC 的常用子集）
# ---------------------------------------------------------------------------

#: 动作集合：日常控制 + 高危动作（高危动作由 auth_middleware 单独拦截）
ACTIONS = {
    # --- 日常控制 ---
    "turn_on",              # 打开设备
    "turn_off",             # 关闭设备
    "set_temperature",      # 设置温度（空调/暖气/冰箱等）
    "set_brightness",       # 设置亮度
    "set_volume",           # 设置音量
    "set_speed",            # 设置风速/档位
    "set_position",         # 设置位置百分比（窗帘开合度等）
    "open",                 # 打开（窗帘/门窗类）
    "close",                # 关闭（窗帘/门窗类）
    "lock_door",            # 上锁门锁
    "query_status",         # 查询状态
    "play_media",           # 播放媒体
    "pause_media",          # 暂停播放
    "start_cleaning",       # 启动清扫
    "return_dock",          # 回充
    # --- 高危动作（必须二次确认，见 HIGH_RISK_ACTIONS）---
    "unlock_door",          # 解锁门锁
    "disarm_security",      # 安防撤防
    "gas_valve_off",        # 关闭燃气阀
    "gas_valve_on",         # 开启燃气阀
}

#: 高危操作清单：任何角色触发后都只能进入 pending_confirmation，
#: 必须由 owner 角色完成二次确认后才真正下发执行。
HIGH_RISK_ACTIONS = {
    "unlock_door",
    "disarm_security",
    "gas_valve_off",
    "gas_valve_on",
}

DEVICES = {
    "light",               # 灯
    "air_conditioner",     # 空调
    "curtain",             # 窗帘
    "door_lock",           # 门锁
    "camera",              # 摄像头
    "speaker",             # 智能音箱
    "tv",                  # 电视
    "fan",                 # 风扇
    "humidifier",          # 加湿器
    "air_purifier",        # 空气净化器
    "robot_vacuum",        # 扫地机器人
    "water_heater",        # 热水器
    "refrigerator",        # 冰箱
    "washing_machine",     # 洗衣机
    "security_panel",      # 安防主机
    "gas_valve",           # 燃气阀
    "smoke_detector",      # 烟感
    "water_leak_sensor",   # 水浸传感器
    "window",              # 窗户
    "socket",              # 插座
}

UNITS = {
    "celsius", "fahrenheit",       # 温度
    "percent",                      # 百分比（亮度/音量/位置/湿度）
    "minutes", "hours",             # 时长
    "rpm", "level",                 # 转速 / 档位
}

FLOORS = {"floor_b1", "floor_1", "floor_2", "floor_3", "floor_4", "floor_5"}

ROOMS = {
    "living_room",   # 客厅
    "bedroom",       # 卧室
    "master_bedroom",# 主卧
    "kitchen",       # 厨房
    "bathroom",      # 浴室
    "study",         # 书房
    "balcony",       # 阳台
    "dining_room",   # 餐厅
    "entryway",      # 玄关
    "garage",        # 车库
    "hallway",       # 走廊
    "garden",        # 花园
}

#: 七个槽位的标准键顺序
SLOT_KEYS = ["action", "device", "attribute", "value", "unit", "room", "floor"]


def empty_slots() -> Dict[str, Optional[Any]]:
    """返回一份全部置空的七字段槽位模板。"""
    return {
        "action": None,
        "device": None,
        "attribute": None,
        "value": None,
        "unit": None,
        "room": None,
        "floor": None,
    }


def validate_slots(slots: Dict[str, Any], strict: bool = False) -> List[str]:
    """
    校验槽位 JSON 是否符合 Domux 输出契约。

    :param slots: 七字段槽位字典
    :param strict: True 时同时校验枚举合法性；False 只校验结构完整性
    :return: 问题列表，为空表示通过
    """
    problems: List[str] = []
    if not isinstance(slots, dict):
        return ["槽位必须是 dict 类型"]
    for key in SLOT_KEYS:
        if key not in slots:
            problems.append(f"缺少必需槽位: {key}")

    if strict:
        if slots.get("action") is not None and slots["action"] not in ACTIONS:
            problems.append(f"非法 action: {slots['action']}")
        if slots.get("device") is not None and slots["device"] not in DEVICES:
            problems.append(f"非法 device: {slots['device']}")
        if slots.get("unit") is not None and slots["unit"] not in UNITS:
            problems.append(f"非法 unit: {slots['unit']}")
        if slots.get("room") is not None and slots["room"] not in ROOMS:
            problems.append(f"非法 room: {slots['room']}")
        if slots.get("floor") is not None and slots["floor"] not in FLOORS:
            problems.append(f"非法 floor: {slots['floor']}")

    # 高危动作必须带 device 归属，便于权限引擎判断作用域
    if slots.get("action") in HIGH_RISK_ACTIONS and not slots.get("device"):
        problems.append("高危动作缺少 device 槽位")

    return problems


def is_high_risk(slots: Dict[str, Any]) -> bool:
    """判断一组槽位是否命中高危操作清单。"""
    return slots.get("action") in HIGH_RISK_ACTIONS


def slots_summary(slots: Dict[str, Any]) -> str:
    """生成一行可读摘要，用于审计日志打印。"""
    parts = [f"{k}={slots.get(k)}" for k in SLOT_KEYS]
    return ", ".join(parts)
