#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 机器人 · Markdown 图文卡片（一条消息里文字 + 图片）
=====================================================================
2026-10-24 用户要求：「你还是换成交互式卡片形式的消息吧。就是。这个交互式卡片，
我记着可以配图的」——QQ 侧没有飞书那种 interactive 卡片，但 **Markdown 消息（msg_type=2）
可以内嵌图片**，效果等价：一条消息里既有排版文字又有图。

⚠️ 本地图片必须先上传到 QQ 平台换 `qqbot-media://` 引用（三步，见官方「素材管理」）：
   ① POST /v2/media-images/prepare  {file_name,file_size,content_type,md5,sha1}
        → {upload_id, presigned_url, expires_in}
   ② PUT  <presigned_url>  （body = 图片二进制，Content-Type 必须与声明一致）
        → 响应头 ETag = MD5（去引号）
   ③ POST /v2/media-images/complete {upload_id, md5}
        → {image_id, url:"qqbot-media://image/v1/xxx", ttl, preview_url}
   然后 markdown 里写：`![说明 #宽px #高px](qqbot-media://image/v1/xxx)`
   （单机器人上限 100 张，滑动过期 90 天，超限要删旧的）

鉴权：POST https://bots.qq.com/app/getAppAccessToken  {appId, clientSecret}
     → access_token；请求头 Authorization: QQBot <token>

用法：
  python3 strategies/qq_md_card.py --text-file /tmp/body.md --img a.png --img b.png
  python3 strategies/qq_md_card.py --text "..." --img a.png --target qqbot:c2c:XXX
  python3 strategies/qq_md_card.py --list-images          # 查已上传素材
作为库：
  import qq_md_card as QQ
  QQ.send_md(title, text, [img1, img2], target=...)
"""
import os, sys, json, hashlib, base64, socket, time, argparse, urllib.request, urllib.parse

WS = os.path.expanduser("~/.openclaw/workspace")
sys.path.insert(0, os.path.join(WS, "bin"))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

CFG = os.path.join(os.path.dirname(WS), "openclaw.json")
DEFAULT_TARGET = os.environ.get("CHAN_QQ_TARGET") or os.environ.get("QQ_DEFAULT_TARGET", "")


# ───────────────────────── 鉴权 ─────────────────────────
def app_creds(account="default"):
    c = json.load(open(CFG, encoding="utf-8"))["channels"]["qqbot"]
    if account == "default":
        return c["appId"], c["clientSecret"]
    a = (c.get("accounts") or {}).get(account) or {}
    return a.get("appId") or c["appId"], a.get("clientSecret") or c["clientSecret"]


def token(account="default"):
    aid, sec = app_creds(account)
    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps({"appId": aid, "clientSecret": sec}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def _req(method, url, tok, body=None, ctype="application/json"):
    data = json.dumps(body).encode() if (body is not None and ctype == "application/json") else body
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": "QQBot " + tok, "Content-Type": ctype})
    return json.loads(urllib.request.urlopen(r, timeout=90).read())


# ───────────────────────── 素材上传（本地图 → qqbot-media://）─────────────────────────
def upload_media(tok, path):
    """三步上传，返回 qqbot-media:// 引用；失败返回 None"""
    try:
        raw = open(path, "rb").read()
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/png")
        if not name.isascii():
            name = "img" + ext          # 中文名可能被拒，转 ASCII
        md5 = hashlib.md5(raw).hexdigest()
        sha1 = hashlib.sha1(raw).hexdigest()

        meta = {"file_name": name, "file_size": len(raw),
                "content_type": ctype, "md5": md5, "sha1": sha1}
        try:
            r1 = _req("POST", "https://api.sgroup.qq.com/v2/media-images/prepare", tok, meta)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if "quota" in body or "40035007" in body:
                # 素材库满了 → 清老图后重试一次（自动自愈，别再让日报丢图）
                prune_images(tok, keep=15)
                r1 = _req("POST", "https://api.sgroup.qq.com/v2/media-images/prepare", tok, meta)
            else:
                print("[WARN] prepare HTTP %s: %s" % (e.code, body[:200]))
                return None
        presign = r1.get("presigned_url")
        upid = r1.get("upload_id")
        if not presign:
            print("[WARN] prepare 失败:", json.dumps(r1, ensure_ascii=False)[:200])
            return None

        # 直传 COS：用 PUT，Content-Type 必须一致
        pr = urllib.request.Request(presign, data=raw, method="PUT",
                                    headers={"Content-Type": ctype})
        with urllib.request.urlopen(pr, timeout=180) as resp:
            etag = (resp.headers.get("ETag") or md5).strip('"')

        r3 = _req("POST", "https://api.sgroup.qq.com/v2/media-images/complete", tok,
                  {"upload_id": upid, "md5": etag})
        url = r3.get("url")
        if url:
            print("[media]", name, "->", url[:48])
            return url
        print("[WARN] complete 失败:", json.dumps(r3, ensure_ascii=False)[:200])
    except urllib.error.HTTPError as e:
        print("[WARN] 上传 HTTP %s: %s" % (e.code, e.read().decode()[:200]))
    except Exception as e:
        print("[WARN] 上传异常:", str(e)[:150])
    return None


def list_images(tok):
    return _req("GET", "https://api.sgroup.qq.com/v2/media-images", tok)


def _all_images(tok):
    """翻页列出全部素材（平台 total 上限 100，默认一页只给 20）"""
    out, cur = [], None
    for _ in range(30):
        url = "https://api.sgroup.qq.com/v2/media-images"
        if cur:
            url += "?cursor=" + urllib.parse.quote(cur)
        r = _req("GET", url, tok)
        out += r.get("images") or []
        cur = r.get("next_cursor")
        if not cur:
            break
    return out


def prune_images(tok, keep=60, verbose=True):
    """QQ 素材库硬上限 100 张，满了之后 prepare 直接 400「markdown image quota exceeded」
    → 图全部发不出去（2026-11-14~11-24 踩坑：日报连续多天只剩文字没图）。
    按 created_at 从旧到新删，只留最新 keep 张。返回删除数。"""
    try:
        imgs = _all_images(tok)
    except Exception as e:
        if verbose:
            print("[WARN] 列素材失败:", str(e)[:120])
        return 0
    n = len(imgs)
    if n <= keep:
        return 0
    imgs.sort(key=lambda i: int(i.get("created_at") or 0))
    drop = imgs[:n - keep]
    ok = 0
    for i in drop:
        if delete_image(tok, i.get("image_id")):
            ok += 1
        time.sleep(0.15)
    if verbose:
        print("[prune] 素材 %d -> %d（删 %d）" % (n, n - ok, ok))
    return ok


def delete_image(tok, image_id):
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://api.sgroup.qq.com/v2/media-images/%s/delete" % image_id,
            method="DELETE", headers={"Authorization": "QQBot " + tok}), timeout=30)
        return True
    except Exception as e:
        print("[WARN] 删除失败:", str(e)[:120])
        return False


# ───────────────────────── 发送 markdown ─────────────────────────
def _parse_target(target):
    """qqbot:c2c:OPENID → ("c2c", OPENID)；qqbot:group:GID → ("group", GID)"""
    t = target or DEFAULT_TARGET
    if t.startswith("qqbot:"):
        parts = t.split(":")
        if len(parts) >= 3:
            kind = parts[1]
            oid = ":".join(parts[2:])
            if kind in ("c2c", "dm", "user"):
                return "c2c", oid
            return "group", oid
    return "c2c", t


def send_md(title, text, imgs=None, target=None, account="default", extra_note=""):
    """发一条通知。返回 True/False。

    默认走**统一通知库**（data_hub.notify：飞书文本 + 邮件内联图）；
    只有显式设 ``QQ_FALLBACK=1``（或统一库未就绪）时才走本文件的 QQ 通道。
    """
    imgs = list(imgs or [])
    paths = [p[0] if isinstance(p, (tuple, list)) else p for p in imgs]
    if os.environ.get("QQ_FALLBACK", "").strip().lower() not in ("1", "true", "yes"):
        try:
            import _notify
            res = _notify.send_card(title or "技术分析", text or "", images=paths or None)
            if res.get("ok"):
                print("[qq_md_card] 已走统一通知（data_hub.notify）：飞书文本 + 邮件内联图 %d 张" % len(paths))
                return True
            print("[qq_md_card] 统一库未就绪，回落 QQ 通道：%s" % res.get("reason"))
        except Exception as exc:
            print("[qq_md_card] 统一库异常，回落 QQ 通道：%s" % exc)
    tok = token(account)
    kind, oid = _parse_target(target)
    # 发图前先自检配额：快满了（>85）就先清到 60，避免中途 400 丢图
    if imgs:
        try:
            if int(list_images(tok).get("total") or 0) > 85:
                prune_images(tok, keep=60)
        except Exception as e:
            print("[WARN] 配额自检:", str(e)[:100])

    md = []
    if title:
        md.append("## %s" % title)
        md.append("")
    md.append(text.strip())
    if imgs:
        md.append("")
        md.append("***")
        md.append("**🖼 配图**")
        md.append("")
        for p in imgs:
            # 2026-11-27 大佬：「也不告诉我是哪个指数」→ 支持 (path, alt) 元组，
            # alt 用中文说明（如「五大宽基·日线中枢」），不再只显示文件名
            if isinstance(p, (tuple, list)):
                p, alt = p[0], (p[1] if len(p) > 1 else '')
            else:
                alt = ''
            url = upload_media(tok, p)
            if url:
                if not alt:
                    alt = os.path.splitext(os.path.basename(p))[0]
                md.append("![%s #900px #600px](%s)" % (alt, url))
                md.append("")
    if extra_note:
        md.append("")
        md.append(extra_note)

    content = "\n".join(md)
    payload = {"msg_type": 2, "msg_id": "", "msg_seq": 1,
               "markdown": {"content": content}}
    url = ("https://api.sgroup.qq.com/v2/users/%s/messages" % oid) if kind == "c2c" \
        else ("https://api.sgroup.qq.com/v2/groups/%s/messages" % oid)
    try:
        r = _req("POST", url, tok, payload)
        ok = bool(r.get("id"))
        print("[qq-md-card]", "sent" if ok else "fail", r.get("id", "")[:26])
        if not ok:
            print("[qq-md-card] 详情:", json.dumps(r, ensure_ascii=False)[:250])
        return ok
    except urllib.error.HTTPError as e:
        print("[qq-md-card] HTTP %s: %s" % (e.code, e.read().decode()[:250]))
    except Exception as e:
        print("[qq-md-card] 异常:", str(e)[:200])
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file", default=None)
    ap.add_argument("--title", default="")
    ap.add_argument("--img", action="append", default=[])
    ap.add_argument("--target", default=None)
    ap.add_argument("--account", default="default")
    ap.add_argument("--list-images", action="store_true")
    a = ap.parse_args()

    tok = token(a.account)
    if a.list_images:
        r = list_images(tok)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:3000])
        return 0

    text = open(a.text_file, encoding="utf-8").read() if a.text_file else a.text
    ok = send_md(a.title, text, a.img, a.target, a.account)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
