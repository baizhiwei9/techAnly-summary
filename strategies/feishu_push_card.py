#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用飞书官方应用机器人把 interactive 卡片发到指定 chat（私聊/群均可）。

用途：定时任务改「发私聊」时复用，避免每次在 prompt 里手写 webhook/python。

用法：
  python3 strategies/feishu_push_card.py --title "📊 组合拆解纪要 · 2026-10-15 · 来自 OpenClaw.Deepseek" --body-file /tmp/x.md
  echo "正文" | python3 strategies/feishu_push_card.py --title "标题"
  python3 strategies/feishu_push_card.py --title "标题" --body "正文" --chat oc_xxx

默认目标 = 飞书私聊 oc_0e4e3335f97551eba50a32d6ccf8c12c（与机器人单聊）。
署名默认「📎 来自钉钉OpenClaw」放正文第一行（全平台统一，2026-09-28 起）。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import json
import sys
import urllib.request

CFG = '/root/.openclaw/openclaw.json'
DEFAULT_CHAT = 'oc_0e4e3335f97551eba50a32d6ccf8c12c'


def _token():
    c = json.load(open(CFG, encoding='utf-8'))['channels']['feishu']
    body = json.dumps({'app_id': c['appId'], 'app_secret': c['appSecret']}).encode()
    req = urllib.request.Request(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        data=body, headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())['tenant_access_token']


def build_card(title, text, template='blue', signature='📎 来自钉钉OpenClaw'):
    elements = []
    if signature:
        elements.append({'tag': 'markdown', 'content': signature})
    elements.append({'tag': 'markdown', 'content': text})
    return {
        'config': {'wide_screen_mode': True},
        'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': template},
        'elements': elements,
    }


def send(card, chat=DEFAULT_CHAT):
    body = json.dumps({'receive_id': chat, 'msg_type': 'interactive',
                       'content': json.dumps(card, ensure_ascii=False)}, ensure_ascii=False).encode()
    tok = _token()
    req = urllib.request.Request(
        'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
        data=body, headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default=None,
                    help='卡片标题；用 --card-file 传整张卡片时可省略')
    ap.add_argument('--body-file')
    ap.add_argument('--body')
    ap.add_argument('--chat', default=DEFAULT_CHAT)
    ap.add_argument('--template', default='blue')
    ap.add_argument('--signature', default='📎 来自钉钉OpenClaw',
                    help='正文第一行署名；传空字符串关闭')
    ap.add_argument('--card-file', help='直接发已构建好的完整卡片 JSON（跳过拼接）')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.card_file:
        card = json.load(open(a.card_file, encoding='utf-8'))
        if a.dry_run:
            print(json.dumps({'chat': a.chat, 'card': card}, ensure_ascii=False)[:500])
            return
        r = send(card, a.chat)
        print('code=%s msg=%s chat=%s' % (r.get('code'), r.get('msg'), a.chat[-8:]))
        sys.exit(0 if r.get('code') == 0 else 1)

    if not a.title:
        ap.error('需要 --title（或改用 --card-file）')

    if a.body_file:
        text = open(a.body_file, encoding='utf-8').read()
    elif a.body is not None:
        text = a.body
    else:
        text = sys.stdin.read()

    card = build_card(a.title, text, a.template, a.signature)
    if a.dry_run:
        print(json.dumps({'chat': a.chat, 'card': card}, ensure_ascii=False)[:500])
        return
    r = send(card, a.chat)
    print('code=%s msg=%s chat=%s' % (r.get('code'), r.get('msg'), a.chat[-8:]))
    sys.exit(0 if r.get('code') == 0 else 1)


if __name__ == '__main__':
    main()
