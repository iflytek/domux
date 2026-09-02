# -*- coding: utf-8 -*-
"""
ha_adapter.py — Home Assistant 对接适配层

职责：把 Domux 的七字段槽位翻译成 Home Assistant 的服务调用
（domain.service + payload），并统一执行通道：

    HAAdapter  —— 真实对接：调用 HA REST API
                  （POST {base_url}/api/services/{domain}/{service}，
                   Bearer Long-Lived Access Token；支持 demo mode 实例）
    MockHA     —— 离线替身：内存中记录全部调用，
                  无 HA 环境时跑通端到端链路（比赛可复现性关键）

映射规则（MCP 槽位 -> HA 服务）
------------------------------------------------------------
    device=light        turn_on            -> light.turn_on      {entity_id, brightness?}
                        turn_off           -> light.turn_off
                        set_brightness     -> light.turn_on      {brightness_pct}
    device=air_conditioner set_temperature -> climate.set_temperature {temperature, hvac_mode?}
                        turn_on/turn_off   -> climate.turn_on / climate.turn_off
    device=curtain      open/close/set_position -> cover.open_cover /
                           cover.close_cover / cover.set_cover_position
    device=door_lock    lock_door          -> lock.lock
                        unlock_door(高危)  -> lock.unlock   （须过 auth 确认流）
    device=security_panel disarm_security(高危) -> alarm_control_panel.alarm_disarm
                        turn_on            -> alarm_control_panel.alarm_arm_home
    device=gas_valve    gas_valve_off/on(高危) -> valve.close_valve / valve.open_valve
    device=speaker/tv   play/pause/turn_on/turn_off/set_volume -> media_player.*
    device=switch/socket turn_on/turn_off  -> switch.turn_on / switch.turn_off
    device=fan          set_speed          -> fan.set_preset_mode / fan.turn_on
    device=robot_vacuum start_cleaning     -> vacuum.start
                        return_dock        -> vacuum.return_to_base

entity_id 生成：{domain}.{device}_{room}_{floor 简写}
    例：climate.air_conditioner_living_room_f1

环境变量：
    HA_BASE_URL  默认 http://homeassistant:8123（demo mode 可换本机实例）
    HA_TOKEN     HA Long-Lived Access Token

独立运行：python ha_adapter.py 执行内置自测。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# 复用槽位契约与高危清单唯一事实源
_SERVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "domux-mcp-server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)
from slots import slots_summary  # noqa: E402,F401


# ===========================================================================
# 槽位 -> (domain, service, payload) 映射表
# ===========================================================================

#: floor 缩写：floor_1 -> f1, floor_b1 -> fb1
def _floor_abbr(floor: Optional[str]) -> str:
    f = (floor or "floor_1").replace("floor_", "")
    return ("fb" + f[1:]) if f.startswith("b") else ("f" + f)


@dataclass
class HAServiceCall:
    """一次 HA 服务调用的完整描述。"""
    domain: str                 # light / climate / cover / lock ...
    service: str                # turn_on / set_temperature ...
    entity_id: str
    payload: Dict[str, Any]
    source_slots: Dict[str, Any]          # 来源槽位（审计用）
    high_risk: bool = False               # 是否高危动作

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SlotMappingError(ValueError):
    """无法把槽位映射为任何 HA 服务。"""


def map_slots_to_service(slots: Dict[str, Any]) -> HAServiceCall:
    """
    核心纯函数：七字段槽位 -> HAServiceCall。
    不做网络 IO、不依赖 HA 存在，方便单测覆盖所有分支。
    """
    action = slots.get("action")
    device = slots.get("device")
    room = slots.get("room") or "home"
    floor = slots.get("floor") or "floor_1"
    value = slots.get("value")
    attribute = slots.get("attribute")

    if not action or not device:
        raise SlotMappingError(f"槽位不完整，缺少 action/device: "
                               f"{json.dumps(slots, ensure_ascii=False)}")

    fa = _floor_abbr(floor)

    def eid(domain: str) -> str:
        return f"{domain}.{device}_{room}_{fa}"

    HIGH_RISK = {"unlock_door", "disarm_security", "gas_valve_off", "gas_valve_on"}

    # ---------- 门锁 ----------
    if device == "door_lock":
        if action == "lock_door":
            return HAServiceCall("lock", "lock", eid("lock"), {},
                                 slots, high_risk=False)
        if action == "unlock_door":
            return HAServiceCall("lock", "unlock", eid("lock"), {},
                                 slots, high_risk=True)
    # ---------- 安防主机 ----------
    if device == "security_panel":
        if action == "disarm_security":
            return HAServiceCall("alarm_control_panel", "alarm_disarm",
                                 eid("alarm_control_panel"),
                                 {"code": "PLACEHOLDER"}, slots, high_risk=True)
        if action == "turn_on":
            return HAServiceCall("alarm_control_panel", "alarm_arm_home",
                                 eid("alarm_control_panel"), {}, slots)
    # ---------- 燃气阀 ----------
    if device == "gas_valve":
        if action == "gas_valve_off":
            return HAServiceCall("valve", "close_valve", eid("valve"), {},
                                 slots, high_risk=True)
        if action == "gas_valve_on":
            return HAServiceCall("valve", "open_valve", eid("valve"), {},
                                 slots, high_risk=True)
        if action in ("turn_on", "open"):
            return HAServiceCall("valve", "open_valve", eid("valve"), {},
                                 slots, high_risk=True)
        if action in ("turn_off", "close"):
            return HAServiceCall("valve", "close_valve", eid("valve"), {},
                                 slots, high_risk=True)
    # ---------- 灯 ----------
    if device == "light":
        if action == "turn_on":
            return HAServiceCall("light", "turn_on", eid("light"), {}, slots)
        if action == "turn_off":
            return HAServiceCall("light", "turn_off", eid("light"), {}, slots)
        if action == "set_brightness" and value is not None:
            return HAServiceCall("light", "turn_on", eid("light"),
                                 {"brightness_pct": int(value)}, slots)
    # ---------- 空调 / 温控 ----------
    if device == "air_conditioner" or attribute == "hvac_mode":
        if action == "set_temperature":
            payload: Dict[str, Any] = {}
            if isinstance(value, str):        # heat / cool 模式语义
                payload["hvac_mode"] = value
            elif value is not None:
                payload["temperature"] = float(value)
            if slots.get("unit") == "celsius" and "temperature" in payload:
                payload["temperature_unit"] = "°C"
            return HAServiceCall("climate", "set_temperature",
                                 eid("climate"), payload, slots)
        if action == "turn_on":
            return HAServiceCall("climate", "turn_on", eid("climate"), {}, slots)
        if action == "turn_off":
            return HAServiceCall("climate", "turn_off", eid("climate"), {}, slots)
    # ---------- 窗帘 / 卷帘 ----------
    if device in ("curtain", "window"):
        domain = "cover"
        if action == "open":
            return HAServiceCall(domain, "open_cover", eid(domain), {}, slots)
        if action == "close":
            return HAServiceCall(domain, "close_cover", eid(domain), {}, slots)
        if action == "set_position" and value is not None:
            return HAServiceCall(domain, "set_cover_position", eid(domain),
                                 {"position": int(value)}, slots)
        if action in ("turn_on",):
            return HAServiceCall(domain, "open_cover", eid(domain), {}, slots)
        if action in ("turn_off",):
            return HAServiceCall(domain, "close_cover", eid(domain), {}, slots)
    # ---------- 媒体播放（音箱/电视）----------
    if device in ("speaker", "tv"):
        d = "media_player"
        svc_by_action = {
            "turn_on": "turn_on", "turn_off": "turn_off",
            "play_media": "media_play", "pause_media": "media_pause",
        }
        if action == "set_volume" and value is not None:
            return HAServiceCall(d, "volume_set", eid(d),
                                 {"volume_level": max(0.0, min(1.0,
                                  float(value) / 100.0))}, slots)
        if action in svc_by_action:
            call = HAServiceCall(d, svc_by_action[action], eid(d), {}, slots)
            if action == "play_media":
                call.payload["media_content_type"] = "music"
            return call
    # ---------- 开关类插座 ----------
    if device in ("socket", "humidifier", "air_purifier", "water_heater"):
        d = "switch" if device == "socket" else device
        if action == "turn_on":
            return HAServiceCall(d, "turn_on", eid(d), {}, slots)
        if action == "turn_off":
            return HAServiceCall(d, "turn_off", eid(d), {}, slots)
        if action == "set_speed" and value is not None:
            return HAServiceCall(d, "turn_on", eid(d),
                                 {"percentage": int(value)}, slots)
    # ---------- 风扇 ----------
    if device == "fan":
        speed_map = {"high": 100, "medium": 55, "low": 25}
        if action == "set_speed":
            pct = speed_map.get(str(value), 50)
            return HAServiceCall("fan", "set_percentage", eid("fan"),
                                 {"percentage": pct}, slots)
        if action in ("turn_on", "turn_off"):
            return HAServiceCall("fan", action, eid("fan"), {}, slots)
    # ---------- 扫地机器人 ----------
    if device == "robot_vacuum":
        svc_by_action = {"start_cleaning": "start",
                         "return_dock": "return_to_base",
                         "turn_on": "start", "turn_off": "stop"}
        if action in svc_by_action:
            return HAServiceCall("vacuum", svc_by_action[action],
                                 eid("vacuum"), {}, slots)
    # ---------- 查询状态：HA 侧走 get state，这里返回特殊标记 ----------
    if action == "query_status":
        domain_guess = {"light": "light", "air_conditioner": "climate",
                        "curtain": "cover", "door_lock": "lock",
                        "camera": "camera", "speaker": "media_player",
                        "tv": "media_player"}.get(device, "sensor")
        return HAServiceCall(domain_guess, "__get_state__",
                             f"{domain_guess}.{device}_{room}_{fa}", {}, slots)

    raise SlotMappingError(
        f"暂不支持的动作/设备组合: action={action}, device={device}")


# ===========================================================================
# 执行通道抽象
# ===========================================================================

class HomeExecutor(ABC):
    """执行通道接口：真实 HA 或 Mock 实现。"""

    name: str = "base"

    @abstractmethod
    def execute(self, call: HAServiceCall) -> Dict[str, Any]:
        """执行一次服务调用，返回 {ok, detail}。"""

    @abstractmethod
    def get_state(self, entity_id: str) -> Dict[str, Any]:
        """读取实体状态。"""


class MockHA(HomeExecutor):
    """
    离线 Mock：记录每次调用到内存日志，可选模拟故障注入。
    无 HA 环境 / CI / 比赛演示的默认通道。
    """

    name = "mock_ha"

    def __init__(self, fail_entities: Optional[List[str]] = None) -> None:
        self.call_log: List[Dict[str, Any]] = []
        self.states: Dict[str, Dict[str, Any]] = {}
        self.fail_entities = set(fail_entities or [])

    def execute(self, call: HAServiceCall) -> Dict[str, Any]:
        ok = call.entity_id not in self.fail_entities
        entry = {"ts_backend": self.name, "domain": call.domain,
                 "service": call.service, "entity_id": call.entity_id,
                 "payload": call.payload, "ok": ok}
        self.call_log.append(entry)
        # 维护一个简易状态机，让 get_state 有反馈
        if ok and call.service != "__get_state__":
            self.states[call.entity_id] = {
                "state": call.payload.get("position",
                                          call.payload.get("temperature",
                                          call.payload.get("brightness_pct",
                                          call.payload.get("volume_level",
                                          call.service))),
                                          ),
                "attributes": dict(call.payload),
                "last_service": call.service,
            }
        return {"ok": ok, "detail": ("mock 故障注入" if not ok else "mock 执行成功"),
                **entry}

    def get_state(self, entity_id: str) -> Dict[str, Any]:
        return self.states.get(entity_id,
                               {"state": "unknown", "attributes": {}})

    # 便捷断言方法（demo / 测试用）
    def assert_called(self, service_part: str) -> bool:
        return any(service_part in (c["entity_id"] + c["service"])
                   for c in self.call_log)


class HAAdapter(HomeExecutor):
    """
    真实 Home Assistant REST 通道。

    兼容官方 demo mode：
        docker run -d --name ha -p 8123:8123 ghcr.io/home-assistant/home-assistant:stable
        （首次启动选择创建演示家庭，即可获得全量 demo 实体）
    配置：
        export HA_BASE_URL=http://localhost:8123
        export HA_TOKEN=<长期访问令牌>
    """

    name = "home_assistant"

    def __init__(self, base_url: Optional[str] = None,
                 token: Optional[str] = None, timeout: int = 10) -> None:
        self.base_url = (base_url or os.getenv("HA_BASE_URL",
                         "http://homeassistant:8123")).rstrip("/")
        self.token = token or os.getenv("HA_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def execute(self, call: HAServiceCall) -> Dict[str, Any]:
        if call.service == "__get_state__":
            return self.get_state(call.entity_id)
        url = f"{self.base_url}/api/services/{call.domain}/{call.service}"
        body = {"entity_id": call.entity_id, **call.payload}
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers=self._headers(), method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode() or "[]")
            return {"ok": True, "status": 200, "entities_touched":
                    [r.get("entity_id") for r in result
                     if isinstance(r, dict)], "request_body": body}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "error": e.reason}
        except Exception as exc:  # noqa: BLE001 - 网络层错误统一降级
            return {"ok": False, "status": -1, "error": str(exc)}

    def get_state(self, entity_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/states/{entity_id}"
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            return {"state": "unavailable", "error": str(exc)}

    @staticmethod
    def ping(base_url: Optional[str] = None, token: Optional[str] = None) -> bool:
        """/api/ 探活，用于启动前自检。"""
        b = (base_url or os.getenv("HA_BASE_URL",
             "http://homeassistant:8123")).rstrip("/")
        t = token or os.getenv("HA_TOKEN", "")
        try:
            req = urllib.request.Request(
                f"{b}/api/", headers={"Authorization": f"Bearer {t}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False


# ===========================================================================
# 高层门面：一条解析结果直接打到家居
# ===========================================================================

class DomuxHomeBridge:
    """
    把「解析 + 权限 + 执行」串成一行的门面类（demo 使用）::

        bridge = DomuxHomeBridge(executor=MockHA(), auth=am)
        outcome = bridge.dispatch(api_key, parse_result)
        # outcome: {decision: AuthzDecision, calls: [...], executed: bool}
    """

    def __init__(self, executor: HomeExecutor, auth=None) -> None:
        self.executor = executor
        self.auth = auth       # AuthMiddleware 实例；None 时跳过鉴权（仅测试）

    def dispatch(self, api_key: str, parse_result: Dict[str, Any]) -> Dict[str, Any]:
        slots = parse_result.get("slots", {})
        decision = None
        if self.auth is not None:
            decision = self.auth.authorize_parsed(api_key, parse_result)
            # pending/denied 一律不下发
            if not decision.executable:
                return {"executed": False, "decision": decision, "calls": []}
            # owner 二次确认路径会回传确认后的槽位
            if decision.slots:
                slots = decision.slots

        try:
            call = map_slots_to_service(slots)
        except SlotMappingError as exc:
            return {"executed": False, "decision": decision,
                    "calls": [], "error": str(exc)}

        result = self.executor.execute(call)
        return {"executed": bool(result.get("ok")), "decision": decision,
                "calls": [{"call": call.to_dict(), "result": result}]}


# ===========================================================================
# 内置自测
# ===========================================================================

def _self_test() -> int:
    print("=" * 62)
    print("ha_adapter.py 自测")
    print("=" * 62)
    p = f = 0

    def check(name, cond, detail=""):
        nonlocal p, f
        p, f = p + (1 if cond else 0), f + (0 if cond else 1)
        print(f"  {'✅' if cond else '❌'} {name}" + ("" if cond else f"  {detail}"))

    def S(**kw):
        base = {"action": None, "device": None, "attribute": None,
                "value": None, "unit": None, "room": "living_room",
                "floor": "floor_1"}
        base.update(kw)
        return base

    # ---- 映射正确性 ----
    c = map_slots_to_service(S(action="set_temperature", device="air_conditioner",
                               value=26, unit="celsius"))
    check("空调温度->climate.set_temperature",
          c.domain == "climate" and c.service == "set_temperature"
          and c.payload["temperature"] == 26.0
          and c.entity_id == "climate.air_conditioner_living_room_f1")

    c = map_slots_to_service(S(action="set_temperature", device="air_conditioner",
                               attribute="hvac_mode", value="heat"))
    check("制热模式->hvac_mode=heat",
          c.domain == "climate" and c.payload.get("hvac_mode") == "heat")

    c = map_slots_to_service(S(action="turn_on", device="light"))
    check("开灯->light.turn_on", c.domain == "light" and c.service == "turn_on")

    c = map_slots_to_service(S(action="set_brightness", device="light", value=70))
    check("亮度70->brightness_pct", c.payload.get("brightness_pct") == 70)

    c = map_slots_to_service(S(action="set_position", device="curtain", value=50))
    check("窗帘50%->set_cover_position",
          c.domain == "cover" and c.service == "set_cover_position"
          and c.payload["position"] == 50)

    c = map_slots_to_service(S(action="unlock_door", device="door_lock",
                               room="entryway"))
    check("解锁->lock.unlock且标高危", c.service == "unlock" and c.high_risk)

    c = map_slots_to_service(S(action="disarm_security", device="security_panel"))
    check("撤防->alarm_disarm且标高危",
          c.domain == "alarm_control_panel" and c.service == "alarm_disarm"
          and c.high_risk)

    c = map_slots_to_service(S(action="gas_valve_off", device="gas_valve",
                               room="kitchen"))
    check("关燃气阀->valve.close_valve高危",
          c.service == "close_valve" and c.high_risk)

    c = map_slots_to_service(S(action="start_cleaning", device="robot_vacuum"))
    check("清扫->vacuum.start", c.domain == "vacuum" and c.service == "start")

    c = map_slots_to_service(S(action="query_status", device="camera"))
    check("查询状态->__get_state__", c.service == "__get_state__")

    try:
        map_slots_to_service(S(action="fly_to_moon", device="rocket"))
        check("非法组合抛SlotMappingError", False)
    except SlotMappingError:
        check("非法组合抛SlotMappingError", True)

    # ---- MockHA 执行与状态维护 ----
    ha = MockHA()
    c_light = map_slots_to_service(S(action="turn_on", device="light"))
    r = ha.execute(c_light)
    check("MockHA执行成功", r["ok"])
    st = ha.get_state(c_light.entity_id)   # 用调用返回的 entity_id 查询
    check("MockHA状态可查", st.get("last_service") == "turn_on")

    ha2 = MockHA(fail_entities=["light.light_bedroom_f2"])
    r = ha2.execute(map_slots_to_service(
        S(action="turn_on", device="light", room="bedroom", floor="floor_2")))
    check("故障注入生效", r["ok"] is False)

    # ---- Bridge 全链路（无鉴权模式）----
    bridge = DomuxHomeBridge(MockHA())
    parse_like = {"text": "x", "slots": S(action="set_temperature",
                                          device="air_conditioner", value=24)}
    out = bridge.dispatch("any-key", parse_like)
    check("Bridge直连执行", out["executed"]
          and out["calls"][0]["call"]["service"] == "set_temperature")

    # ---- 真实 HA 适配器：构造可用、离线优雅失败 ----
    real = HAAdapter(base_url="http://127.0.0.1:59999", token="x")
    res = real.execute(map_slots_to_service(S(action="turn_on", device="light")))
    check("真实HA不可达时优雅失败", res["ok"] is False and "error" in res)
    check("HA ping 离线返回False", HAAdapter.ping(
        base_url="http://127.0.0.1:59999", token="x") is False)

    print("-" * 62)
    print(f"ha_adapter 自测完成：✅ {p} 通过，❌ {f} 失败")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(_self_test())
