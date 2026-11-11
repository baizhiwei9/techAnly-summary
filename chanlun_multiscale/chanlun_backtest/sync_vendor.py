#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把远程 techAnly 的单源模块同步进 chanlun_backtest/vendor/

用法：
    python -m chanlun_backtest.sync_vendor [--src <bin目录>] [--check]

默认查找顺序（依序取第一个同时包含两个文件的目录）：
    $TECHANLY_BIN
    <repo>/bin
    <repo>/../techAnly-summary/bin
    <repo>/../../techAnly-summary/bin
    <workspace>/techAnly_合并多尺度交易/techAnly-summary/bin

--check 只比对 md5，不写文件；不一致时退出码 1。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

FILES = ("chan_ma_trend_v2.py", "chan_smc_index.py")
HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")


def _candidates():
    repo = os.path.dirname(HERE)
    return [
        os.environ.get("TECHANLY_BIN"),
        os.path.join(repo, "bin"),
        os.path.join(repo, "..", "bin"),          # 仓库布局：<repo>/chanlun_multiscale/../bin
        os.path.join(repo, "techAnly_合并多尺度交易", "techAnly-summary", "bin"),
        os.path.join(repo, "..", "techAnly-summary", "bin"),
        os.path.join(repo, "..", "..", "techAnly-summary", "bin"),
    ]


def _find_src(explicit=None):
    for p in ([explicit] if explicit else []) + _candidates():
        if p and all(os.path.exists(os.path.join(p, f)) for f in FILES):
            return os.path.abspath(p)
    raise SystemExit("找不到远程单源目录（需同时包含 %s）。已查找: %r"
                     % (" / ".join(FILES), [p for p in _candidates() if p]))


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    src = _find_src(args.src)
    os.makedirs(VENDOR, exist_ok=True)
    drift = 0
    for f in FILES:
        s, d = os.path.join(src, f), os.path.join(VENDOR, f)
        same = os.path.exists(d) and _md5(s) == _md5(d)
        print("%-24s %s  %s" % (f, "OK " if same else "DIFF", _md5(s)))
        if same:
            continue
        if args.check:
            drift += 1
            continue
        shutil.copy2(s, d)
        print("  -> copied to vendor/%s" % f)
    if args.check and drift:
        sys.exit(1)


if __name__ == "__main__":
    main()
