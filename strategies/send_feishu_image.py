#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传图片到飞书群（从 openclaw.json 读密钥，避免命令行明文）"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import json, urllib.request, os, sys

def main():
    img = sys.argv[1]
    chat = sys.argv[2] if len(sys.argv) > 2 else 'oc_5971f823bf987081f722f2942bbc8d71'
    cfg = json.load(open('/root/.openclaw/openclaw.json'))
    app_id = cfg['channels']['feishu']['appId']
    app_secret = cfg['channels']['feishu']['appSecret']

    data = json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode()
    req = urllib.request.Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                                 data=data, headers={'Content-Type': 'application/json'})
    token = json.loads(urllib.request.urlopen(req, timeout=30).read())['tenant_access_token']

    boundary = '----x' + os.urandom(8).hex()
    with open(img, 'rb') as f:
        raw = f.read()
    body = (('--%s\r\n' % boundary).encode() +
            b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n' +
            ('--%s\r\n' % boundary).encode() +
            b'Content-Disposition: form-data; name="image"; filename="m.png"\r\nContent-Type: image/png\r\n\r\n' +
            raw + ('\r\n--%s--\r\n' % boundary).encode())
    req = urllib.request.Request('https://open.feishu.cn/open-apis/im/v1/images', data=body,
                                 headers={'Authorization': 'Bearer ' + token,
                                          'Content-Type': 'multipart/form-data; boundary=' + boundary})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if r.get('code') != 0:
        print('upload fail', r)
        sys.exit(1)
    image_key = r['data']['image_key']

    body = json.dumps({'receive_id': chat, 'msg_type': 'image',
                       'content': json.dumps({'image_key': image_key})}).encode()
    req = urllib.request.Request('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
                                 data=body, headers={'Authorization': 'Bearer ' + token,
                                                     'Content-Type': 'application/json'})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print('feishu send code', r.get('code'), r.get('msg'))

if __name__ == '__main__':
    main()
