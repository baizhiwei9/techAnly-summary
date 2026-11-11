#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gh_publish_techanly.py —— 把 技术分析汇总（techAnly-summary）项目同步到 GitHub 私有库
=====================================================================
一条命令：本地文件 → 剥离私有信息（路径/token/chat_id）→ 逐文件 REST 上传 → 追加 CHANGELOG。

用法：
  python3 tools/publish_techanly.py --msg "fix: 均线版笔画端点取区间极值"           # 真上传
  python3 tools/publish_techanly.py --msg "..." --dry-run                          # 只看差异

⚠️ 为什么不用 git push：本机 github.com:443 间歇阻断（TLS 半路断），api.github.com 通。
⚠️ 为什么必须先剥离：仓库是私有库但仍按公开标准处理，不允许出现本机路径/密钥/chat_id。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "core")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "report", "misc", "wavelet", "smc"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
REPO = "baizhiwei9/techAnly-summary"
BRANCH = "main"
STAGE = "/tmp/techanly_publish"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_assets")

# 需要同步的文件：本地路径（相对 WS） → 仓库路径
FILES = [
    "report/tech_summary.py",
    "report/tech_buypoint.py",
    "report/buypoint_chart.py",
    "chanlun/fractal/chan_chart.py",
    "chanlun/ma/chan_ma_trend_v2.py",
    "chanlun/fractal/chan_smc_index.py",
    "core/datahub.py",
    "core/feishu_card.py",
    "core/minute_hub.py",
    "core/tushare_init.py",
    "misc/weekly_zs.py",
    "smc/smc_core.py",
    "skills/tech-summary/SKILL.md",
    "skills/chan-ma-trend/SKILL.md",
]

# 仓库里另有「公开版」文件（本机版含私有信息，不能直接上传）→ 从 bin/publish_assets 取
# 发布工具自身（仓库里的 tools/ 版本，路径已改相对）
PUBLISH_TOOLS = {}   # 仓库版：工具已在 tools/ 下，无需再自上传

ASSET_FILES = {
    "smc/smc_config.py": "smc_config.py",
    "README.md": "README.md",
    "requirements.txt": "requirements.txt",
    ".gitignore": "gitignore",
    "tools/bootstrap_cache.py": "tools/bootstrap_cache.py",
    "examples/sample_report_中证500.txt": "examples/sample_report_中证500.txt",
}

WS_PUB = ('WS = os.environ.get("TECH_WS") or '
          'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))')

# ── 剥离规则（顺序敏感，逐条 regex.sub）────────────────────────────
STRIP = [
    # 本机绝对路径
    (r'^WS = "/root/\.openclaw/workspace"$', WS_PUB, re.M),
    (r'^CACHE = "/root/\.openclaw/workspace/\.cache/minute"$',
     'CACHE = os.path.join(os.environ.get("TECH_WS") or '
     'os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache/minute")', re.M),
    # tushare token
    (r"^_DEFAULT_PROXY_TOKEN = '[0-9a-f]{20,}'",
     "_DEFAULT_PROXY_TOKEN = ''   # 公开版不内置密钥，请用环境变量或 ~/.config/tushare/proxy_token", re.M),
    (r"DIRECT_TOKEN = os\.environ\.get\(\s*'TUSHARE_DIRECT_TOKEN',\s*'[0-9a-f]{20,}'\)",
     "DIRECT_TOKEN = os.environ.get('TUSHARE_DIRECT_TOKEN', '')", re.S),
    # 飞书 chat_id 默认值 → 空（留空 = 不发，必须显式传 chat=）
    (r'FEISHU_CHAT = os\.environ\.get\("CHAN_FEISHU_CHAT", "oc_[0-9a-f]+"\)\s*# 默认私聊',
     'FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""  # 留空=不发', re.M),
    (r'FEISHU_CHAT = os\.environ\.get\("WZS_FEISHU_CHAT", "oc_[0-9a-f]+"\)',
     'FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("WZS_FEISHU_CHAT") or ""', re.M),
    (r'DEFAULT_CHAT = os\.environ\.get\("CHAN_FEISHU_CHAT", "oc_[0-9a-f]+"\)',
     'DEFAULT_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""', re.M),
    # QQ 私聊 target
    (r'QQ_TARGET = os\.environ\.get\("WZS_QQ_TARGET", "qqbot:c2c:[0-9A-F]+"\)',
     'QQ_TARGET = os.environ.get("WZS_QQ_TARGET", "")', re.M),
]

# feishu_card.py 凭据函数：优先环境变量（本机版直接读 openclaw.json）
FEISHU_CFG_PUBLIC = '''def _cfg():
    """飞书应用凭据：优先环境变量，其次 workspace 同级 openclaw.json（本机部署形态）。"""
    aid = os.environ.get("FEISHU_APP_ID")
    sec = os.environ.get("FEISHU_APP_SECRET")
    if aid and sec:
        return aid, sec
    p = os.path.join(os.path.dirname(WS), "openclaw.json")
    c = json.load(open(p, encoding="utf-8"))["channels"]["feishu"]
    return c["appId"], c["appSecret"]'''


def sanitize(rel, text):
    """把本机版源码转成可公开版本"""
    for pat, rep, *fl in STRIP:
        flags = fl[0] if fl else 0
        text = re.sub(pat, rep, text, flags=flags)
    if rel == "core/feishu_card.py":
        # 本机版 _cfg() 只读 openclaw.json → 公开版允许环境变量优先
        text = re.sub(
            r'def _cfg\(\):\n(?:.*\n)*?    return c\["appId"\], c\["appSecret"\]',
            FEISHU_CFG_PUBLIC, text, count=1)
    if rel == "chanlun/fractal/chan_smc_index.py":
        text = re.sub(
            r'    c = json\.load\(open\(os\.path\.join\(os\.path\.dirname\(WS\), "openclaw\.json"\), encoding="utf-8"\)\)\["channels"\]\["feishu"\]\n'
            r'    b = json\.dumps\(\{"app_id": c\["appId"\], "app_secret": c\["appSecret"\]\}\)\.encode\(\)',
            '    aid, sec = os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")\n'
            '    if not (aid and sec):\n'
            '        c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))["channels"]["feishu"]\n'
            '        aid, sec = c["appId"], c["appSecret"]\n'
            '    b = json.dumps({"app_id": aid, "app_secret": sec}).encode()', text, count=1)
    if rel == "core/tushare_init.py":
        # 公开版要保留「未配置 token」的提示（本机版有内置 token 所以没这段）
        text = text.replace(
            "    except Exception:\n        pass\n    return _DEFAULT_PROXY_TOKEN",
            "    except Exception:\n        pass\n"
            "    if not _DEFAULT_PROXY_TOKEN:\n"
            "        print('[tushare_init] 未配置 token：请设 TUSHARE_PROXY_TOKEN '\n"
            "              '或写 ~/.config/tushare/proxy_token', file=sys.stderr)\n"
            "    return _DEFAULT_PROXY_TOKEN", 1)
    return text


def stage_files():
    os.makedirs(STAGE, exist_ok=True)
    out = []
    for rel in FILES:
        src = os.path.join(WS, rel)
        if not os.path.exists(src):
            print("[缺文件] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        raw = open(src, encoding="utf-8").read()
        txt = sanitize(rel, raw)
        open(dst, "w", encoding="utf-8").write(txt)
        out.append((dst, rel))
    # 公开版资产（原样拷贝，不做替换）
    for rel, asset in ASSET_FILES.items():
        src = os.path.join(ASSETS, asset)
        if not os.path.exists(src):
            print("[缺资产] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
        shutil.copyfile(src, dst)
        out.append((dst, rel))
    return out


def append_changelog(msg, notes):
    p = os.path.join(STAGE, "CHANGELOG.md")
    head = ("# CHANGELOG\n\n"
            "> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。\n\n")
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else head
    if not old.startswith("# CHANGELOG"):
        old = head + old
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = "\n## %s\n\n**%s**\n\n%s\n" % (stamp, msg, notes or "")
    idx = old.find("\n## ")
    new = old if idx < 0 else old[:idx] + entry + old[idx:]
    if idx < 0:
        new = old.rstrip() + "\n" + entry
    open(p, "w", encoding="utf-8").write(new)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--msg", required=True, help="commit message（同时写进 CHANGELOG 标题）")
    ap.add_argument("--notes", default="", help="CHANGELOG 正文（改了什么/为什么/影响文件）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pairs = stage_files()
    # ⚠️ dry-run **不许**写 CHANGELOG（否则试跑几次就把假条目写进仓库历史）
    if a.dry_run:
        cl = os.path.join(STAGE, "CHANGELOG.md")
        if not os.path.exists(cl):
            open(cl, "w", encoding="utf-8").write(
                "# CHANGELOG\n\n> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。\n")
        pairs.append((cl, "CHANGELOG.md"))
    else:
        changelog = append_changelog(a.msg, a.notes)
        pairs.append((changelog, "CHANGELOG.md"))

    mp = os.path.join(STAGE, "_map.txt")
    with open(mp, "w", encoding="utf-8") as f:
        for dst, rel in pairs:
            f.write("%s:%s\n" % (dst, rel))

    # 发布工具自身也要进仓库（tools/ 下，已剥掉本机路径）
    for rel, asset in PUBLISH_TOOLS.items():
        src = os.path.join(ASSETS, asset)
        if not os.path.exists(src):
            print("[缺发布工具] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
        shutil.copyfile(src, dst)
        out.append((dst, rel))

    # 示例图（二进制，只随源码一起更新）
    for png in ("examples/sample_day_600519.png", "examples/sample_h1_600519.png"):
        lo2 = os.path.join(ASSETS, os.path.basename(png))
        if os.path.exists(lo2):
            with open(mp, "a", encoding="utf-8") as f:
                f.write("%s:%s\n" % (lo2, png))

    # ⚠️ 用 gh_commit（Git Data API）把全部文件合成**一个 commit**
    #    contents API 每 PUT 一个文件就是一条 commit → 10 个文件 10 条同名记录，历史没法读
    cmd = [sys.executable, os.path.join(WS, "tools/gh_commit.py"),
           "--repo", REPO, "--branch", BRANCH, "--map-file", mp, "--msg", a.msg]
    if a.dry_run:
        cmd.append("--dry-run")
    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0 and not a.dry_run:
        print("\n✅ 已同步：https://github.com/%s/commits/%s" % (REPO, BRANCH))
    return rc


if __name__ == "__main__":
    sys.exit(main())
