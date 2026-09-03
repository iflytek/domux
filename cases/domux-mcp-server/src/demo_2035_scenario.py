# -*- coding: utf-8 -*-
"""
demo_2035_scenario.py — 「2035 快递机器人上门」端到端演示

完整链路（对应 Hack-Astron #4 的「真实集成 + 安全边界」命题）：

    [0] 初始化智慧之家：注册 owner(张先生) / family(李女士) / service_agent(快递机器人)
    [1] 快递机器人到达玄关，用自然语言申请进门（走 Domux MCP 解析）
    [2] Domux 解析出 unlock_door -> 命中高危操作清单
    [3] 首次鉴权被拒（service_agent 无有效授权）
    [4] 管家(owner)核验快递凭证后签发任务授权：
        仅 entryway / 仅 door_lock / 仅 unlock_door / TTL=300s / 一次性
    [5] 机器人重试 -> 高危动作转 pending_confirmation
    [6] owner 在家庭 App 上二次确认(approve) -> 放行
    [7] HA 适配层把槽位翻译成 lock.unlock 并由 MockHA 执行留痕
    [8] 审计日志回放：每一步谁在何时做了什么全部落库
    [9] 授权过期后机器人再次申请开门 -> 被拒（TTL 失效）
    [10] 风险引擎吸收事件流 -> 四维风险分更新
    [11] owner 给保险公司签发只读 token -> 拉取标准化风险报告

运行（无需 GPU / 无需 Home Assistant / 无需网络）:
    python demo_2035_scenario.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_SERVER_DIR = os.path.join(_HERE, "domux-mcp-server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from backend import create_backend                      # noqa: E402
from auth_middleware import AuthMiddleware              # noqa: E402
from ha_adapter import (                                # noqa: E402
    DomuxHomeBridge, MockHA, map_slots_to_service,
)
from home_risk_engine import (                          # noqa: E402
    DeviceEvent, HomeContext, HomeRiskEngine, RiskReport,
)
from insurance_api import InsuranceAuthStore, build_risk_report  # noqa: E402


# ---------------------------------------------------------------------------
# 输出美化
# ---------------------------------------------------------------------------

def banner(step: str, title: str) -> None:
    print(f"\n{'=' * 66}\n  [{step}] {title}\n{'=' * 66}")


def info(indent: int, text: str) -> None:
    print("   " + "  " * indent + text)


def slots_line(slots: dict) -> str:
    return " | ".join(f"{k}={v}" for k, v in slots.items()
                      if v is not None)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    # 运行时 sqlite 数据落在项目 data/<时间戳>/ 下，便于赛后导出审计证据
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(_HERE, "data", f"demo_run_{stamp}")
    os.makedirs(run_dir, exist_ok=True)
    print("🏠 Domux MCP 智能家居 · 2035 快递机器人上门演示")
    print(f"   运行时数据目录: {run_dir}")

    # ------------------------------------------------------------------
    banner("0", "初始化智慧之家（身份与组件装配）")
    # ------------------------------------------------------------------
    am = AuthMiddleware(os.path.join(run_dir, "auth.db"))
    owner = am.register_agent("owner", "张先生（户主）")
    family = am.register_agent("family", "李女士（家庭成员）")
    courier = am.register_agent("service_agent", "SF-Courier-9000（快递机器人）",
                                ttl_seconds=3600)
    info(1, f"owner         : {owner.agent_id}  key={owner.api_key[:14]}…")
    info(1, f"family        : {family.agent_id}")
    info(1, f"service_agent : {courier.agent_id}  （身份有效期3600s）")

    backend = create_backend("mock")          # MockBackend：无GPU可跑通
    ha = MockHA()                             # MockHA：无需真机 Home Assistant
    bridge = DomuxHomeBridge(executor=ha, auth=am)

    # ------------------------------------------------------------------
    banner("1", "门铃响：快递机器人用自然语言申请进门")
    # ------------------------------------------------------------------
    request_text = "我是快递机器人，请给玄关门锁解锁让我放下包裹"
    info(0, f'机器人说：「{request_text}」')
    parsed = backend.parse(request_text)
    info(0, "Domux 解析结果:")
    info(1, slots_line(parsed["slots"]))
    info(1, f"confidence={parsed['confidence']} backend={parsed['backend']}")

    # ------------------------------------------------------------------
    banner("2", "高危识别 + 首次鉴权（应被拒）")
    # ------------------------------------------------------------------
    decision = am.authorize_parsed(courier.api_key, parsed)
    action = parsed["slots"]["action"]
    info(0, f"动作 {action} 命中高危清单 -> 需要特殊处理")
    info(0, f"首次鉴权结论: {decision.status.upper()} —— {decision.reason}")
    assert decision.status == "denied", "未授权的 service_agent 必须被拒"

    # ------------------------------------------------------------------
    banner("3", "管家核验凭证，签发最小化任务授权（仅玄关/仅门锁/5分钟）")
    # ------------------------------------------------------------------
    grant = am.grant_task(
        owner.api_key, courier.agent_id,
        rooms=["entryway"], devices=["door_lock"], actions=["unlock_door"],
        purpose="顺丰投递包裹 #SF20350826", ttl_seconds=300, single_use=True)
    info(0, f"授权单 {grant.grant_id}: rooms=['entryway'] "
            f"devices=['door_lock'] actions=['unlock_door']")
    info(0, f"TTL={300}s single_use=True purpose={grant.purpose!r}")
    info(0, "权限设计原则：最小授权范围 + 最短时效 + 一次性消耗")

    # ------------------------------------------------------------------
    banner("4", "机器人重试 -> 高危动作转 pending_confirmation")
    # ------------------------------------------------------------------
    decision = am.authorize_parsed(courier.api_key, parsed)
    info(0, f"鉴权结论: {decision.status.upper()} —— {decision.reason}")
    info(0, f"确认单号: {decision.request_id}")
    pendings = am.list_pending()
    info(0, f"owner 家庭 App 收到待确认事项 {len(pendings)} 条:")
    for req in pendings:
        info(1, f"{req['request_id']} 来自 {req['agent_id']} "
                f"指令「{req['command_text']}」")

    # ------------------------------------------------------------------
    banner("5", "owner 二次确认 -> 执行门锁解锁（HA 留痕）")
    # ------------------------------------------------------------------
    confirmed = am.confirm(owner.api_key, decision.request_id, approve=True)
    info(0, f"确认结论: {confirmed.status.upper()} by {confirmed.confirmed_by}")
    if not confirmed.executable:
        print("❌ 确认失败，演示中止"); return 1

    # 用确认后的槽位走 HA 适配层执行
    call = map_slots_to_service(confirmed.slots)
    exec_result = ha.execute(call)
    am.consume_grant(grant.grant_id)      # 一次性授权计数
    info(0, f"HA 服务调用: {call.domain}.{call.service} @ {call.entity_id}"
            f" high_risk={call.high_risk}")
    info(0, f"执行结果: ok={exec_result['ok']} ({exec_result['detail']})")
    other = am.authenticate(family.api_key)
    _ = other  # family 角色在本环节无需介入

    # ------------------------------------------------------------------
    banner("6", "审计留痕回放（sqlite audit_log 表）")
    # ------------------------------------------------------------------
    logs = am.query_audit_log(limit=12)
    info(0, f"共 {len(logs)} 条审计记录，最近事件倒序：")
    for r in reversed(logs[-9:]):
        info(1, f"[{r['ts'][11:19]}] {r['event_type']:<12} "
                f"agent={r['agent_id'] or '-':<24} "
                f"result={r['result']:<21} conf={r['confirmation_status']}")
    risky_trace = [r for r in logs
                   if r["action"] == "unlock_door"
                   and r["confirmation_status"] == "approved"]
    info(0, f"高危解锁全链路留痕条数: {len(risky_trace)} （authorize→confirm→execute 可追溯）")

    # ------------------------------------------------------------------
    banner("7", "授权过期后再次申请开门 -> 应被拒（TTL 失效）")
    # ------------------------------------------------------------------
    expired_grant_probe = am._active_grants_for(courier.agent_id,
                                                parsed["slots"])
    # 直接把已用完的一次性授权再查一遍：single_use 已消耗
    second_try = am.authorize_parsed(courier.api_key, parsed)
    info(0, f"一次性授权已消耗，剩余有效授权: {len(expired_grant_probe)} 条")
    info(0, f"再次申请开门结论: {second_try.status.upper()} —— {second_try.reason}")

    # 再演示 TTL 到期语义：新签一个 1 秒授权并手动推进时钟判定
    g2 = am.grant_task(owner.api_key, courier.agent_id,
                       rooms=["entryway"], devices=["door_lock"],
                       actions=["close"], ttl_seconds=1)
    close_slots = dict(action="close", device="door_lock", attribute=None,
                       value=None, unit=None, room="entryway",
                       floor="floor_1")
    d_now = am.authorize(courier.api_key, close_slots, "关门")
    import time as _t
    _t.sleep(1.2)   # 让 1 秒 TTL 自然流逝
    d_late = am.authorize(courier.api_key, close_slots, "1秒后再关门")
    info(0, f"TTL=1s 授权: 到期内 {d_now.status} -> 到期后 {d_late.status}")
    info(0, f"到期后拒绝原因: {d_late.reason}")

    # ------------------------------------------------------------------
    banner("8", "风险引擎吸收事件流 -> 四维风险分更新")
    # ------------------------------------------------------------------
    now = None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    events = [
        # 本次快递事件在传感器侧的投影
        DeviceEvent.create("lock", "door_open", "entryway", minutes_ago=3, now=now),
        DeviceEvent.create("lock", "door_closed", "entryway", minutes_ago=2, now=now),
        # 模拟同日其它传感器事件
        DeviceEvent.create("gas_sensor", "gas_leak_detected", "kitchen",
                           minutes_ago=40, value=8, now=now),
        DeviceEvent.create("water_leak_sensor", "water_leak_detected",
                           "bathroom", minutes_ago=90, now=now),
        DeviceEvent.create("power_meter", "power_overload", "living_room",
                           minutes_ago=95, value=7200, now=now),
    ]
    ctx = HomeContext(home_id="home_zhang_001",
                      last_human_activity=(now - timedelta(hours=1)).isoformat())
    engine = HomeRiskEngine()
    report: RiskReport = engine.assess(events, ctx)
    info(0, f"总分 {report.total_score}/100 等级 {report.level}")
    for dim, sc in report.subscores.items():
        bar = "█" * (sc // 10) + "░" * (10 - sc // 10)
        info(1, f"{dim:<16} {sc:>3} {bar}")
    info(0, "命中因子:")
    for fac in report.factors:
        info(1, f"+{fac['score_delta']:>2}  {fac['description']}")

    # ------------------------------------------------------------------
    banner("9", "owner 向保险公司签发只读授权 -> 拉取标准化风险报告")
    # ------------------------------------------------------------------
    store = InsuranceAuthStore(os.path.join(run_dir, "insurance.db"))
    tok_info = store.issue_token(ctx.home_id, owner.agent_id, ttl_seconds=1800)
    info(0, f"报告查询 token 已签发: {tok_info['token'][:18]}… "
            f"(scope={tok_info['scope']}, 有效至 {tok_info['expires_at']})")

    bundle = build_risk_report(ctx.home_id, events, ctx)
    payload = bundle.payload
    payload["authorization_note"] = (
        f"本报告经 homeowner({owner.agent_id}) 授权读取，"
        f"token={tok_info['token'][:10]}…，30天后自动失效")
    info(0, "标准化保险风险报告（节选）:")
    keys_show = ["report_version", "home_id", "total_score", "risk_level",
                 "subscores", "recommendations"]
    print(json.dumps({k: payload[k] for k in keys_show},
                     ensure_ascii=False, indent=4)[:1600])

    # 越权校验演示
    forged = store.verify(ctx.home_id, "ins_forged_token")
    info(0, f"伪造 token 校验: {'通过❌' if forged else '拒绝✅'}")

    # ------------------------------------------------------------------
    banner("FIN", "演示完成 · 关键产出汇总")
    # ------------------------------------------------------------------
    summary = {
        "mcp_backend": backend.name,
        "ha_executor": ha.name,
        "ha_calls_recorded": len(ha.call_log),
        "audit_log_entries": len(am.query_audit_log(limit=1000)),
        "high_risk_flow": "unlock_door -> pending_confirmation -> owner approved",
        "risk_total": report.total_score,
        "risk_level": report.level,
        "insurance_report_version": payload["report_version"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n💡 对应比赛评分维度：真实集成链路（25%可复现性）· 安全边界与二次确认"
          "\n   （20%安全意识）· 保险 API 商业闭环（20%场景创新）。详见 README.md")

    am.close(); store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
