#!/usr/bin/env python3
"""拉取飞书群指定日期消息摘要（供 cron 日报使用）
用法: python3 strategies/jobs/fetch-feishu-group-msgs.py --chat <chat_id> --date YYYY-MM-DD

凭据来源（2026-11-28 去 OpenClaw 依赖）：环境变量 → DataHub 统一配置
（config.local.json / secrets.env 的 secrets.feishu_app_id / feishu_app_secret）。
不再读 ~/.openclaw/openclaw.json；都没有就报错，不静默返回空串。
"""
import os, json, urllib.request, sys, argparse, datetime


def _bootstrap_hub_root():
    """引导定位 data_hub：env DATAHUB_ROOT → ~/.config/precious-task/libroots.json → 向上探测。

    子进程不继承父进程的 sys.path（见 case-child-syspath-secrets），必须自己引导。
    """
    import json as _j, os as _o
    p = (_o.environ.get("DATAHUB_ROOT") or "").strip()
    if p and _o.path.isdir(_o.path.join(p, "data_hub")):
        return p
    try:
        with open(_o.path.expanduser("~/.config/precious-task/libroots.json"), encoding="utf-8") as _f:
            _d = _j.load(_f) or {}
        for _k in ("datahub", "datahub_root", "DATAHUB_ROOT"):
            _v = _d.get(_k)
            if _v and _o.path.isdir(_o.path.join(str(_v), "data_hub")):
                return str(_v)
    except Exception:
        pass
    _d0 = _o.path.dirname(_o.path.abspath(__file__))
    for _ in range(6):
        if _o.path.isdir(_o.path.join(_d0, "data_hub")):
            return _d0
        _d0 = _o.path.dirname(_d0)
    return ""


_HUB_ROOT = _bootstrap_hub_root()
if _HUB_ROOT and _HUB_ROOT not in sys.path:
    sys.path.insert(0, _HUB_ROOT)


def _secret(name):
    """环境变量 → DataHub get_secret；缺了就抛可读错误。"""
    env = (os.environ.get(name) or "").strip()
    if env:
        return env
    try:
        from data_hub import get_secret as _g
        v = (_g(name) or "").strip()
        if v:
            return v
    except Exception:
        pass
    raise RuntimeError(
        '拿不到 %s：请设环境变量，或在 config.local.json 的 secrets 段配置 '
        'feishu_app_id / feishu_app_secret' % name)


def load_app_id():
    return _secret('FEISHU_APP_ID')


def load_secret():
    return _secret('FEISHU_APP_SECRET')


def get_token(app_id, app_secret):
    data = json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode()
    req = urllib.request.Request(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        data=data, headers={'Content-Type': 'application/json'})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if resp.get('code') != 0:
        raise RuntimeError('token error: %s' % resp)
    return resp['tenant_access_token']


def parse_content(msg):
    mtype = msg['msg_type']
    content = msg['body']['content']
    try:
        obj = json.loads(content)
    except Exception:
        return content[:500]
    if mtype == 'text':
        return obj.get('text', '')[:500]
    if mtype == 'interactive':
        parts = []
        # 兼容两种结构: {card:{...}} 或 {title, elements}
        card = obj.get('card', obj)
        header = card.get('header', {}).get('title', {})
        if header.get('content'):
            parts.append(header['content'])
        if card.get('title'):
            parts.append(card['title'])
        def walk(el):
            if isinstance(el, dict):
                if el.get('tag') in ('text','markdown','lark_md','plain_text') and el.get('text'):
                    parts.append(el['text'])
                if el.get('tag') == 'text' and el.get('content'):
                    parts.append(el['content'])
                for v in el.values():
                    walk(v)
            elif isinstance(el, list):
                for x in el:
                    walk(x)
        walk(card.get('elements', []))
        # 去重并合并
        seen, out = set(), []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return '\n'.join(out)[:20000]
    if mtype == 'post':
        title = obj.get('title', '')
        body = []
        for c in obj.get('content', []):
            for s in c:
                if s.get('tag') == 'text':
                    body.append(s.get('text', ''))
        return (title + '\n' + '\n'.join(body))[:2500]
    if mtype == 'file':
        return '[文件] %s' % obj.get('file_name', '')
    if mtype == 'image':
        return '[图片]'
    if mtype == 'share_chat':
        return '[分享群]'
    return json.dumps(obj, ensure_ascii=False)[:500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chat', required=True)
    ap.add_argument('--date', required=True, help='YYYY-MM-DD 北京时间')
    ap.add_argument('--after', default='00:00', help='只输出该时间（HH:MM 北京时间）之后的消息，默认 00:00')
    ap.add_argument('--page-size', type=int, default=50)
    ap.add_argument('--max-pages', type=int, default=20)
    args = ap.parse_args()

    token = get_token(load_app_id(), load_secret())
    tz8 = datetime.timezone(datetime.timedelta(hours=8))
    day = datetime.datetime.strptime(args.date, '%Y-%m-%d')
    start_ms = int(day.replace(tzinfo=tz8).timestamp() * 1000)
    end_ms = start_ms + 86400000
    after_h, after_m = [int(x) for x in args.after.split(':')]
    after_ms = int(day.replace(hour=after_h, minute=after_m, second=0, tzinfo=tz8).timestamp() * 1000)

    items = []
    page_token = ''
    stop = False
    for _ in range(args.max_pages):
        url = ('https://open.feishu.cn/open-apis/im/v1/messages?container_id_type=chat'
               '&container_id=%s&page_size=%d&sort_type=ByCreateTimeDesc' % (args.chat, args.page_size))
        if page_token:
            url += '&page_token=' + page_token
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if resp.get('code') != 0:
            raise RuntimeError('list error: %s' % resp)
        data = resp['data']
        for it in data['items']:
            t = int(it['create_time'])
            if after_ms <= t < end_ms:
                items.append(it)
            elif t < after_ms:
                stop = True
                break
        if stop:
            break
        if data.get('has_more') and data.get('page_token'):
            page_token = data['page_token']
            continue
        break

    items.sort(key=lambda x: int(x['create_time']))
    print('===== 飞书群消息 %s %s 之后 共 %d 条 =====' % (args.date, args.after, len(items)))
    for it in items:
        t = datetime.datetime.fromtimestamp(int(it['create_time']) / 1000, tz=tz8).strftime('%H:%M')
        sender = it['sender'].get('id', '')
        stype = it['sender'].get('sender_type', '')
        text = parse_content(it).strip()
        print('\n--- [%s] %s:%s (%s) ---' % (t, stype, sender, it['msg_type']))
        print(text)


if __name__ == '__main__':
    main()
