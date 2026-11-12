#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gh_publish_techanly.py —— 把技术分析汇总（techAnly-summary）同步到 GitHub
=====================================================================
一条命令：本地文件 → 剥离私有信息（路径/token/chat_id）→ 逐文件比对 → 一个 commit 推上去。

用法：
  python3 bin/gh_publish_techanly.py --msg "fix: 均线版笔画端点取区间极值"
  python3 bin/gh_publish_techanly.py --msg "..." --dry-run        # 只看差异，不写 CHANGELOG
  python3 bin/gh_publish_techanly.py --msg "..." --notes "改了什么/为什么"

⚠️ 为什么不用 git push：本机 github.com:443 间歇阻断（TLS 半路断），api.github.com 通。
⚠️ 为什么必须先剥离：仓库是公开库，不允许出现本机路径 / 密钥 / chat_id / webhook。
⚠️ 2026-11-12 目录整理后：真实文件在 core/ report/ chanlun/ misc/ wavelet/ smc/ strategies/，
   bin/ 只剩 25 个兼容 shim —— 两者都要入库（shim 保证旧路径仍可用）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "baizhiwei9/techAnly-summary"
BRANCH = "main"
STAGE = "/tmp/techanly_publish"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_assets")

# ── 需要同步的文件清单（相对 WS，仓库路径 == 本地路径）──────────────
# 大佬 2026-11-12 定稿：「就缠论一个目录，SMC 一个目录，其他的杂项一个目录，小波一个目录」
#   顶层只有四类：chanlun/（缠论） smc/ wavelet/（小波） misc/（其余全部）
#   外加 bin/（兼容 shim，cron 依赖）与 skills/（OpenClaw 技能发现要求放顶层）
TREES = [
    "chanlun",
    "smc",
    "wavelet",
    "misc",             # 杂项：core/report/strategies/tools/examples/reports/weekly_zs/xq_*
    "bin",              # 兼容层 shim（只挑 shim，见 _is_shim）
    "skills/tech-summary",
    "skills/chan-ma-trend",
]
# 目录里明确不要的（别的项目 / 大文件 / 缓存）
TREE_SKIP_DIRS = {"__pycache__", "cache", "reports", "kc50_dynamics", "vendor"}
TREE_SKIP_EXT = (".db", ".sqlite", ".log", ".pyc", ".zip", ".parquet", ".csv",
                 ".bak", ".orig", ".rej")
TREE_SKIP_NAME = ("stats_h1.json", "stats_day.json", "smc_sim.db")

# 仓库里已废弃的顶层路径（本地整理后不存在了）→ 上传时顺带删除
REMOTE_DELETE_PREFIX = ("core/", "report/", "strategies/", "tools/", "examples/", "reports/")

# 单文件清单
FILES = [
    "requirements.txt",
    "README.md",
    ".gitignore",
    "smc/smc_config.py",
    "misc/examples/sample_report_中证500.txt",
]
# 发布工具自身（已剥掉本机路径的公开版）
PUBLISH_TOOLS = {
    "misc/tools/gh_commit.py": "tools/gh_commit.py",
    "misc/tools/gh_publish.py": "tools/gh_publish.py",
    "misc/tools/publish_techsummary.py": "tools/publish_techsummary.py",
}
# 发布工具主脚本：直接从本机版消毒后落库，避免「本地改了、仓库还是旧清单」
SELF_TOOL = "misc/tools/publish_techanly.py"

# 仓库里另有「公开版」文件（本机版含私有信息，不能直接上传）→ 从 bin/publish_assets 取
ASSET_FILES = {
    "smc/smc_config.py": "smc_config.py",
    "README.md": "README-techanly.md" if os.path.exists(os.path.join(ASSETS, "README-techanly.md")) else "README.md",
    "requirements.txt": "requirements.txt",
    ".gitignore": "gitignore",
    "misc/tools/bootstrap_cache.py": "tools/bootstrap_cache.py",
    "misc/examples/sample_report_中证500.txt": "examples/sample_report_中证500.txt",
}

WS_PUB = ('WS = os.environ.get("TECH_WS") or '
          'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))')

# ── 剥离规则（顺序敏感，逐条 regex.sub）────────────────────────────
STRIP = [
    # 本机工作区绝对路径（各种写法）
    (r'^WS = "/root/\.openclaw/workspace"$', WS_PUB, re.M),
    (r"^WS = '/root/\.openclaw/workspace'$", WS_PUB, re.M),
    (r'^CACHE = "/root/\.openclaw/workspace/\.cache/minute"$',
     'CACHE = os.path.join(os.environ.get("TECH_WS") or '
     'os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache/minute")', re.M),
    (r'^    return "/root/\.openclaw/workspace"$',
     '    return os.path.dirname(os.path.dirname(os.path.abspath(start)))', re.M),
    (r'^BASE = "/root/\.openclaw/workspace"$',
     'BASE = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))', re.M),
    (r'sys\.path\.insert\(0, "/root/\.openclaw/workspace/bin"\)',
     'sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))'),
    # tushare token（泛化匹配：不把真实密钥写进这个脚本本身）
    (r"^_DEFAULT_PROXY_TOKEN = '[0-9a-f]{20,}'",
     "_DEFAULT_PROXY_TOKEN = ''   # 公开版不内置密钥，请用环境变量或 ~/.config/tushare/proxy_token", re.M),
    (r"DIRECT_TOKEN = os\.environ\.get\(\s*'TUSHARE_DIRECT_TOKEN',\s*'[0-9a-f]{20,}'\)",
     "DIRECT_TOKEN = os.environ.get('TUSHARE_DIRECT_TOKEN', '')", re.S),
    # 飞书 chat_id 默认值 → 空（留空 = 不发，必须显式传 chat=）
    (r'FEISHU_CHAT = os\.environ\.get\("CHAN_FEISHU_CHAT", "oc_[0-9a-f]+"\)\s*# 默认私聊',
     'FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""  # 留空=不发', re.M),
    (r'FEISHU_CHAT = os\.environ\.get\("WZS_FEISHU_CHAT", "oc_[0-9a-f]+"\)',
     'FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("WZS_FEISHU_CHAT") or ""', re.M),
    (r'FEISHU_CHAT = os\.environ\.get\("CHAN_FEISHU_CHAT", "oc_[0-9a-f]+"\)',
     'FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""  # 留空=不发', re.M),
    (r'DEFAULT_CHAT = os\.environ\.get\("CHAN_FEISHU_CHAT", "oc_[0-9a-f]+"\)',
     'DEFAULT_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""', re.M),
    # QQ 私聊 target
    (r'QQ_TARGET = os\.environ\.get\("WZS_QQ_TARGET", "qqbot:c2c:[0-9A-F]+"\)',
     'QQ_TARGET = os.environ.get("WZS_QQ_TARGET", "")', re.M),
    (r'QQ_TARGET = os\.environ\.get\("CHAN_QQ_TARGET", "qqbot:c2c:[0-9A-F]+"\)',
     'QQ_TARGET = os.environ.get("CHAN_QQ_TARGET", "")   # 留空=不发，需显式设 environment', re.M),
    (r'QQ_ACCOUNT = os\.environ\.get\("CHAN_QQ_ACCOUNT", "bot[0-9]+"\)',
     'QQ_ACCOUNT = os.environ.get("CHAN_QQ_ACCOUNT", "")   # 本机是 bot2，不设则按默认账号发', re.M),
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


def _is_shim(path):
    """bin/ 下只收 2026-11-12 目录整理生成的兼容 shim"""
    try:
        head = open(path, encoding="utf-8", errors="ignore").read(600)
    except Exception:
        return False
    return "兼容入口（2026-11-12 目录整理）" in head


def _walk_tree(rel_dir):
    out = []
    base = os.path.join(WS, rel_dir)
    for root, dirs, fs in os.walk(base):
        dirs[:] = [d for d in dirs if d not in TREE_SKIP_DIRS]
        for f in fs:
            if f.endswith(TREE_SKIP_EXT) or f in TREE_SKIP_NAME or ".bak" in f:
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, WS)
            if rel.startswith("bin" + os.sep) and not _is_shim(p):
                continue      # bin/ 里还有别的项目的脚本，不收
            out.append(rel)
    return out


def collect_files():
    seen, out = set(), []
    for d in TREES:
        for rel in _walk_tree(d):
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    for rel in FILES:
        if rel not in seen and os.path.exists(os.path.join(WS, rel)):
            seen.add(rel)
            out.append(rel)
    return out


def sanitize(rel, text):
    """把本机版源码转成可公开版本"""
    for pat, rep, *fl in STRIP:
        flags = fl[0] if fl else 0
        text = re.sub(pat, rep, text, flags=flags)
    # 文档/注释里残留的本机绝对路径（README、说明文字、示例命令）统一换成仓库根
    text = text.replace("${TECH_WS:-<repo>}", "${TECH_WS:-<repo>}")
    if rel.endswith("misc/core/feishu_card.py"):
        # 本机版 _cfg() 只读 openclaw.json → 公开版允许环境变量优先
        text = re.sub(
            r'def _cfg\(\):\n(?:.*\n)*?    return c\["appId"\], c\["appSecret"\]',
            FEISHU_CFG_PUBLIC, text, count=1)
    if rel.endswith("chan_smc_index.py"):
        text = re.sub(
            r'    c = json\.load\(open\(os\.path\.join\(os\.path\.dirname\(WS\), "openclaw\.json"\), encoding="utf-8"\)\)\["channels"\]\["feishu"\]\n'
            r'    b = json\.dumps\(\{"app_id": c\["appId"\], "app_secret": c\["appSecret"\]\}\)\.encode\(\)',
            '    aid, sec = os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")\n'
            '    if not (aid and sec):\n'
            '        c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))["channels"]["feishu"]\n'
            '        aid, sec = c["appId"], c["appSecret"]\n'
            '    b = json.dumps({"app_id": aid, "app_secret": sec}).encode()', text, count=1)
    if rel.endswith("tushare_init.py"):
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
    for rel in collect_files():
        src = os.path.join(WS, rel)
        if not os.path.exists(src):
            print("[缺文件] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
        try:
            raw = open(src, encoding="utf-8").read()
        except UnicodeDecodeError:
            shutil.copyfile(src, dst)          # 非 UTF-8（个别历史 sh/文本）：原样拷，不做剥离
            out.append(rel)
            continue
        if rel.endswith((".png", ".jpg", ".pdf")):
            shutil.copyfile(src, dst)
        else:
            open(dst, "w", encoding="utf-8").write(sanitize(rel, raw))
        out.append(rel)
    # 公开版资产（原样拷贝，不做替换）
    for rel, asset in ASSET_FILES.items():
        src = os.path.join(ASSETS, asset)
        if not os.path.exists(src):
            print("[缺资产] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
        shutil.copyfile(src, dst)
        if rel not in out:
            out.append(rel)
    # 发布工具自身
    for rel, asset in PUBLISH_TOOLS.items():
        src = os.path.join(ASSETS, asset)
        if not os.path.exists(src):
            print("[缺发布工具] %s" % rel)
            continue
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
        shutil.copyfile(src, dst)
        if rel not in out:
            out.append(rel)
    # 发布工具主脚本：本机版 → 消毒 → tools/publish_techanly.py
    src = os.path.join(WS, "bin/gh_publish_techanly.py")
    if os.path.exists(src):
        txt = sanitize(SELF_TOOL, open(src, encoding="utf-8").read())
        # 公开版：assets 就在仓库 tools/ 下，其余行为不变
        txt = txt.replace('ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_assets")',
                          'ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_assets")')
        dst = os.path.join(STAGE, SELF_TOOL)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w", encoding="utf-8").write(txt)
        if SELF_TOOL not in out:
            out.append(SELF_TOOL)
    return out


def append_changelog(msg, notes):
    p = os.path.join(STAGE, "CHANGELOG.md")
    notes = (notes or "").replace("${TECH_WS:-<repo>}", "${TECH_WS:-<repo>}")
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
    new = new.replace("${TECH_WS:-<repo>}", "${TECH_WS:-<repo>}")
    open(p, "w", encoding="utf-8").write(new)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--msg", required=True, help="commit message（同时写进 CHANGELOG 标题）")
    ap.add_argument("--notes", default="", help="CHANGELOG 正文（改了什么/为什么/影响文件）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files = stage_files()
    print("待上传文件 %d 个（已剥离私有信息）" % len(files))

    # CHANGELOG：dry-run 不许写（否则试跑就把假条目写进仓库）
    cl = os.path.join(STAGE, "CHANGELOG.md")
    if a.dry_run:
        if not os.path.exists(cl):
            open(cl, "w", encoding="utf-8").write(
                "# CHANGELOG\n\n> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。\n")
        files.append("CHANGELOG.md")
    else:
        append_changelog(a.msg, a.notes)
        files.append("CHANGELOG.md")

    mp = os.path.join(STAGE, "_map.txt")
    with open(mp, "w", encoding="utf-8") as f:
        for rel in files:
            f.write("%s:%s\n" % (os.path.join(STAGE, rel), rel))

    # ⚠️ 用 gh_commit（Git Data API）把全部文件合成**一个 commit**
    #    contents API 每 PUT 一个文件就是一条 commit → 几十条同名记录，历史没法读
    cmd = [sys.executable, os.path.join(WS, "misc/tools/gh_commit.py"),
           "--repo", REPO, "--branch", BRANCH, "--map-file", mp, "--msg", a.msg]
    for pre in REMOTE_DELETE_PREFIX:
        cmd += ["--delete-prefix", pre]
    if a.dry_run:
        cmd.append("--dry-run")
    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0 and not a.dry_run:
        print("\n✅ 已同步：https://github.com/%s/commits/%s" % (REPO, BRANCH))
    return rc


if __name__ == "__main__":
    sys.exit(main())
