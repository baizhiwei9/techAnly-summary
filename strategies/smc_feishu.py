#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 飞书应用通道（用飞书官方应用 / 机器人 直接发到指定群）
================================================================
两种飞书通道：
  1) **群自定义机器人 webhook**（旧）→ 绑定某一个群，换群要换 webhook
  2) **飞书官方应用（机器人）**（本文件）→ 应用被拉进哪个群，就能发到哪个群；
     由 `chats()` 列出应用可见的群，用 chat_id 定向发送（推荐）

凭据来源：~/.openclaw/openclaw.json → channels.feishu.appId / appSecret（可用 OPENCLAW_JSON 覆盖）
用法：
  python3 smc_feishu.py chats                 # 列出应用可见的群
  python3 smc_feishu.py send <chat_id> "文本"
"""
from __future__ import annotations

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import json
import os
import sys
import urllib.request

CFG_PATH = os.environ.get("OPENCLAW_JSON") or os.path.expanduser("~/.openclaw/openclaw.json")
BASE = "https://open.feishu.cn/open-apis"


def _creds():
    cfg = json.load(open(CFG_PATH))
    f = cfg["channels"]["feishu"]
    return f["appId"], f["appSecret"]


def _http(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


def token():
    aid, sec = _creds()
    r = _http(f"{BASE}/auth/v3/tenant_access_token/internal",
              {"app_id": aid, "app_secret": sec})
    if r.get("code") != 0:
        raise RuntimeError(f"token error: {r}")
    return r["tenant_access_token"]


def chats():
    """列出应用（机器人）所在的群。"""
    t = token()
    r = _http(f"{BASE}/im/v1/chats?page_size=100", headers={"Authorization": "Bearer " + t})
    return (r.get("data") or {}).get("items") or []


def _unified():
    """统一通知库（DataHub notify）；不可用时返回 None，由调用方回落。"""
    try:
        import _notify
        return _notify
    except Exception:
        return None


def _card_text(card):
    """把卡片 JSON 压成 (标题, 正文 markdown)，供统一库发文本。"""
    title = (((card or {}).get("header") or {}).get("title") or {}).get("content") or ""
    lines = []
    for el in (card or {}).get("elements") or []:
        if not isinstance(el, dict):
            continue
        if el.get("tag") == "markdown":
            lines.append(el.get("content") or "")
        else:
            txt = ((el.get("text") or {}).get("content")) or ""
            if txt:
                lines.append(txt)
    return title, "\n\n".join([x for x in lines if x and x.strip()])


def send_text(chat_id, text):
    """发文本：优先走统一通知库（data_hub.notify），未就绪才应用直发。"""
    uni = _unified()
    if uni is not None:
        head = (text or "").strip().splitlines()
        title = (head[0][:60] if head else "") or "技术分析"
        res = uni.send_feishu(title, text)
        if res.get("ok"):
            return {"code": 0, "msg": "success（data_hub.notify）"}
        print("[smc_feishu] 统一库未就绪，回落应用直发：%s" % res.get("reason"))
    t = token()
    body = {"receive_id": chat_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)}
    return _http(f"{BASE}/im/v1/messages?receive_id_type=chat_id", body,
                 headers={"Authorization": "Bearer " + t})


def send_card(chat_id, card):
    """发卡片：优先走统一通知库（文本），未就绪才应用直发。"""
    uni = _unified()
    if uni is not None:
        title, text = _card_text(card)
        res = uni.send_card(title or "技术分析", text or "", card=card)
        if res.get("ok"):
            return {"code": 0, "msg": "success（data_hub.notify）"}
        print("[smc_feishu] 统一库未就绪，回落应用直发：%s" % res.get("reason"))
    t = token()
    body = {"receive_id": chat_id, "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)}
    return _http(f"{BASE}/im/v1/messages?receive_id_type=chat_id", body,
                 headers={"Authorization": "Bearer " + t})


def build_card(title, body_md, template="blue", sign="📎 来自 openclaw.glm"):
    """构造飞书交互式卡片：header 标题 + markdown 正文 + 分割线 + 署名。"""
    elements = [{"tag": "markdown", "content": body_md}]
    if sign:
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": sign}]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": elements,
    }


def send_card_message(chat_id, title, body_md, template="blue", sign="📎 来自 openclaw.glm"):
    """一行调用：优先走统一通知库（文本），未就绪才发交互卡片。"""
    uni = _unified()
    if uni is not None:
        res = uni.send_card(title or "技术分析", "%s\n\n%s" % (body_md or "", sign or ""))
        if res.get("ok"):
            return {"code": 0, "msg": "success（data_hub.notify）"}
        print("[smc_feishu] 统一库未就绪，回落交互卡片：%s" % res.get("reason"))
    return send_card(chat_id, build_card(title, body_md, template, sign))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "chats"
    if cmd == "chats":
        for it in chats():
            print(it.get("chat_id"), "|", it.get("name"), "|", it.get("chat_mode"),
                  "| members:", it.get("member_count"))
    elif cmd == "send":
        print(send_text(sys.argv[2], sys.argv[3]))
