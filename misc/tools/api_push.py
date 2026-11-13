#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api_push.py · 用 GitHub REST API 创建 commit（git push 的 443 不通时的备用通道）

流程：BASE commit → 建 blob → 建 tree（base_tree=当前远端 tree）→ 建 commit → PATCH ref
只改指定文件，其余文件沿用远端当前 tree。
"""
import base64
import json
import os
import subprocess
import sys

REPO = "baizhiwei9/techAnly-summary"
BRANCH = "main"


def gh(method, path, payload=None):
    cmd = ["gh", "api", "-X", method, path]
    data = None
    if payload is not None:
        cmd += ["--input", "-"]
        data = json.dumps(payload).encode()
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode != 0:
        sys.exit("[gh api] %s %s 失败: %s" % (method, path, r.stderr.decode()[:500]))
    out = r.stdout.decode().strip()
    return json.loads(out) if out else {}


def main():
    files = sys.argv[1:-1]
    msg = sys.argv[-1]
    if not files:
        sys.exit("用法: api_push.py <file1> <file2> ... <commit-message>")

    ref = gh("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))
    base = ref["object"]["sha"]
    print("[api] base=%s" % base)

    base_commit = gh("GET", "/repos/%s/git/commits/%s" % (REPO, base))
    base_tree = base_commit["tree"]["sha"]

    entries = []
    for f in files:
        f = f.strip()
        with open(f, "rb") as fh:
            raw = fh.read()
        blob = gh("POST", "/repos/%s/git/blobs" % REPO, {
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({
            "path": f.replace(os.sep, "/"),
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        print("[api] blob %s → %s (%d B)" % (f, blob["sha"][:10], len(raw)))

    tree = gh("POST", "/repos/%s/git/trees" % REPO,
              {"base_tree": base_tree, "tree": entries})
    print("[api] tree=%s" % tree["sha"])

    commit = gh("POST", "/repos/%s/git/commits" % REPO,
                {"message": msg, "tree": tree["sha"], "parents": [base]})
    print("[api] commit=%s" % commit["sha"])

    gh("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, BRANCH),
       {"sha": commit["sha"], "force": False})
    print("[api] ✓ %s 已更新到 %s" % (BRANCH, commit["sha"]))
    return commit["sha"]


if __name__ == "__main__":
    main()
