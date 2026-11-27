# -*- coding: utf-8 -*-
"""_notify —— techAnly-summary 的通知统一出口（走 DataHub 通知框架）

本仓库的技术脚本不再各自拼 HTTP、各写一份 chat_id，飞书/邮件统一从这里出去：

    from _notify import send, send_feishu, send_email
    send("标题", "正文")                        # 飞书文本 + 邮件
    send("标题", "正文", images=["out/x.png"])   # 内联图只随邮件
    send("标题", "正文", channels=("email",))    # 只发邮件
    send_feishu("标题", "正文")                  # 只发飞书
    send_email("标题", "正文", images=[...])     # 只发邮件

底层就是 DataHub 的 ``data_hub.notify``（2026-11-27 起支持按应用初始化）：

    data_hub.notify.apps.init_if_ready("technaly-summary", feishu=…, email=…)

边界
----
- **不写死 chat_id / 邮箱 / 密钥**：绑定来自 ocsecrets（生产机）、环境变量，
  或运行期配置 ``~/.openclaw/openclaw.json``（文件本身不入库）。
- ``data_hub`` 不可导入或本应用没配齐时，``send`` 返回 ``{"ok": False, "reason": …}``，
  调用方据此回落脚本自带通道（各脚本保留原实现），不会因统一改造打断日更。
- 飞书通道是**纯文本**（统一库不发卡片/图）；要图走邮件内联图，或把图路径写进正文。

自检：``python3 strategies/_notify.py --selfcheck``
"""
from __future__ import annotations

import json
import os
import sys

APP = "technaly-summary"

__all__ = ["APP", "init", "available", "send", "send_card", "send_feishu",
           "send_email", "build_card", "state", "selfcheck"]

_STATE = None


# ── 运行环境 ────────────────────────────────────────────────────────
def _workspace() -> str:
    for key in ("OPENCLAW_WORKSPACE", "WS", "WORKSPACE"):
        v = (os.environ.get(key) or "").strip()
        if v and os.path.isdir(v):
            return v
    try:
        import ocsecrets
        ws = ocsecrets.workspace()
        if ws and os.path.isdir(ws):
            return ws
    except Exception:
        pass
    return os.path.expanduser("~/.openclaw/workspace")


def _ensure_datahub() -> bool:
    """把 DataHub 私有库挂到 sys.path（生产机在 optstack / workspace 下）。"""
    if "data_hub" in sys.modules:
        return True
    ws = _workspace()
    for p in (os.path.join(ws, "optstack"), os.path.join(ws, "datahub"),
              os.path.join(ws, "projects", "datahub-multi-tier"),
              os.path.join(ws, "datahub-multi-tier")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)          # append：不抢调用方已有优先序
    try:
        import data_hub.notify  # noqa: F401
        return True
    except Exception:
        return False


def _openclaw_cfg() -> dict:
    for p in ((os.environ.get("OPENCLAW_CONFIG") or "").strip(),
              os.path.expanduser("~/.openclaw/openclaw.json")):
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
    return {}


def _secrets():
    """飞书/邮件绑定：环境变量 → openclaw.json（运行期配置）→ ocsecrets。"""
    f = ((_openclaw_cfg().get("channels") or {}).get("feishu") or {})
    feishu = {
        "app_id": (os.environ.get("FEISHU_APP_ID") or f.get("appId") or "").strip(),
        "app_secret": (os.environ.get("FEISHU_APP_SECRET") or f.get("appSecret") or "").strip(),
        "chat_id": (os.environ.get("FEISHU_CHAT_DM")
                    or os.environ.get("FEISHU_CHAT_ID") or "").strip(),
        "webhook_url": (os.environ.get("FEISHU_WEBHOOK_URL") or "").strip(),
    }
    email = {
        "to": [x.strip() for x in
               (os.environ.get("TECHANLY_MAIL_TO") or os.environ.get("EMAIL_TO") or "").split(",")
               if x.strip()],
        "from": (os.environ.get("EMAIL_FROM") or "").strip(),
    }
    try:
        import ocsecrets
        if not feishu["chat_id"]:
            feishu["chat_id"] = (ocsecrets.get("FEISHU_CHAT_ID") or "").strip()
        if not feishu["webhook_url"]:
            try:
                feishu["webhook_url"] = (ocsecrets.feishu_hook() or "").strip()
            except Exception:
                pass
        if not email["to"]:
            v = ocsecrets.get("MAIL_DEFAULT_TO", ("EMAIL_TO_ADDR",))
            if isinstance(v, (list, tuple)):
                email["to"] = [x for x in v if x]
            elif v:
                email["to"] = [x.strip() for x in str(v).split(",") if x.strip()]
    except Exception:
        pass
    return feishu, email


# ── 初始化 ──────────────────────────────────────────────────────────
def _missing_text(missing) -> str:
    if not isinstance(missing, dict):
        return str(missing)
    parts = []
    for ch, fields in missing.items():
        if fields:
            parts.append("%s：%s" % (ch, "、".join(f.split("（")[0] for f in fields)))
    return "；".join(parts) or "无"


def init(force: bool = False) -> dict:
    """注册本应用的通知绑定（幂等）。返回 ``{activated, reason, missing}``。"""
    global _STATE
    if _STATE is not None and not force:
        return _STATE
    if not _ensure_datahub():
        _STATE = {"app": APP, "activated": False,
                  "reason": "data_hub 不可导入（生产机需 optstack / workspace 提供）"}
        return _STATE
    try:
        from data_hub.notify import apps
    except Exception as exc:
        _STATE = {"app": APP, "activated": False,
                  "reason": "data_hub.notify 不可用：%s" % exc}
        return _STATE
    feishu, email = _secrets()
    try:
        res = apps.init_if_ready(APP, feishu=feishu, email=email,
                                 channels=("feishu", "email"),
                                 subject_prefix=os.environ.get("TECHANLY_NOTIFY_PREFIX") or None)
    except Exception as exc:
        _STATE = {"app": APP, "activated": False, "reason": "apps.init_if_ready 失败：%s" % exc}
        return _STATE
    ok = bool(res.get("activated"))
    _STATE = {"app": APP, "activated": ok, "missing": res.get("missing")}
    if not ok:
        _STATE["reason"] = ("本应用绑定未配齐（缺 %s），继续走脚本自带通道"
                            % _missing_text(res.get("missing")))
    return _STATE


def state() -> dict:
    return init()


def available() -> bool:
    return bool(init().get("activated"))


# ── 发送 ────────────────────────────────────────────────────────────
def send(title: str, body: str, *, images=None, channels=None,
         card=None, dry_run: bool = False) -> dict:
    """发一条通知。返回 ``{"ok", "reason"?, "via"?, "channels"?…}``。"""
    st = init()
    if not st.get("activated"):
        return {"ok": False, "app": APP, "reason": st.get("reason") or "统一通知库未就绪"}
    try:
        from data_hub import notify
        res = notify.send(title, body, channels=channels, images=images,
                          card=card, dry_run=dry_run, app=APP) or {}
        res["via"] = "data_hub.notify"
        res.setdefault("app", APP)
        return res
    except Exception as exc:
        return {"ok": False, "app": APP, "reason": "send 失败：%s" % exc}


def build_card(title: str, body: str, template: str = "blue",
               signature: str = "") -> dict:
    """按飞书交互式卡片格式拼一张卡片（header 标题 + markdown 正文）。"""
    elements = []
    if signature:
        elements.append({"tag": "markdown", "content": signature})
    elements.append({"tag": "markdown", "content": body or ""})
    return {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title or "技术分析"},
                       "template": template},
            "elements": elements}


def send_card(title: str, body: str, *, images=None, card=None,
              dry_run: bool = False) -> dict:
    """发**飞书交互式卡片** + 邮件文本（图随邮件内联）。

    ``card`` 不传时按 ``title + body`` 现拼一张；传了就原样发（保留调用方排版）。
    """
    if card is None:
        card = build_card(title, body)
    return send(title, body, images=images, card=card, dry_run=dry_run)


def send_feishu(title: str, body: str, dry_run: bool = False) -> dict:
    """只发飞书；默认也走卡片（飞书侧就是卡片形态）。"""
    return send_card(title, body, dry_run=dry_run)


def send_email(subject: str, body: str, images=None, dry_run: bool = False) -> dict:
    return send(subject, body, channels=("email",), images=images, dry_run=dry_run)


# ── 自检 / CLI ─────────────────────────────────────────────────────
def selfcheck() -> int:
    st = init(force=True)
    print("app=%s｜activated=%s" % (st.get("app"), st.get("activated")))
    if st.get("reason"):
        print("  原因：%s" % st["reason"])
    if st.get("missing"):
        print("  缺：%s" % _missing_text(st["missing"]))
    if st.get("activated"):
        from data_hub import notify
        print(notify.describe(app=APP))
        rep = notify.doctor(app=APP)
        for ch, s in rep["channels"].items():
            print("  %s %s" % ("✓" if s["ready"] else "✗", ch))
    return 0 if st.get("activated") else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="strategies/_notify.py",
                                description="techAnly-summary 通知统一出口（DataHub notify）")
    ap.add_argument("--selfcheck", action="store_true", help="打印绑定状态")
    ap.add_argument("--send", nargs=2, metavar=("标题", "正文"), help="手工发一条")
    ap.add_argument("--channel", default="", help="feishu,email；默认两个都发")
    ap.add_argument("--image", action="append", default=[], help="内联图（仅邮件）")
    ap.add_argument("--dry", action="store_true", help="只渲染不发")
    a = ap.parse_args(argv)
    if a.selfcheck:
        return selfcheck()
    if a.send:
        chans = [c.strip() for c in a.channel.split(",") if c.strip()] or None
        print(json.dumps(send(a.send[0], a.send[1], images=a.image or None,
                              channels=chans, dry_run=a.dry),
                         ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
