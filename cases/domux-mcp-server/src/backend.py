# -*- coding: utf-8 -*-
"""
backend.py — Domux 推理后端的抽象层

设计目标：
    MCP Server 的三个工具（parse_command / batch_parse / health_check）
    不直接依赖任何具体推理框架，而是通过 ParseBackend 接口访问模型能力。
    通过环境变量 `DOMUX_BACKEND=mock|vllm` 一键切换：

        mock : MockBackend   —— 纯 Python 规则匹配模拟，无需 GPU，
                                用于开发、CI 与比赛演示（离线可复现）
        vllm : VLLMBackend   —— 通过 OpenAI 兼容 HTTP 接口调用
                                vLLM/SGLang 部署的 Domux 权重
                               （HF: iFlytekOpenSource/Domux）

    切换示例：
        export DOMUX_BACKEND=vllm
        export DOMUX_VLLM_BASE_URL=http://127.0.0.1:8000/v1
        export DOMUX_VLLM_MODEL=iFlytekOpenSource/Domux
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

try:  # 允许 backend.py 被独立拷贝使用；slots 缺失时给出降级常量
    from slots import (  # type: ignore
        ACTIONS, DEVICES, FLOORS, ROOMS, UNITS,
        empty_slots, validate_slots,
    )
except ImportError:  # pragma: no cover - 仅在脱离包结构时触发
    from .slots import (
        ACTIONS, DEVICES, FLOORS, ROOMS, UNITS,
        empty_slots, validate_slots,
    )


# ===========================================================================
# 抽象接口
# ===========================================================================

class ParseBackend(ABC):
    """Domux 指令解析后端的统一接口。"""

    name: str = "base"

    @abstractmethod
    def parse(self, text: str) -> Dict[str, Any]:
        """
        把一条自然语言指令解析为七字段槽位。

        :param text: 自然语言指令，如 "把客厅空调调到26度"
        :return: {
            "text": 原始输入,
            "slots": {action, device, attribute, value, unit, room, floor},
            "confidence": float,
            "backend": 后端名,
            "latency_ms": 解析耗时,
            "warnings": [非致命告警],
        }
        """

    def parse_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """默认批量实现 = 逐条 parse；vLLM 后端可覆写为真批量。"""
        return [self.parse(t) for t in texts]

    def health_check(self) -> Dict[str, Any]:
        """健康检查：后端是否可用。"""
        return {"backend": self.name, "status": "ok"}


# ===========================================================================
# MockBackend —— 规则匹配模拟（无 GPU 可跑通全链路）
# ===========================================================================

class MockBackend(ParseBackend):
    """
    用关键词规则模拟 Domux 的槽位抽取能力。

    说明：这不是对模型的仿真精度负责，而是对**输出契约**负责——
    保证返回结构与真实 Domux 完全一致（7 字段 JSON），让上层
    权限/风险/HA 映射代码可以在无 GPU 环境下完整开发与测试。
    """

    name = "mock"

    # --- 关键词 -> 槽位 规则表 ---
    _ACTION_RULES = [
        # (正则, action, 默认device, 默认attribute)
        # 注意：制热/制冷必须排在通用温度规则之前；
        # (?<!空) 防止“空调”的“调”字误触发温度调节规则
        (r"制热", "set_temperature", "air_conditioner", "hvac_mode"),
        (r"制冷", "set_temperature", "air_conditioner", "hvac_mode"),
        (r"(?<!空)(调到?|设置).{0,8}(温度|度)", "set_temperature", "air_conditioner", "temperature"),
        (r"(开灯|打开.{0,6}灯|灯.{0,4}打开)", "turn_on", "light", None),
        (r"(关灯|关闭.{0,6}灯|灯.{0,4}关闭)", "turn_off", "light", None),
        (r"(打开|开启).{0,6}(窗帘|卷帘)", "open", "curtain", None),
        (r"(关闭|拉上).{0,6}(窗帘|卷帘)", "close", "curtain", None),
        (r"窗帘.{0,6}开到?.{0,4}\d+", "set_position", "curtain", "position"),
        (r"(打开|开一下).{0,6}窗", "open", "window", None),
        (r"(关闭|关上).{0,6}窗", "close", "window", None),
        (r"锁门|门锁.{0,4}(上锁|锁上)|把门锁上", "lock_door", "door_lock", None),
        (r"开门锁|解锁|门锁.{0,4}打开|把门打开", "unlock_door", "door_lock", None),  # 高危
        (r"撤防|解除.{0,4}布防|关闭安防", "disarm_security", "security_panel", None),  # 高危
        (r"布防|启动安防|打开安防", "turn_on", "security_panel", None),
        (r"燃气阀.{0,4}(关闭|关掉|切断)|(关闭|关掉|切断).{0,4}燃气", "gas_valve_off", "gas_valve", None),  # 高危
        (r"燃气阀.{0,4}(打开|开启)|(打开|开启).{0,4}燃气阀", "gas_valve_on", "gas_valve", None),      # 高危
        (r"(打开|开启).{0,6}(空调|冷气)", "turn_on", "air_conditioner", None),
        (r"(关闭|关掉).{0,6}(空调|冷气)", "turn_off", "air_conditioner", None),
        (r"(打开|开启).{0,6}(电视|TV|tv)", "turn_on", "tv", None),
        (r"(关闭|关掉).{0,6}(电视|TV|tv)", "turn_off", "tv", None),
        (r"亮度.{0,6}(调到?|设置为?)", "set_brightness", "light", "brightness"),
        (r"音量.{0,6}(调到?|设置为?)", "set_volume", "speaker", "volume"),
        (r"(风速|风量).{0,6}(调到?|设置为?)|(调到?).{0,4}(高风|中风|低风|自动风)", "set_speed", "fan", "speed"),
        (r"(播放|来一[点首]).*(音乐|歌|歌曲)", "play_media", "speaker", None),
        (r"(暂停|停止)(播放|音乐|歌)", "pause_media", "speaker", None),
        (r"开始清扫|打扫|扫地", "start_cleaning", "robot_vacuum", None),
        (r"回充|回去充电", "return_dock", "robot_vacuum", None),
        (r"查询|看看|检查.*(状态|情况)|什么状态|多少度", "query_status", None, None),
    ]

    _DEVICE_KEYWORDS = [
        (r"空调|冷气", "air_conditioner"), (r"灯", "light"),
        (r"窗帘|卷帘", "curtain"), (r"门锁", "door_lock"),
        (r"摄像头|监控", "camera"), (r"音箱|音响", "speaker"),
        (r"电视|TV|tv", "tv"), (r"风扇", "fan"),
        (r"加湿器", "humidifier"), (r"净化器", "air_purifier"),
        (r"扫地机器人|扫拖机", "robot_vacuum"), (r"热水器", "water_heater"),
        (r"冰箱", "refrigerator"), (r"洗衣机", "washing_machine"),
        (r"安防|报警主机", "security_panel"), (r"燃气", "gas_valve"),
        (r"烟感|烟雾", "smoke_detector"), (r"水浸|漏水", "water_leak_sensor"),
        (r"窗户", "window"), (r"插座", "socket"),
    ]

    _ROOM_KEYWORDS = [
        (r"主卧", "master_bedroom"), (r"卧室", "bedroom"),
        (r"客厅|大厅", "living_room"), (r"厨房", "kitchen"),
        (r"卫生间|浴室", "bathroom"), (r"书房", "study"),
        (r"阳台", "balcony"), (r"餐厅", "dining_room"),
        (r"玄关|门口|进门", "entryway"), (r"车库", "garage"),
        (r"走廊|过道", "hallway"), (r"花园|院子", "garden"),
    ]

    _FLOOR_KEYWORDS = [
        (r"B1|地下一层|负一层", "floor_b1"),
        (r"[一二1]楼|[一二1]层", "floor_1"),
        (r"[二2]楼|[二2]层", "floor_2"),
        (r"[三3]楼|[三3]层", "floor_3"),
        (r"[四4]楼|[四4]层", "floor_4"),
        (r"[五5]楼|[五5]层", "floor_5"),
    ]

    _UNIT_KEYWORDS = [
        (r"摄氏|℃|°C", "celsius"), (r"华氏|℉|°F", "fahrenheit"),
        (r"%|百分之", "percent"), (r"分钟", "minutes"), (r"小时", "hours"),
        (r"档", "level"),
    ]

    _SPEED_MAP = {"高风": "high", "中风": "medium", "低风": "low", "自动风": "auto"}

    def __init__(self, default_floor: str = "floor_1") -> None:
        self.default_floor = default_floor

    # ------------------------------------------------------------------
    def _match_action(self, text: str):
        for pattern, action, device, attribute in self._ACTION_RULES:
            if re.search(pattern, text):
                return action, device, attribute
        return None, None, None

    def _match_first(self, text: str, rules):
        for pattern, value in rules:
            if re.search(pattern, text):
                return value
        return None

    def _extract_number(self, text: str) -> Optional[float]:
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        return float(m.group(1)) if m else None

    # ------------------------------------------------------------------
    def parse(self, text: str) -> Dict[str, Any]:
        t0 = time.time()
        warnings: List[str] = []
        slots = empty_slots()
        text = (text or "").strip()

        action, device_hint, attribute = self._match_action(text)
        device = self._match_first(text, self._DEVICE_KEYWORDS) or device_hint
        room = self._match_first(text, self._ROOM_KEYWORDS)
        floor = self._match_first(text, self._FLOOR_KEYWORDS) or self.default_floor
        unit = self._match_first(text, self._UNIT_KEYWORDS)
        number = self._extract_number(text)

        # --- 细化修正（注意先判 hvac_mode，再走普通 set_temperature）---
        if attribute == "hvac_mode":
            # “制热/制冷”：value 承载模式语义；若同时带温度数字则提示契约限制
            mode = "heat" if re.search(r"制热|暖", text) else "cool"
            slots["attribute"], slots["value"], slots["unit"] = "hvac_mode", mode, None
            if number is not None:
                warnings.append(
                    f"复合指令：目标温度 {number} 度因7槽位契约限制未单独保存")
        elif action == "set_temperature":
            unit = unit if unit in ("celsius", "fahrenheit") else "celsius"
            slots["value"] = number
        elif action in ("set_brightness", "set_volume", "set_position"):
            slots["value"] = number
            slots["unit"] = unit or "percent"
        elif action == "set_speed":
            for cn, en in self._SPEED_MAP.items():
                if cn in text:
                    slots["attribute"], slots["value"] = "speed", en
                    break
            else:
                slots["attribute"], slots["value"] = "speed", number
            slots["unit"] = "level"
        elif action in ("open", "close") and device == "curtain" and number is not None:
            # “窗帘开到50%”这类带百分比的开合指令
            action = "set_position"
            slots["attribute"], slots["value"], slots["unit"] = "position", number, "percent"

        if action is None:
            warnings.append("MOCK_FALLBACK: 未匹配到动作规则，按 query_status 处理")
            action, device = "query_status", device or "camera"

        slots.update({
            "action": action,
            "device": device,
            "attribute": slots.get("attribute") or attribute,
            "unit": slots.get("unit") or unit,
            "room": room,
            "floor": floor,
        })

        problems = validate_slots(slots, strict=False)
        if problems:
            warnings.extend(problems)

        confidence = 0.60 if warnings else 0.92
        return {
            "text": text,
            "slots": slots,
            "confidence": round(confidence, 2),
            "backend": self.name,
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "warnings": warnings,
        }


# ===========================================================================
# VLLMBackend —— OpenAI 兼容接口（预留，不安装 torch/vllm 本体）
# ===========================================================================

class VLLMBackend(ParseBackend):
    """
    通过 vLLM / SGLang 的 **OpenAI Chat Completions 兼容接口** 调用 Domux。

    部署参考（正式接入时执行，本仓库不安装重型依赖）：
        pip install vllm
        vllm serve iFlytekOpenSource/Domux --revision <commit-sha> \
            --port 8000 --guided-decoding-backend outlines

    环境变量：
        DOMUX_VLLM_BASE_URL  默认 http://127.0.0.1:8000/v1
        DOMUX_VLLM_MODEL     默认 iFlytekOpenSource/Domux
        DOMUX_VLLM_API_KEY   默认 EMPTY（vLLM 本地部署通常不校验）
    """

    name = "vllm"

    #: 结构化输出提示词：要求模型只输出七字段 JSON（与官方 COMMAND_SPEC 对齐）
    SYSTEM_PROMPT = (
        "你是智能家居指令解析引擎 Domux。把用户指令解析为且仅输出一个 JSON 对象，"
        '包含七个字段："action","device","attribute","value","unit","room","floor"。'
        "action/device/unit/room/floor 只能取给定枚举值，未知字段填 null，不要输出其他内容。\n"
        f"ACTIONS={sorted(ACTIONS)}\nDEVICES={sorted(DEVICES)}\nUNITS={sorted(UNITS)}\n"
        f"ROOMS={sorted(ROOMS)}\nFLOORS={sorted(FLOORS)}"
    )

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = (base_url or os.getenv("DOMUX_VLLM_BASE_URL",
                          "http://127.0.0.1:8000/v1")).rstrip("/")
        self.model = model or os.getenv("DOMUX_VLLM_MODEL", "iFlytekOpenSource/Domux")
        self.api_key = api_key or os.getenv("DOMUX_VLLM_API_KEY", "EMPTY")
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _chat(self, user_text: str) -> str:
        """调用 OpenAI 兼容接口（用标准库 urllib，避免强依赖 openai SDK）。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.0,
            "max_tokens": 128,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _extract_json(content: str) -> Dict[str, Any]:
        """从模型输出中鲁棒地抠出第一个 JSON 对象。"""
        content = content.strip()
        # 去掉 ```json ... ``` 围栏
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            raise ValueError(f"模型输出中没有 JSON: {content[:120]}")
        return json.loads(m.group(0))

    # ------------------------------------------------------------------
    def parse(self, text: str) -> Dict[str, Any]:
        t0 = time.time()
        warnings: List[str] = []
        raw = self._chat(text)
        try:
            slots = self._extract_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"JSON 解析失败: {exc}")
            slots = empty_slots()

        for key, val in list(slots.items()):
            if isinstance(val, str) and val.lower() in ("null", "none", ""):
                slots[key] = None
        problems = validate_slots(slots, strict=True)
        warnings.extend(problems)

        return {
            "text": text,
            "slots": slots,
            "confidence": 0.99 if not problems else 0.55,
            "backend": self.name,
            "raw_output": raw,
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "warnings": warnings,
        }

    def health_check(self) -> Dict[str, Any]:
        """探测 vLLM 服务 /models 是否可达。"""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                ok = resp.status == 200
            return {"backend": self.name, "status": "ok" if ok else "degraded",
                    "base_url": self.base_url}
        except Exception as exc:  # noqa: BLE001 - 健康检查需要吞掉所有网络错误
            return {"backend": self.name, "status": "unreachable",
                    "error": str(exc), "base_url": self.base_url}


# ===========================================================================
# 工厂函数：根据环境变量创建后端
# ===========================================================================

def create_backend(backend_name: Optional[str] = None) -> ParseBackend:
    """
    根据环境变量 DOMUX_BACKEND 创建解析后端。

    >>> os.environ["DOMUX_BACKEND"] = "mock"
    >>> backend = create_backend()
    >>> backend.parse("把客厅空调调到26度")["slots"]["value"]
    26.0
    """
    name = (backend_name or os.getenv("DOMUX_BACKEND", "mock")).lower().strip()
    if name == "mock":
        return MockBackend()
    if name in ("vllm", "sglang", "openai"):
        return VLLMBackend()
    raise ValueError(f"未知后端类型: {name}（可选 mock|vllm）")


if __name__ == "__main__":
    # 独立冒烟测试：python backend.py
    b: ParseBackend = create_backend("mock")
    samples = [
        "把客厅空调调到26度",
        "打开主卧的灯",
        "把窗帘开到50%",
        "帮我锁门",
        "给玄关门锁解锁放快递进来",
        "关闭厨房燃气阀门",
        "客厅空调制热到24度",
        "扫地机器人开始清扫然后回充",
    ]
    for s in samples:
        r = b.parse(s)
        flag = "⚠️高危" if r["slots"]["action"] in {
            "unlock_door", "disarm_security", "gas_valve_off", "gas_valve_on"} else "   "
        print(f"{flag} [{s}] -> {json.dumps(r['slots'], ensure_ascii=False)} "
              f"(conf={r['confidence']}, {r['latency_ms']}ms)")
