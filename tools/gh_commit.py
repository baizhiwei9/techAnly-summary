#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gh_commit.py —— 用 Git Data API 把多个文件**一次性**提交成一个 commit
=====================================================================
为什么需要它：GitHub contents API 每次 PUT 只改一个文件 = 一次 commit。
一次改动 10 个文件 → 10 条同名 commit，历史脏得没法读（大佬要求「每次改了什么都记清楚」）。
Git Data API：blobs → tree → commit → 更新 ref，全流程 1 个 commit。

用法（一般由 gh_publish_techsummary.py 调用，也可单独用）：
  python3 bin/gh_commit.py --repo owner/name --map-file /tmp/x/_map.txt --msg "..." [--dry-run]
  map 文件每行：本地路径:仓库路径
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_publish import api, check_secrets, gh_token  # noqa: E402


def blob(repo, token, raw):
    r = api("POST", "/repos/%s/git/blobs" % repo, token,
            {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
    return r["sha"]


def build(repo, branch, pairs, token, msg, dry=False):
    ref = api("GET", "/repos/%s/git/ref/heads/%s" % (repo, branch), token)
    base_commit = ref["object"]["sha"]
    commit = api("GET", "/repos/%s/git/commits/%s" % (repo, base_commit), token)
    base_tree = commit["tree"]["sha"]
    # 远端现有 tree（用于内容比对，跳过未变的文件）
    tree = api("GET", "/repos/%s/git/trees/%s?recursive=1" % (repo, base_tree), token)
    have = {t["path"]: t["sha"] for t in tree.get("tree", []) if t["type"] == "blob"}

    entries, n_add, n_mod, n_same, problems = [], 0, 0, 0, []
    for local, path in pairs:
        if not os.path.exists(local):
            problems.append("缺文件 %s" % local)
            continue
        raw = open(local, "rb").read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None and not local.endswith((".png", ".jpg", ".pdf")):
            hits = check_secrets(local, text)
            if hits:
                problems.append("%s 含私密串：%s" % (path, "、".join(sorted(set(hits)))))
                continue
        # git blob sha = sha1("blob <len>\0" + content)
        import hashlib
        h = hashlib.sha1()
        h.update(b"blob %d\0" % len(raw))
        h.update(raw)
        sha = h.hexdigest()
        if have.get(path) == sha:
            n_same += 1
            print("[SAME  ] %s" % path)
            continue
        if dry:
            print("[%s] %s" % ("MOD   " if path in have else "ADD   ", path))
            if path in have:
                n_mod += 1
            else:
                n_add += 1
            continue
        sha = blob(repo, token, raw)
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        print("[%s] %s %s" % ("MOD   " if path in have else "ADD   ", path, sha[:7]))
        if path in have:
            n_mod += 1
        else:
            n_add += 1

    print("\n新增 %d / 更新 %d / 未变 %d" % (n_add, n_mod, n_same))
    if problems:
        print("\n⚠️ 问题：")
        for p in problems:
            print("  -", p)
    if dry:
        return 0 if not problems else 1
    if not entries:
        print("没有任何变化，不建 commit")
        return 0 if not problems else 1

    t = api("POST", "/repos/%s/git/trees" % repo, token,
            {"base_tree": base_tree, "tree": entries})
    c = api("POST", "/repos/%s/git/commits" % repo, token,
            {"message": msg, "tree": t["sha"], "parents": [base_commit]})
    api("PATCH", "/repos/%s/git/refs/heads/%s" % (repo, branch), token, {"sha": c["sha"]})
    print("\n✅ commit %s" % c["sha"][:7])
    return 0 if not problems else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--map-file", default=None)
    ap.add_argument("--msg", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pairs = []
    if a.map_file:
        for ln in open(a.map_file, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and ":" in ln:
                lo, _, re_ = ln.partition(":")
                pairs.append((lo.strip(), re_.strip()))
    for pr in a.map:
        if ":" in pr:
            lo, _, re_ = pr.partition(":")
            pairs.append((lo.strip(), re_.strip()))
    if not pairs:
        raise SystemExit("没有文件要提交")

    tok = gh_token()
    return build(a.repo, a.branch, pairs, tok, a.msg, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
