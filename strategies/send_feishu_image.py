#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把图随通知发出去 —— 统一走 data_hub.notify（邮件内联图 + 飞书文本提示）。

迁移前这里是「上传图片到飞书群」的自建实现（读 openclaw.json 取密钥、直连
im/v1/images）；现在技术脚本的通知统一由 DataHub 通知库负责，本文件只做薄壳。

用法:
    python3 strategies/send_feishu_image.py out/x.png [标题]
    python3 strategies/send_feishu_image.py out/x.png --raw   # 老行为（直发飞书图）
"""
import json
import os
import sys

try:                                   # 路径引导（迁入后与其他脚本一致）
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass


def _raw(img, chat):
    """老行为：上传到飞书并发送图片消息（仅 --raw 时使用）。"""
    import urllib.request
    cfg = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json"), encoding="utf-8"))
    f = cfg["channels"]["feishu"]
    data = json.dumps({"app_id": f["appId"], "app_secret": f["appSecret"]}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"})
    tok = json.loads(urllib.request.urlopen(req, timeout=30).read())["tenant_access_token"]
    boundary = "----x" + os.urandom(8).hex()
    raw = open(img, "rb").read()
    body = (("--%s\r\n" % boundary).encode()
            + b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
            + ("--%s\r\n" % boundary).encode()
            + b'Content-Disposition: form-data; name="image"; filename="m.png"\r\nContent-Type: image/png\r\n\r\n'
            + raw + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/images", data=body,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if r.get("code") != 0:
        print("upload fail", r)
        return 1
    key = r["data"]["image_key"]
    body = json.dumps({"receive_id": chat, "msg_type": "image",
                       "content": json.dumps({"image_key": key})}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, headers={"Authorization": "Bearer " + tok,
                            "Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("feishu send code", r.get("code"), r.get("msg"))
    return 0 if r.get("code") == 0 else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    img = args[0]
    title = args[1] if len(args) > 1 else os.path.basename(img)
    if "--raw" in sys.argv:
        return _raw(img, os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", ""))
    import _notify
    res = _notify.send_card(title, "配图：%s" % os.path.basename(img), images=[img])
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
