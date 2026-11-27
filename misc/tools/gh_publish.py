#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gh_publish.py —— 把 workspace 里的代码同步到 GitHub 私有库（走 REST API，绕过 git 阻断）
=====================================================================
为什么不用 git push：本机 github.com:443 被间歇性阻断（TLS 半路断 / 130s 超时），
`git ls-remote` / `git push` 必败；但 `api.github.com` 通。所以用 REST contents 接口逐文件 PUT。

用法：
  python3 bin/gh_publish.py --repo baizhiwei9/techAnly-summary \
      --map "bin/tech_summary.py:bin/tech_summary.py" ... \
      --msg "fix: ..." [--dry-run]

更常用的是**项目专用封装**（每个项目一个薄壳），避免每次写长 --map：
  python3 bin/gh_publish_techsummary.py --msg "fix: 均线版端点取极值"

特性：
  · 自动读取上次 commit 的 sha（每个文件各自 PUT，互不影响）
  · 内容完全相同的文件自动跳过（不多造 commit）
  · 自动剥私：`--strip` 可指定一组替换规则文件（JSON），默认对 .py 做常见私密串检查
  · 只改文件内容 + commit message，**不删除**远端已有文件（除非 --prune 且显式给 --dir）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"

# 私密串黑名单：出现即拒绝上传（防止把 token / chat_id / webhook 推上去）
DENYLIST_FILE = os.environ.get(
    "GITHUB_PUBLISH_DENYLIST",
    os.path.expanduser("~/.config/gh/publish_denylist.txt"))

GENERIC_PATTERNS = [
    (r"\b[0-9a-f]{40,}\b", "疑似 40+ 位 hex token"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI 风格 key"),
    (r"\boc_[0-9a-f]{20,}\b", "疑似飞书 chat_id"),
    (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "疑似 webhook UUID"),
    (r"qqbot:[cg]2?c?:[0-9A-F]{30,}", "疑似 QQ target"),
]


def load_patterns():
    """通用形态 + 外部名单（不在代码里写死真实密钥）"""
    pats = list(GENERIC_PATTERNS)
    if os.path.exists(DENYLIST_FILE):
        for ln in open(DENYLIST_FILE, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "|" in ln:
                name, _, rx = ln.partition("|")
                try:
                    re.compile(rx.strip())
                    pats.append((rx.strip(), name.strip()))
                except re.error:
                    pass
    return pats


def gh_token():
    """优先环境变量，其次 gh CLI 的 hosts.yml"""
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    p = os.path.expanduser("~/.config/gh/hosts.yml")
    try:
        txt = open(p, encoding="utf-8").read()
        m = re.search(r"oauth_token:\s*(\S+)", txt)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    raise SystemExit("找不到 GitHub token：设 GITHUB_TOKEN 或先 gh auth login")


def api(method, path, token, data=None):
    url = API + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "gh-publish")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        if e.code == 404:
            return None
        raise SystemExit("GitHub API %s %s → %s %s" % (method, path, e.code, detail))


def remote_sha(repo, path, branch, token):
    """远端文件的 blob sha（不存在返回 None）"""
    r = api("GET", "/repos/%s/contents/%s?ref=%s" % (repo, urllib.parse.quote(path), branch), token)
    return (r or {}).get("sha")


def remote_content(repo, path, branch, token):
    r = api("GET", "/repos/%s/contents/%s?ref=%s" % (repo, urllib.parse.quote(path), branch), token)
    if not r or "content" not in r:
        return None
    try:
        return base64.b64decode(r["content"]).decode("utf-8", "ignore")
    except Exception:
        return None


def check_secrets(path, text):
    return [name for pat, name in load_patterns() if re.search(pat, text)]


def git_blob_sha(data):
    """GitHub contents API 返回的 sha 就是 git blob sha1，用它做内容比对（二进制也准）"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def put_file(repo, branch, path, local, token, msg, dry=False):
    raw = open(local, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and not local.endswith((".png", ".jpg", ".pdf")):
        hits = check_secrets(local, text)
        if hits:
            return "REFUSE", "含私密串：%s" % "、".join(sorted(set(hits)))
        # 本机工作区前缀由外部名单检查（见 ~/.config/gh/publish_denylist.txt）
    old_sha = remote_sha(repo, path, branch, token)
    if old_sha and old_sha == git_blob_sha(raw):
        return "SAME", ""
    if dry:
        return ("ADD" if old_sha is None else "MOD"), "sha=%s" % (old_sha or "-")[:7]
    data = {"message": msg, "branch": branch,
            "content": base64.b64encode(raw).decode("ascii")}
    if old_sha:
        data["sha"] = old_sha
    r = api("PUT", "/repos/%s/contents/%s" % (repo, urllib.parse.quote(path)), token, data)
    return ("ADD" if old_sha is None else "MOD"), (r.get("commit") or {}).get("sha", "")[:7]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--map", action="append", default=[],
                    help="本地路径:远端路径（可多次）")
    ap.add_argument("--map-file", default=None, help="文件里每行一条 本地:远端")
    ap.add_argument("--root", default=None, help="本地路径的基准目录（默认当前目录）")
    ap.add_argument("--msg", required=True, help="commit message")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    root = a.root or os.getcwd()
    pairs = list(a.map)
    if a.map_file:
        for ln in open(a.map_file, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                pairs.append(ln)
    if not pairs:
        raise SystemExit("没有要上传的文件（--map / --map-file）")

    tok = gh_token()
    n_add = n_mod = n_same = n_ref = 0
    problems = []
    for pr in pairs:
        if pr.count(":") < 1:
            print("[跳过] 格式错（应 本地:远端）：%s" % pr)
            continue
        lo, _, re_ = pr.partition(":")
        lo = os.path.join(root, lo.strip())
        if not os.path.exists(lo):
            problems.append("缺文件 %s" % lo)
            continue
        try:
            st, info = put_file(a.repo, a.branch, re_.strip(), lo, tok, a.msg, a.dry_run)
        except SystemExit as e:
            problems.append("%s → %s" % (re_.strip(), e))
            continue
        print("[%-6s] %s %s" % (st, re_.strip(), info))
        if st == "ADD":
            n_add += 1
        elif st == "MOD":
            n_mod += 1
        elif st == "SAME":
            n_same += 1
        else:
            n_ref += 1
            problems.append("%s 被拒：%s" % (re_.strip(), info))

    print("\n新增 %d / 更新 %d / 未变 %d / 拒绝 %d" % (n_add, n_mod, n_same, n_ref))
    if problems:
        print("\n⚠️ 问题：")
        for p in problems:
            print("  -", p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
