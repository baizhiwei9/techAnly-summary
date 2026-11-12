#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一图片推送：QQ 私聊 + 飞书私聊（给缠论图这类程序生成的 PNG 用）
=====================================================================
为什么单独写这个：
  ⚠️ `openclaw message send --media <workspace里的图>` 在 QQ 通道**发不出去但返回 rc=0**，
     返回 JSON 里 messageId 为空、parts 为空。原因是平台安全策略只允许从
     `~/.openclaw/media/`（含 media/qqbot）等受信根路径上传。
     所以这里**先把图片复制到 ~/.openclaw/media/qqbot/ 再发**。
  ⚠️ 飞书要用 im/v1/images 上传拿 image_key，再发 msg_type=image（不能用 webhook 卡片塞图）。

用法：
  python3 bin/push_image.py <图片路径> [--text "说明"] [--qq-only|--feishu-only]
  python3 bin/push_image.py a.png b.png --text "五指数"     # 多张依次发
"""
import os, sys, json, shutil, subprocess, urllib.request
from datetime import datetime

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "chanlun")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "misc/core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "misc/report", "misc", "wavelet", "smc",
            "misc/strategies", "misc/tools"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
MEDIA_DIR = os.path.expanduser("~/.openclaw/media/qqbot/charts")
QQ_TARGET = os.environ.get("CHAN_QQ_TARGET", "")   # 留空=不发，需显式设 environment
# ⚠️ 2026-11-10 踩坑：QQ 发图必须指定 --account（本机是 bot2），否则平台静默拦截
#    → rc=0 但 messageId 为空、parts 为空，看起来"发成功了"其实没发出去
QQ_ACCOUNT = os.environ.get("CHAN_QQ_ACCOUNT", "")   # 本机是 bot2，不设则按默认账号发
FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""  # 留空=不发


def _to_media_dir(img):
    """复制到受信媒体目录，返回新路径（已在里面就原样返回）"""
    img = os.path.abspath(img)
    if os.path.abspath(MEDIA_DIR) in os.path.dirname(img):
        return img
    os.makedirs(MEDIA_DIR, exist_ok=True)
    base = os.path.basename(img)
    # 中文名转 ASCII 更稳（QQ 上传对中文名偶发失败）
    stem, ext = os.path.splitext(base)
    safe = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "_" for ch in stem)
    safe = (safe or "img") + "_" + datetime.now().strftime("%H%M%S") + ext
    dst = os.path.join(MEDIA_DIR, safe)
    shutil.copy2(img, dst)
    return dst


def send_qq(img, text="", doc=False):
    """返回 True 表示真的发出去了（校验 messageId 非空）

    doc=True → --force-document：以「文件」形式发送，**平台不再压缩**，图最清晰
                （代价：聊天里显示成文件卡片，需要点开看；大佬 2026-11-11 要求"再高清点"）
    """
    p = _to_media_dir(img)
    try:
        cmd = ["openclaw", "message", "send", "--channel", "qqbot",
               "--target", QQ_TARGET, "--media", p, "-m", text or " ", "--json"]
        if doc:
            cmd.append("--force-document")
        if QQ_ACCOUNT:
            cmd += ["--account", QQ_ACCOUNT]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            print("[QQ] fail rc=%d %s" % (r.returncode, (r.stderr or "")[-160:]))
            return False
        try:
            d = json.loads(out)
            res = (d.get("payload", {}) or {}).get("result", {}) or {}
            mid = res.get("messageId") or ""
            parts = ((res.get("receipt") or {}).get("parts") or [])
            if mid or parts:
                print("[QQ] sent ->", p, "| id", mid[:24])
                return True
            print("[QQ] blocked（messageId 为空 = 平台拦截）->", p)
            return False
        except Exception:
            # 拿不到 JSON 时退回看输出里有没有 Sent
            ok = "Sent via" in out
            print("[QQ]", "sent" if ok else "unknown", out[-120:])
            return ok
    except Exception as e:
        print("[QQ] exc:", str(e)[:150])
        return False


def _feishu_token():
    c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))["channels"]["feishu"]
    b = json.dumps({"app_id": c["appId"], "app_secret": c["appSecret"]}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=b, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]


def send_feishu(img, text=""):
    try:
        tok = _feishu_token()
        boundary = "----x" + os.urandom(8).hex()
        raw = open(img, "rb").read()
        body = (("--%s\r\n" % boundary).encode() +
                b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n' +
                ("--%s\r\n" % boundary).encode() +
                b'Content-Disposition: form-data; name="image"; filename="m.png"\r\n'
                b"Content-Type: image/png\r\n\r\n" +
                raw + ("\r\n--%s--\r\n" % boundary).encode())
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/images", data=body,
            headers={"Authorization": "Bearer " + tok,
                     "Content-Type": "multipart/form-data; boundary=" + boundary})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        if r.get("code") != 0:
            print("[FS] upload fail", r)
            return False
        key = r["data"]["image_key"]
        body = json.dumps({"receive_id": FEISHU_CHAT, "msg_type": "image",
                           "content": json.dumps({"image_key": key})}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=body, headers={"Authorization": "Bearer " + tok,
                                "Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        ok = r.get("code") == 0
        print("[FS]", "sent" if ok else "fail", r.get("code"), r.get("msg"))
        return ok
    except Exception as e:
        print("[FS] exc:", str(e)[:150])
        return False


def push(imgs, text="", qq=True, fs=True, doc=False):
    """imgs: 路径 或 路径列表。任一通道成功即返回 True

    doc=True → QQ 用文件形式发（不压缩，最清晰）
    """
    if isinstance(imgs, str):
        imgs = [imgs]
    ok = False
    for i, img in enumerate(imgs):
        t = text if (i == 0 or not text) else (text + " (%d/%d)" % (i + 1, len(imgs)))
        if qq:
            ok = send_qq(img, t, doc=doc) or ok
        if fs:
            ok = send_feishu(img, t) or ok
    return ok


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    text = ""
    if "--text" in sys.argv:
        text = sys.argv[sys.argv.index("--text") + 1]
        args = [a for a in args if a != text]
    ok = push(args, text,
              qq=("--feishu-only" not in sys.argv),
              fs=("--qq-only" not in sys.argv),
              doc=("--document" in sys.argv))
    sys.exit(0 if ok else 1)
