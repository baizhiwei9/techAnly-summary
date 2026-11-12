#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 飞书应用通道（用飞书官方应用 / 机器人 直接发到指定群）
================================================================
两种飞书通道：
  1) **群自定义机器人 webhook**（旧）→ 绑定某一个群，换群要换 webhook
  2) **飞书官方应用（机器人）**（本文件）→ 应用被拉进哪个群，就能发到哪个群；
     由 `chats()` 列出应用可见的群，用 chat_id 定向发送（推荐）

凭据来源：/root/.openclaw/openclaw.json → channels.feishu.appId / appSecret
用法：
  python3 smc_feishu.py chats                 # 列出应用可见的群
  python3 smc_feishu.py send <chat_id> "文本"
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

CFG_PATH = os.environ.get("OPENCLAW_JSON", "/root/.openclaw/openclaw.json")
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


def send_text(chat_id, text):
    """以应用身份向指定 chat_id 发文本。"""
    t = token()
    body = {"receive_id": chat_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)}
    return _http(f"{BASE}/im/v1/messages?receive_id_type=chat_id", body,
                 headers={"Authorization": "Bearer " + t})


def send_card(chat_id, card):
    """以应用身份向指定 chat_id 发交互式卡片。"""
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
    """一行调用：发一张交互式卡片（标题+markdown+署名）。"""
    return send_card(chat_id, build_card(title, body_md, template, sign))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "chats"
    if cmd == "chats":
        for it in chats():
            print(it.get("chat_id"), "|", it.get("name"), "|", it.get("chat_mode"),
                  "| members:", it.get("member_count"))
    elif cmd == "send":
        print(send_text(sys.argv[2], sys.argv[3]))
