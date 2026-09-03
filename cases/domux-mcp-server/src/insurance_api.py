# -*- coding: utf-8 -*-
"""
insurance_api.py — 面向保险公司的家居风险报告端点

商业闭环的最后一环：把风险引擎输出转化为保险公司可消费的标准化报告，
并施加「户主授权」访问控制——保险公司不能偷看用户家里的数据。

端点（标准库 http.server 实现，零第三方依赖）
--------------------------------------------------
    GET /healthz                     存活探针
    POST /admin/tokens               户主用 api_key 换取「报告查询授权 token」
                                     body: {"home_id": "...", "ttl_seconds": 3600}
    GET  /risk-report/{home_id}      查询标准化风险报告
                                     鉴权: ?token=... 或 Authorization: Bearer ...
                                     无效/过期/越权 -> 401

报告 JSON 结构（对齐财产险核保常用字段）
    home_id / generated_at / report_version
    total_score(0-100) / risk_level(low|moderate|elevated|critical)
    subscores{fire,flood,intrusion,equipment_fault}
    last_30d_events{total, by_type{...}}
    recommendations[...]              分级处置建议
    factors[...]                      可解释的风险因子明细
    disclaimer                        数据边界声明（隐私与免责）

独立运行：
    python insurance_api.py --selftest   业务逻辑自测（默认）
    python insurance_api.py --serve 8080 启动 HTTP 服务
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# 同目录模块导入
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from auth_middleware import AuthMiddleware          # noqa: E402
from home_risk_engine import (                       # noqa: E402
    DeviceEvent, HomeContext, HomeRiskEngine, RiskReport,
    generate_mock_sensor_stream,
)

REPORT_VERSION = "1.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ===========================================================================
# 授权凭证库（谁在什么时间段有权读取哪个家的报告）
# ===========================================================================

class InsuranceAuthStore:
    """homeowner 授权记录的 sqlite 存储。"""

    def __init__(self, db_path: str = "data/insurance.db") -> None:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 同 auth_middleware：内存 journal 兼容网络盘等特殊文件系统
        self._conn.execute("PRAGMA journal_mode=MEMORY")
        self._conn.execute("PRAGMA synchronous=OFF")
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS report_tokens (
            token      TEXT PRIMARY KEY,
            home_id    TEXT NOT NULL,
            issued_to  TEXT NOT NULL,       -- 申请者身份（owner agent_id）
            scope      TEXT NOT NULL DEFAULT 'risk_report:read',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked    INTEGER DEFAULT 0
        )
        """)
        self._conn.commit()

    def issue_token(self, home_id: str, issued_by_agent_id: str,
                    ttl_seconds: int = 3600) -> Dict[str, Any]:
        """签发一个只读报告授权 token。"""
        token = f"ins_{secrets.token_hex(16)}"
        created = _now()
        expires = created + timedelta(seconds=ttl_seconds)
        self._conn.execute(
            "INSERT INTO report_tokens(token,home_id,issued_to,scope,"
            "created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (token, home_id, issued_by_agent_id, "risk_report:read",
             _iso(created), _iso(expires)))
        self._conn.commit()
        return {"token": token, "home_id": home_id,
                "expires_at": _iso(expires), "scope": "risk_report:read"}

    def verify(self, home_id: str, token: str) -> Optional[Dict[str, Any]]:
        """校验 token 对该 home 是否有效；无效返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM report_tokens WHERE token=? AND home_id=? AND revoked=0",
            (token, home_id)).fetchone()
        if row is None:
            return None
        exp = datetime.fromisoformat(row["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            return None
        return dict(row)

    def close(self) -> None:
        self._conn.close()


# ===========================================================================
# 报告生成（业务核心，纯函数便于单测）
# ===========================================================================

def build_recommendations(subscores: Dict[str, int],
                          total: int) -> List[str]:
    """根据分项分数生成分级处置建议。"""
    rec: List[str] = []
    if subscores["fire"] >= 50:
        rec.append("【紧急】检测到燃气/烟感高危信号：立即开窗通风、关闭燃气阀，"
                   "必要时撤离并联系燃气公司")
    elif subscores["fire"] >= 25:
        rec.append("近期存在电路过载诱因：建议排查大功率电器并错峰使用")
    if subscores["flood"] >= 50:
        rec.append("水浸传感器持续报警：建议关闭总水阀并安排上门检修")
    elif subscores["flood"] >= 25:
        rec.append("曾出现水浸报警：建议检查卫浴密封件与洗衣机进水管老化情况")
    if subscores["intrusion"] >= 50:
        rec.append("入侵风险显著：离家模式下建议启用布防+摄像头联动推送")
    elif subscores["intrusion"] >= 25:
        rec.append("存在门窗异常开启或门锁试错：建议更换门锁管理密码并核对家庭成员操作")
    if subscores["equipment_fault"] >= 25:
        rec.append("设备健康度下降：断路器跳闸/多设备离线，建议预约电工巡检线路")
    if total >= 75:
        rec.append("综合风险等级 critical：建议 24 小时内完成全部整改并向保险顾问报备")
    elif total <= 24 and not rec:
        rec.append("家庭风险状况良好：保持现有安防布防习惯即可")
    return rec


def summarize_events_30d(events: List[DeviceEvent]) -> Dict[str, Any]:
    """近30天事件摘要：总数 + 按类型计数 + 最近一次时间。"""
    cutoff = _now() - timedelta(days=30)
    by_type: Dict[str, int] = {}
    latest: Dict[str, str] = {}
    for e in events:
        try:
            ts = datetime.fromisoformat(e.ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        if e.event_type not in latest or ts.isoformat() > latest[e.event_type]:
            latest[e.event_type] = ts.isoformat()
    return {
        "window_days": 30,
        "total": sum(by_type.values()),
        "by_type": by_type,
        "latest_per_type": latest,
    }


@dataclass
class ReportBundle:
    """一次评估的全部产物（供 HTTP 层与离线测试共用）。"""
    report: RiskReport
    payload: Dict[str, Any]     # 最终对外 JSON


def build_risk_report(home_id: str, events: List[DeviceEvent],
                      context: HomeContext) -> ReportBundle:
    """
    完整报告流水线：风险评分 -> 事件摘要 -> 建议 -> 标准化 JSON。
    """
    engine = HomeRiskEngine()
    report = engine.assess(events, context)
    summary = summarize_events_30d(events)
    recs = build_recommendations(report.subscores, report.total_score)

    payload = {
        "report_version": REPORT_VERSION,
        "home_id": home_id,
        "generated_at": report.generated_at,
        "engine": engine.name,
        "total_score": report.total_score,
        "risk_level": report.level,
        "subscores": report.subscores,
        "factors": report.factors,
        "last_30d_events": summary,
        "recommendations": recs,
        "weights": {"fire": 0.30, "intrusion": 0.30,
                    "flood": 0.20, "equipment_fault": 0.20},
        "disclaimer": (
            "本报告由家庭传感器数据自动生成，仅供保险评核参考，"
            "不构成承保承诺；数据范围仅限该住宅接入设备的事件流，"
            "不含音视频内容。"),
    }
    return ReportBundle(report=report, payload=payload)


# ===========================================================================
# HTTP 服务层
# ===========================================================================

class InsuranceAPIHandler(BaseHTTPRequestHandler):
    """
    路由处理器。依赖类属性注入（ThreadingHTTPServer 的 handler 不走 __init__）：
        auth_store : InsuranceAuthStore
        user_auth  : AuthMiddleware（校验 owner api_key 用）
    """

    auth_store: InsuranceAuthStore
    user_auth: AuthMiddleware

    # ---------- 工具 ----------
    def _json(self, code: int, obj: Dict[str, Any]) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 安全要求：绝不把完整请求目标写入日志（query 里可能夹带 token）。
        # 无论 fmt 是 requestline 还是其它格式，都只保留 "METHOD /path"，
        # 且路径部分剥离 query string，杜绝 token 经日志泄漏。
        try:
            text = fmt % args
            parts = text.split()
            method = parts[0].strip('"') if parts else ""
            path = ""
            for p in parts[1:]:
                if p.startswith("/"):
                    path = p.split("?", 1)[0]
                    break
            if method or path:
                sys.stderr.write("[insurance-api] %s %s\n"
                                 % (method, path))
            else:
                sys.stderr.write("[insurance-api] (sanitized log)\n")
        except Exception:
            sys.stderr.write("[insurance-api] (sanitized log)\n")

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(200, {"status": "ok", "service": "domux-insurance-api"})
            return
        if parsed.path.startswith("/risk-report/"):
            home_id = parsed.path.rsplit("/", 1)[-1]
            # 安全要求：凭证仅允许通过 Authorization: Bearer 头传递，
            # 不接受查询参数 token（避免 token 进入访问日志/代理日志）。
            auth_header = self.headers.get("Authorization", "")
            token = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
            rec = self.auth_store.verify(home_id, token)
            if rec is None:
                self._json(401, {"error": "unauthorized",
                                 "detail": "缺少有效的 homeowner 授权 token"})
                return
            events, ctx = load_home_data(home_id)
            bundle = build_risk_report(home_id, events, ctx)
            payload = dict(bundle.payload)
            payload["authorization"] = {
                "granted_to": rec["issued_to"],
                "scope": rec["scope"], "expires_at": rec["expires_at"]}
            self._json(200, payload)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/admin/tokens":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        owner_api_key = self.headers.get("X-Owner-API-Key", "")
        agent = self.user_auth.authenticate(owner_api_key)
        if agent is None or agent.role != "owner":
            self._json(403, {"error": "forbidden",
                             "detail": "仅 owner 可签发报告查询授权"})
            return
        home_id = str(body.get("home_id", "")).strip()
        if not home_id:
            self._json(400, {"error": "missing_home_id"})
            return
        ttl = min(int(body.get("ttl_seconds", 3600)), 86400)
        info = self.auth_store.issue_token(home_id, agent.agent_id, ttl)
        self._json(201, info)


def load_home_data(home_id: str):
    """
    加载某住宅的传感器事件与上下文。
    生产环境应替换为消息队列/时序数据库拉取；
    demo 环境使用模拟数据流保证可复现。
    """
    scenario = os.getenv("DOMUX_DEMO_SCENARIO", "incident")
    events, ctx = generate_mock_sensor_stream(scenario)
    ctx.home_id = home_id
    return events, ctx


def serve(host: str = "127.0.0.1", port: int = 8080,
          db_dir: str = "data") -> None:
    """启动 HTTP 服务（Ctrl+C 退出）。"""
    auth_store = InsuranceAuthStore(os.path.join(db_dir, "insurance.db"))
    user_auth = AuthMiddleware(os.path.join(db_dir, "auth.db"))
    InsuranceAPIHandler.auth_store = auth_store
    InsuranceAPIHandler.user_auth = user_auth
    httpd = ThreadingHTTPServer((host, port), InsuranceAPIHandler)
    print(f"[insurance-api] listening on http://{host}:{port} "
          f"(GET /healthz | POST /admin/tokens | GET /risk-report/{{home_id}})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[insurance-api] bye")


# ===========================================================================
# 自测
# ===========================================================================

def _self_test() -> int:
    import tempfile
    import io
    import contextlib

    print("=" * 62)
    print("insurance_api.py 自测")
    print("=" * 62)
    p = f = 0

    def check(name, cond, detail=""):
        nonlocal p, f
        p, f = p + (1 if cond else 0), f + (0 if cond else 1)
        print(f"  {'✅' if cond else '❌'} {name}" + ("" if cond else f"  {detail}"))

    tmp = tempfile.mkdtemp(prefix="domux_ins_")

    # ---- 1. 授权凭证库 ----
    store = InsuranceAuthStore(os.path.join(tmp, "ins.db"))
    tok = store.issue_token("home_demo_001", "agent_owner_x", 3600)
    check("签发token成功", tok["token"].startswith("ins_"))
    check("有效token校验通过",
          store.verify("home_demo_001", tok["token"]) is not None)
    check("跨home校验失败", store.verify("other_home", tok["token"]) is None)
    check("伪造token失败", store.verify("home_demo_001", "ins_fake") is None)
    expired = store.issue_token("home_demo_001", "agent_owner_x", -10)
    check("过期token失效",
          store.verify("home_demo_001", expired["token"]) is None)

    # ---- 2. 报告生成 ----
    events, ctx = generate_mock_sensor_stream("incident")
    ctx.home_id = "home_demo_001"
    bundle = build_risk_report("home_demo_001", events, ctx)
    pl = bundle.payload
    check("报告含全部标准字段",
          all(k in pl for k in ("home_id", "total_score", "risk_level",
                                "subscores", "last_30d_events",
                                "recommendations", "disclaimer")))
    check("四维分项齐全",
          set(pl["subscores"]) == {"fire", "flood", "intrusion",
                                   "equipment_fault"})
    check("事故场景总分处于中高风险", 25 <= pl["total_score"] < 75,
          f"got {pl['total_score']}")
    check("30天事件摘要统计正确",
          pl["last_30d_events"]["total"] >= len(events)
          and "gas_leak_detected" in pl["last_30d_events"]["by_type"])
    check("高风险场景生成紧急建议",
          any(r.startswith("【紧急】") for r in pl["recommendations"]))
    check("报告JSON可序列化", isinstance(json.dumps(pl, ensure_ascii=False), str))

    # ---- 3. 低风险场景建议分支 ----
    ev2, ctx2 = generate_mock_sensor_stream("normal")
    pl2 = build_risk_report("home_b", ev2, ctx2).payload
    check("低风险场景输出保持性建议",
          any("良好" in r for r in pl2["recommendations"]))

    # ---- 4. HTTP 层集成测试（起真实端口）----
    from threading import Thread
    import urllib.request as ur
    import urllib.error as ue

    user_auth = AuthMiddleware(os.path.join(tmp, "auth.db"))
    owner = user_auth.register_agent("owner", "张先生")
    InsuranceAPIHandler.auth_store = store
    InsuranceAPIHandler.user_auth = user_auth
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), InsuranceAPIHandler)
    port = httpd.server_address[1]
    t = Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    with ur.urlopen(f"{base}/healthz", timeout=5) as resp:
        check("GET /healthz 200", resp.status == 200)
    try:
        ur.urlopen(f"{base}/risk-report/home_demo_001", timeout=5)
        check("无token访问401", False)
    except ue.HTTPError as e:
        check("无token访问401", e.code == 401)

    req = ur.Request(f"{base}/admin/tokens", method="POST",
                     data=json.dumps({"home_id": "home_demo_001"}).encode(),
                     headers={"Content-Type": "application/json",
                              "X-Owner-API-Key": owner.api_key})
    with ur.urlopen(req, timeout=5) as resp:
        tok_info = json.loads(resp.read())
        check("owner签发token接口201", resp.status == 201
              and tok_info["token"].startswith("ins_"))

    bad_req = ur.Request(f"{base}/admin/tokens", method="POST",
                         data=json.dumps({"home_id": "h"}).encode(),
                         headers={"X-Owner-API-Key": "dmx_fake"})
    try:
        ur.urlopen(bad_req, timeout=5)
        check("非owner签发被403", False)
    except ue.HTTPError as e:
        check("非owner签发被403", e.code == 403)

    req_report = ur.Request(
        f"{base}/risk-report/home_demo_001",
        headers={"Authorization": "Bearer " + tok_info["token"]})
    with ur.urlopen(req_report, timeout=5) as resp:
        report = json.loads(resp.read())
        check("带token(Bearer头)取回完整报告", resp.status == 200
              and report["home_id"] == "home_demo_001"
              and "subscores" in report)

    # ---- 4.5 安全回归：日志中绝不出现 token ----
    # 用带 token 的请求触发日志，抓 stderr，断言无 ins_ token 泄漏。
    log_buf = io.StringIO()
    with contextlib.redirect_stderr(log_buf):
        try:
            ur.urlopen(f"{base}/risk-report/home_demo_001?token="
                       + tok_info["token"], timeout=5)
        except ue.HTTPError:
            pass  # query token 已不被支持，401 正常
        req_report2 = ur.Request(
            f"{base}/risk-report/home_demo_001",
            headers={"Authorization": "Bearer " + tok_info["token"]})
        try:
            ur.urlopen(req_report2, timeout=5)
        except ue.HTTPError:
            pass
        # 再发一次伪造 token 请求，确保错误路径也不泄漏
        try:
            ur.urlopen(f"{base}/risk-report/home_demo_001?token=ins_fake_bad",
                       timeout=5)
        except ue.HTTPError:
            pass
    leaked = tok_info["token"] in log_buf.getvalue()
    check("日志中不出现授权token（query与Bearer均脱敏）", not leaked)
    httpd.shutdown()

    print("-" * 62)
    print(f"insurance 自测完成：✅ {p} 通过，❌ {f} 失败")
    return 1 if f else 0


if __name__ == "__main__":
    if "--serve" in sys.argv:
        port = int(sys.argv[sys.argv.index("--serve") + 1]) \
            if len(sys.argv) > sys.argv.index("--serve") + 1 else 8080
        serve(port=port)
    else:
        sys.exit(_self_test())
