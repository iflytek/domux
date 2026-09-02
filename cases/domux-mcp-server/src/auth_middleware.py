# -*- coding: utf-8 -*-
"""
auth_middleware.py — Agent 身份认证与权限中间件（sqlite 持久化）

解决的比赛命题：「安全边界：高风险动作、歧义检测、二次确认和安全拒绝」+
「真实集成：把 Domux 接入一个可复现的 Agent 自动化流程」中的身份层。

四级行为者权限模型
------------------
    owner         户主：全权；高危动作触发后可自行二次确认
    family        家庭成员：日常设备控制全放行；高危动作转为 pending_confirmation，
                  必须由 owner 确认后才执行
    guest         访客：仅限白名单设备 + 有效时间窗内 + 禁止一切高危动作
    service_agent 外部服务代理（快递机器人/保洁 Agent 等）：仅在有效任务授权
                  （task_grant：房间/设备/动作白名单 + TTL）范围内执行；
                  即使命令解析出高危动作，也强制降级为 pending_confirmation

高危操作清单（与 domux-mcp-server/slots.py 保持同源）
----------------------------------------------------
    unlock_door / disarm_security / gas_valve_off / gas_valve_on
    -> 任何角色触发都先返回 pending_confirmation，owner 确认后才放行

审计日志
--------
    所有认证/鉴权/确认/拒绝事件写入 audit_log 表：
    时间 / agent_id / 指令原文 / 解析槽位 / 结果 / 确认状态

独立运行：python auth_middleware.py 执行内置自测。
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# 复用 MCP server 包内的槽位契约（含高危清单唯一事实源）
_SERVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "domux-mcp-server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)
from slots import HIGH_RISK_ACTIONS, slots_summary  # noqa: E402


# ===========================================================================
# 常量与数据结构
# ===========================================================================

ROLES = ("owner", "family", "guest", "service_agent")

#: 各角色是否允许触发的动作类别
ROLE_POLICY = {
    "owner":         {"daily": True,  "high_risk": "confirmable"},   # 可自确认
    "family":        {"daily": True,  "high_risk": "needs_owner"},     # 转 pending
    "guest":         {"daily": True,  "high_risk": "forbidden"},       # 直接拒绝
    "service_agent": {"daily": True,  "high_risk": "needs_owner"},     # 强制 pending
}

#: pending 确认单的有效期（超时自动失效，防止“深夜挂单清晨被批”）
PENDING_TTL_SECONDS = 300


def _now() -> datetime:
    """统一使用 UTC 时区感知时间，避免本地时钟歧义。"""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass
class AgentProfile:
    """已注册 Agent 的身份档案（api_key 仅注册返回值中可见）。"""
    agent_id: str
    name: str
    role: str
    api_key: str = ""                  # 认证凭证
    expires_at: Optional[str] = None   # 身份本身的有效期（guest 临时通行证等）

    def is_expired(self) -> bool:
        exp = _parse_iso(self.expires_at) if self.expires_at else None
        return exp is not None and exp < _now()


@dataclass
class TaskGrant:
    """一次任务授权：service_agent / guest 执行操作的合法性来源。"""
    grant_id: str
    agent_id: str
    rooms: List[str] = field(default_factory=list)      # 允许的房间
    devices: List[str] = field(default_factory=list)    # 允许的设备类型
    actions: List[str] = field(default_factory=list)    # 允许的动作
    purpose: str = ""
    granted_by: str = ""                                # 签发人（owner）
    created_at: str = ""
    expires_at: str = ""                                # TTL
    single_use: bool = False                            # 是否一次性
    used_count: int = 0
    revoked: bool = False

    def is_expired(self) -> bool:
        exp = _parse_iso(self.expires_at)
        return exp is not None and exp < _now()

    def covers(self, slots: Dict[str, Any]) -> bool:
        """判断一组解析槽位是否落在授权范围内。"""
        if self.revoked or self.is_expired():
            return False
        if self.single_use and self.used_count >= 1:
            return False
        if slots.get("action") not in self.actions:
            return False
        if self.devices and slots.get("device") not in self.devices:
            return False
        if self.rooms and slots.get("room") not in self.rooms:
            return False
        return True


@dataclass
class AuthzDecision:
    """鉴权结论：三态 allowed / denied / pending_confirmation。"""
    status: str                     # "allowed" | "denied" | "pending_confirmation"
    reason: str
    request_id: Optional[str] = None   # pending 时的确认单号
    grant_id: Optional[str] = None     # 命中的任务授权
    confirmed_by: Optional[str] = None # 已批准时的确认人
    slots: Optional[Dict[str, Any]] = None  # owner 确认后回传的原始槽位

    @property
    def executable(self) -> bool:
        return self.status == "allowed"


# ===========================================================================
# 主类
# ===========================================================================

class AuthMiddleware:
    """
    用法示例::

        am = AuthMiddleware("data/auth.db")
        owner = am.register_agent("owner", "户主张先生")
        courier = am.register_agent("service_agent", "顺丰快递机器人")

        # owner 给 courier 签发 5 分钟玄关开门授权
        grant = am.grant_task(owner.agent_id, courier.agent_id,
                              rooms=["entryway"], devices=["door_lock"],
                              actions=["unlock_door"], ttl_seconds=300)

        decision = am.authorize(courier.api_key,
                                {"action": "unlock_door", "device": "door_lock",
                                 "room": "entryway", ...},
                                command_text="打开门锁放包裹")
        # -> AuthzDecision(status="pending_confirmation", request_id=...)
    """

    def __init__(self, db_path: str = "data/auth.db") -> None:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 兼容网络盘/容器卷等不支持回滚 journal 的文件系统：
        # 演示与测试场景将 journal 放入内存，避免间歇性 disk I/O error。
        # 生产部署在本地磁盘时可去掉这两行 PRAGMA 以获得崩溃恢复能力。
        self._conn.execute("PRAGMA journal_mode=MEMORY")
        self._conn.execute("PRAGMA synchronous=OFF")
        self._init_schema()

    # ------------------------------------------------------------------ #
    # schema
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            role       TEXT NOT NULL CHECK(role IN ('owner','family','guest','service_agent')),
            api_key    TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked    INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS task_grants (
            grant_id   TEXT PRIMARY KEY,
            agent_id   TEXT NOT NULL,
            rooms      TEXT NOT NULL,       -- JSON 数组
            devices    TEXT NOT NULL,
            actions    TEXT NOT NULL,
            purpose    TEXT DEFAULT '',
            granted_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            single_use INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0,
            revoked    INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS confirmations (
            request_id TEXT PRIMARY KEY,
            agent_id   TEXT NOT NULL,
            command_text TEXT,
            slots_json TEXT,
            status     TEXT NOT NULL DEFAULT 'pending',
                             -- pending / approved / rejected / expired
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            event_type TEXT NOT NULL,   -- register/authenticate/authorize/confirm/grant/...
            agent_id   TEXT,
            command_text TEXT,
            action     TEXT,
            device     TEXT,
            room       TEXT,
            result     TEXT NOT NULL,   -- ok/allowed/denied/pending_confirmation/...
            confirmation_status TEXT DEFAULT 'none',
            detail     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # 审计日志
    # ------------------------------------------------------------------ #
    def _audit(self, event_type: str, *, agent_id: str = "", command_text: str = "",
               slots: Optional[Dict[str, Any]] = None, result: str = "",
               confirmation_status: str = "none", detail: str = "") -> None:
        s = slots or {}
        self._conn.execute(
            "INSERT INTO audit_log(ts,event_type,agent_id,command_text,action,"
            "device,room,result,confirmation_status,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_iso(_now()), event_type, agent_id, command_text,
             s.get("action"), s.get("device"), s.get("room"),
             result, confirmation_status, detail))
        self._conn.commit()

    def query_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 身份管理
    # ------------------------------------------------------------------ #
    def register_agent(self, role: str, name: str,
                       ttl_seconds: Optional[int] = None) -> AgentProfile:
        """
        注册一个 Agent 身份，返回含 api_key 的档案（api_key 仅此一次可见）。
        :param ttl_seconds: 身份有效期；None 表示长期（owner/family 通常如此）
        """
        if role not in ROLES:
            raise ValueError(f"非法角色: {role}（可选 {ROLES}）")
        agent_id = f"agent_{role}_{secrets.token_hex(4)}"
        api_key = f"dmx_{role[:2]}_{secrets.token_hex(16)}"
        expires_at = _iso(_now() + timedelta(seconds=ttl_seconds)) if ttl_seconds else None
        self._conn.execute(
            "INSERT INTO agents(agent_id,name,role,api_key,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (agent_id, name, role, api_key, _iso(_now()), expires_at))
        self._conn.commit()
        self._audit("register", agent_id=agent_id, result="ok",
                    detail=f"role={role}, name={name}")
        return AgentProfile(agent_id, name, role, api_key=api_key,
                            expires_at=expires_at)

    #: register_agent 的别名：强调“生成凭证”语义
    create_credentials = register_agent

    def authenticate(self, api_key: str) -> Optional[AgentProfile]:
        """用 api_key 换取身份档案；无效/吊销/过期返回 None 并留审计。"""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE api_key=? AND revoked=0", (api_key,)
        ).fetchone()
        if row is None:
            self._audit("authenticate", result="denied", detail="未知或已吊销的 api_key")
            return None
        profile = AgentProfile(row["agent_id"], row["name"], row["role"],
                               api_key=row["api_key"],
                               expires_at=row["expires_at"])
        if profile.is_expired():
            self._audit("authenticate", agent_id=profile.agent_id,
                        result="denied", detail="身份已过期")
            return None
        return profile

    def get_owner(self) -> Optional[Dict[str, Any]]:
        """取第一个 owner（demo 用；生产环境应支持多 owner 会签）。"""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE role='owner' AND revoked=0 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # 任务授权（service_agent / guest 的合法性来源）
    # ------------------------------------------------------------------ #
    def grant_task(self, owner_api_key: str, target_agent_id: str, *,
                   rooms: List[str], devices: List[str], actions: List[str],
                   purpose: str = "", ttl_seconds: int = 300,
                   single_use: bool = False) -> TaskGrant:
        """owner 签发任务授权。只有 owner 能签发。"""
        granter = self.authenticate(owner_api_key)
        if granter is None or granter.role != "owner":
            raise PermissionError("任务授权只能由 owner 签发")
        grant_id = f"grant_{secrets.token_hex(5)}"
        g = TaskGrant(
            grant_id=grant_id, agent_id=target_agent_id,
            rooms=rooms, devices=devices, actions=actions, purpose=purpose,
            granted_by=granter.agent_id, created_at=_iso(_now()),
            expires_at=_iso(_now() + timedelta(seconds=ttl_seconds)),
            single_use=single_use,
        )
        self._conn.execute(
            "INSERT INTO task_grants(grant_id,agent_id,rooms,devices,actions,purpose,"
            "granted_by,created_at,expires_at,single_use) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (g.grant_id, g.agent_id, json.dumps(g.rooms), json.dumps(g.devices),
             json.dumps(g.actions), g.purpose, g.granted_by, g.created_at,
             g.expires_at, int(g.single_use)))
        self._conn.commit()
        self._audit("grant", agent_id=target_agent_id, result="ok",
                    detail=f"grant={grant_id}, by={granter.agent_id}, "
                           f"rooms={rooms}, actions={actions}, ttl={ttl_seconds}s")
        return g

    def consume_grant(self, grant_id: str) -> None:
        """执行成功后调用：一次性授权计数 +1。"""
        self._conn.execute(
            "UPDATE task_grants SET used_count=used_count+1 WHERE grant_id=?",
            (grant_id,))
        self._conn.commit()

    def _active_grants_for(self, agent_id: str, slots: Dict[str, Any]) -> List[TaskGrant]:
        rows = self._conn.execute(
            "SELECT * FROM task_grants WHERE agent_id=? AND revoked=0",
            (agent_id,)).fetchall()
        grants = []
        for r in rows:
            g = TaskGrant(
                grant_id=r["grant_id"], agent_id=r["agent_id"],
                rooms=json.loads(r["rooms"]), devices=json.loads(r["devices"]),
                actions=json.loads(r["actions"]), purpose=r["purpose"],
                granted_by=r["granted_by"], created_at=r["created_at"],
                expires_at=r["expires_at"], single_use=bool(r["single_use"]),
                used_count=r["used_count"], revoked=bool(r["revoked"]),
            )
            if g.covers(slots):
                grants.append(g)
        return grants

    # ------------------------------------------------------------------ #
    # 核心：鉴权（含高危二次确认流转）
    # ------------------------------------------------------------------ #
    def authorize(self, api_key: str, slots: Dict[str, Any],
                  command_text: str = "") -> AuthzDecision:
        """
        对一条已解析指令做权限判定。

        返回三种状态之一：
            allowed               —— 直接下发 HA 执行
            pending_confirmation  —— 高危动作，等待 owner 二次确认
            denied                —— 拒绝（原因见 reason），不下发
        """
        agent = self.authenticate(api_key)
        if agent is None:
            self._audit("authorize", command_text=command_text, slots=slots,
                        result="denied", detail="认证失败")
            return AuthzDecision("denied", "认证失败：无效凭证")

        action = slots.get("action")
        is_high_risk = action in HIGH_RISK_ACTIONS
        policy = ROLE_POLICY[agent.role]

        # guest / service_agent 一律需要有效任务授权覆盖该指令
        grants = self._active_grants_for(agent.agent_id, slots) \
            if agent.role in ("guest", "service_agent") else []

        # ---------- 1) guest/service_agent 无授权覆盖：无论是否高危都拒绝 ----------
        if agent.role in ("guest", "service_agent") and not grants:
            self._audit("authorize", agent_id=agent.agent_id,
                        command_text=command_text, slots=slots, result="denied",
                        detail=f"{agent.role} 无匹配的有效授权")
            return AuthzDecision(
                "denied",
                f"{agent.role} 角色无覆盖该操作的有效授权"
                f"（action={action}, room={slots.get('room')}）")

        # ---------- 2) 高危动作：统一进入二次确认流程 ----------
        if is_high_risk:
            if policy["high_risk"] == "forbidden":
                self._audit("authorize", agent_id=agent.agent_id,
                            command_text=command_text, slots=slots,
                            result="denied", detail=f"{agent.role} 禁止高危动作")
                return AuthzDecision("denied",
                                     f"{agent.role} 角色禁止触发高危动作 {action}")
            req_id = self._create_pending(agent.agent_id, command_text, slots)
            grant_note = f"，授权单 {grants[0].grant_id}" if grants else ""
            self._audit("authorize", agent_id=agent.agent_id,
                        command_text=command_text, slots=slots,
                        result="pending_confirmation",
                        confirmation_status="pending",
                        detail=f"高危动作 {action} 待 owner 确认 ({req_id}{grant_note})")
            return AuthzDecision(
                "pending_confirmation",
                f"高危动作 {action} 需 owner 二次确认{grant_note}",
                request_id=req_id,
                grant_id=grants[0].grant_id if grants else None)

        # ---------- 3) 非高危：按角色分流 ----------
        if agent.role == "owner":
            self._audit("authorize", agent_id=agent.agent_id,
                        command_text=command_text, slots=slots, result="allowed")
            return AuthzDecision("allowed", "owner 全权")

        if agent.role == "family":
            self._audit("authorize", agent_id=agent.agent_id,
                        command_text=command_text, slots=slots, result="allowed")
            return AuthzDecision("allowed", "家庭成员日常控制")

        # guest / service_agent：已通过第 1 步的授权检查
        self._audit("authorize", agent_id=agent.agent_id,
                    command_text=command_text, slots=slots, result="allowed",
                    detail=f"命中授权 {grants[0].grant_id}")
        return AuthzDecision(
            "allowed",
            f"命中任务授权 {grants[0].grant_id}"
            + (f"（{grants[0].purpose}）" if grants[0].purpose else ""),
            grant_id=grants[0].grant_id)

    # ------------------------------------------------------------------ #
    # 二次确认流
    # ------------------------------------------------------------------ #
    def _create_pending(self, agent_id: str, command_text: str,
                        slots: Dict[str, Any]) -> str:
        req_id = f"req_{secrets.token_hex(5)}"
        self._conn.execute(
            "INSERT INTO confirmations(request_id,agent_id,command_text,slots_json,"
            "status,created_at,expires_at) VALUES(?,?,?,?,'pending',?,?)",
            (req_id, agent_id, command_text,
             json.dumps(slots, ensure_ascii=False), _iso(_now()),
             _iso(_now() + timedelta(seconds=PENDING_TTL_SECONDS))))
        self._conn.commit()
        return req_id

    def confirm(self, owner_api_key: str, request_id: str, *,
                approve: bool = True) -> AuthzDecision:
        """
        owner 对 pending 确认单做裁决。
        approve=True  -> 确认单变 approved，返回 allowed（附原始槽位信息于 reason）
        approve=False -> 确认单变 rejected，返回 denied
        只有 owner 能裁决；确认单过期/已裁决返回 denied。
        """
        owner = self.authenticate(owner_api_key)
        if owner is None or owner.role != "owner":
            raise PermissionError("只有 owner 可以裁决确认单")
        row = self._conn.execute(
            "SELECT * FROM confirmations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return AuthzDecision("denied", "确认单不存在")
        if row["status"] == "expired" or (
                row["status"] == "pending"
                and _parse_iso(row["expires_at"]) < _now()):
            self._conn.execute(
                "UPDATE confirmations SET status='expired' WHERE request_id=?",
                (request_id,))
            self._conn.commit()
            self._audit("confirm", agent_id=row["agent_id"], result="denied",
                        confirmation_status="expired",
                        detail=f"{request_id} 已过期")
            return AuthzDecision("denied", "确认单已过期")
        if row["status"] != "pending":
            return AuthzDecision("denied", f"确认单已被处理（{row['status']}）")

        new_status = "approved" if approve else "rejected"
        self._conn.execute(
            "UPDATE confirmations SET status=?, decided_by=?, decided_at=? "
            "WHERE request_id=?",
            (new_status, owner.agent_id, _iso(_now()), request_id))
        self._conn.commit()
        self._audit("confirm", agent_id=row["agent_id"],
                    command_text=row["command_text"],
                    slots=json.loads(row["slots_json"] or "{}"),
                    result="allowed" if approve else "denied",
                    confirmation_status=new_status,
                    detail=f"{request_id} 由 {owner.agent_id} 裁决")
        if approve:
            return AuthzDecision(
                "allowed", f"owner 已确认 {request_id}", request_id=request_id,
                confirmed_by=owner.agent_id,
                slots=json.loads(row["slots_json"] or "{}"))
        return AuthzDecision("denied", f"owner 已驳回 {request_id}",
                             request_id=request_id)

    def list_pending(self) -> List[Dict[str, Any]]:
        """列出所有仍处于 pending 的确认单（owner 客户端轮询用）。"""
        rows = self._conn.execute(
            "SELECT * FROM confirmations WHERE status='pending'"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            expired = _parse_iso(d["expires_at"]) < _now()
            if expired:
                self._conn.execute(
                    "UPDATE confirmations SET status='expired' WHERE request_id=?",
                    (d["request_id"],))
                continue
            d["slots"] = json.loads(d.pop("slots_json") or "{}")
            out.append(d)
        self._conn.commit()
        return out

    # ------------------------------------------------------------------ #
    # 便捷封装：解析结果直接过中间件（demo 使用）
    # ------------------------------------------------------------------ #
    def authorize_parsed(self, api_key: str, parse_result: Dict[str, Any]
                         ) -> AuthzDecision:
        """把 backend.parse() 的完整结果直接送审。"""
        return self.authorize(api_key, parse_result.get("slots", {}),
                              command_text=parse_result.get("text", ""))

    def close(self) -> None:
        self._conn.close()


# ===========================================================================
# 内置自测
# ===========================================================================

def _self_test() -> int:
    print("=" * 62)
    print("auth_middleware.py 自测")
    print("=" * 62)
    import tempfile

    results = {"p": 0, "f": 0}

    def check(name, cond, detail=""):
        mark = "✅" if cond else "❌"
        results["p" if cond else "f"] += 1
        print(f"  {mark} {name}" + ("" if cond else f"  {detail}"))

    tmp_db = os.path.join(tempfile.mkdtemp(prefix="domux_auth_"), "auth.db")
    am = AuthMiddleware(tmp_db)

    # 1. 注册与认证
    owner = am.register_agent("owner", "张先生")
    spouse = am.register_agent("family", "李女士")
    courier = am.register_agent("service_agent", "快递机器人", ttl_seconds=600)
    guest = am.register_agent("guest", "钟点工阿姨", ttl_seconds=3600)
    check("四种角色注册成功",
          all(a.api_key.startswith("dmx_") for a in (owner, spouse, courier, guest)))
    check("正确 key 通过认证", am.authenticate(owner.api_key).role == "owner")
    check("错误 key 认证失败", am.authenticate("dmx_fake_000") is None)

    daily_slots = {"action": "turn_on", "device": "light", "attribute": None,
                   "value": None, "unit": None, "room": "living_room",
                   "floor": "floor_1"}
    risky_slots = {"action": "unlock_door", "device": "door_lock", "attribute": None,
                   "value": None, "unit": None, "room": "entryway",
                   "floor": "floor_1"}

    # 2. 日常控制权限
    d = am.authorize(owner.api_key, daily_slots, "开客厅灯")
    check("owner 日常控制放行", d.status == "allowed")
    d = am.authorize(spouse.api_key, daily_slots, "开客厅灯")
    check("family 日常控制放行", d.status == "allowed")

    # 3. 高危动作：全部转 pending，owner 可确认
    d = am.authorize(spouse.api_key, risky_slots, "打开门锁")
    check("family 触发高危转pending", d.status == "pending_confirmation"
          and d.request_id is not None)
    pendings = am.list_pending()
    check("pending 列表可见", len(pendings) == 1)
    d = am.confirm(owner.api_key, d.request_id, approve=True)
    check("owner 确认后放行", d.status == "allowed" and d.confirmed_by)
    d = am.authorize(guest.api_key, risky_slots, "打开门锁")
    check("guest 高危直接拒绝", d.status == "denied")

    # 4. service_agent 无授权时拒绝；授权后高危动作走 pending -> owner 确认
    d = am.authorize(courier.api_key, risky_slots, "打开门锁放包裹")
    check("service_agent 未授权被拒", d.status == "denied")
    g = am.grant_task(owner.api_key, courier.agent_id,
                      rooms=["entryway"], devices=["door_lock"],
                      actions=["unlock_door"], purpose="投递包裹",
                      ttl_seconds=300, single_use=True)
    ok_slots = dict(risky_slots)
    d = am.authorize(courier.api_key, ok_slots, "打开门锁放包裹")
    check("有授权的高危动作转pending并挂授权单",
          d.status == "pending_confirmation" and d.grant_id == g.grant_id)
    d2 = am.confirm(owner.api_key, d.request_id, approve=True)
    check("owner确认后放行并回传槽位",
          d2.status == "allowed" and d2.slots is not None
          and d2.slots["room"] == "entryway")
    am.consume_grant(g.grant_id)
    # 一次性授权用掉后再次拒绝 —— 注意 unlock_door 本身会转 pending，
    # 但这里验证的是非高危路径：换一个普通动作验证 single_use 语义
    normal = dict(action="close", device="door_lock", attribute=None, value=None,
                  unit=None, room="entryway", floor="floor_1")
    g2 = am.grant_task(owner.api_key, courier.agent_id,
                       rooms=["entryway"], devices=["door_lock"],
                       actions=["close"], ttl_seconds=60, single_use=True)
    d1 = am.authorize(courier.api_key, normal, "关门")
    am.consume_grant(g2.grant_id)
    d2 = am.authorize(courier.api_key, normal, "再关一次门")
    check("一次性授权用后即失效", d1.status == "allowed" and d2.status == "denied")

    # 5. 越权范围：授权只到 entryway，客厅操作拒绝
    living_room = dict(action="turn_on", device="light", attribute=None, value=None,
                       unit=None, room="living_room", floor="floor_1")
    d = am.authorize(courier.api_key, living_room, "开客厅灯")
    check("越出授权房间被拒", d.status == "denied")

    # 6. 过期身份
    short_lived = am.register_agent("guest", "临时访客", ttl_seconds=-1)
    check("过期身份认证失败", am.authenticate(short_lived.api_key) is None)

    # 7. 审计日志完整性
    logs = am.query_audit_log(limit=100)
    events = {r["event_type"] for r in logs}
    need = {"register", "authenticate", "authorize", "confirm", "grant"}
    check("审计事件全覆盖", need <= events, f"缺 {need - events}")
    has_risky_trace = any(
        r["action"] == "unlock_door" and r["confirmation_status"] == "approved"
        for r in logs)
    check("高危确认链路留痕", has_risky_trace)

    am.close()
    print("-" * 62)
    print(f"auth 自测完成：✅ {results['p']} 通过，❌ {results['f']} 失败")
    return 1 if results["f"] else 0


if __name__ == "__main__":
    sys.exit(_self_test())
