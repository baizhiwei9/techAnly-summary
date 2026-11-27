#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两融图技能自检：登记 / 路由 / 出图 / 数据层 / 直连审计，一次跑完。"""
import ast, os, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DATA_HUB_PATH", "/root/.openclaw/workspace/optstack")

ok = True
def chk(name, cond, extra=""):
    global ok
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + extra) if extra else ""))
    if not cond: ok = False

print("① registry 登记")
import misc.charts.registry as registry
d = registry.get("margin")
chk("registry 里有 margin", d is not None)
chk("module 指向 charts/margin.py", d and d["module"] == "charts/margin.py")
chk("default_out 指向 reports/charts/margin", d and d["default_out"] == "reports/charts/margin")
chk("out_kind=file", d and d.get("out_kind") == "file")
chk("keywords ≥ 15 个", d and len(d["keywords"]) >= 15, "实际 %d" % (len(d["keywords"]) if d else 0))

print("\n② 说人话路由（大佬可能说的每一句）")
for q in ["两融图", "A股两融", "融资融券余额", "两融账户", "个人投资者数量",
          "有融资融券负债", "维持担保比例", "画两融", "两融全景", "融资余额", "两融负债"]:
    chk(q, registry.resolve(q) == "margin", "→ %s" % registry.resolve(q))

print("\n③ 脚本存在 + 语法")
sp = os.path.join(REPO, d["module"])
chk("脚本存在", os.path.exists(sp))
tree = ast.parse(open(sp, encoding="utf-8").read())
chk("语法可解析", True)
bad = []
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        for a in n.names:
            if a.name.split(".")[0] in ("akshare", "requests", "urllib", "httpx"):
                bad.append(a.name)
    elif isinstance(n, ast.ImportFrom):
        if n.module and n.module.split(".")[0] in ("akshare", "requests", "urllib", "httpx"):
            bad.append(n.module)
chk("无直连 akshare/requests/urllib", not bad, str(bad))

print("\n④ 出图（真实跑一遍）")
r = subprocess.run([sys.executable, os.path.join(REPO, "misc/charts/run.py"),
                    "margin", "--days", "120", "--out", "/tmp/_sc.png"],
                   capture_output=True, text=True, timeout=400)
chk("run.py 退出码 0", r.returncode == 0, "rc=%d" % r.returncode)
chk("图文件已生成", os.path.exists("/tmp/_sc.png"),
    "%d KB" % (os.path.getsize("/tmp/_sc.png") // 1024) if os.path.exists("/tmp/_sc.png") else "")
chk("输出含图标题", "A股两融" in r.stdout)
chk("输出含图路径", ".png" in r.stdout)

print("\n⑤ 数据层（只读 EDB）")
r2 = subprocess.run([sys.executable, sp, "--days", "250", "--json"],
                    capture_output=True, text=True, timeout=400,
                    env={**os.environ, "DATA_HUB_PATH": "/root/.openclaw/workspace/optstack"})
import json
try:
    j = json.loads(r2.stdout[r2.stdout.index("{"):r2.stdout.rindex("}")+1])
except Exception as e:
    j = {}
    chk("--json 可解析", False, str(e))
if j:
    chk("--json 可解析", True)
    chk("source = edb:cn_market", j.get("source") == "edb:cn_market", str(j.get("source")))
    chk("bars ≥ 250（窗口够）", j.get("bars", 0) >= 250, "实际 %s" % j.get("bars"))
    chk("个人投资者 ≈ 847.9", abs(j.get("n_indiv", 0) - 847.9) < 1, str(j.get("n_indiv")))
    chk("有负债 ≈ 198.8", abs(j.get("n_debt", 0) - 198.8) < 1, str(j.get("n_debt")))
    chk("担保比例 ≈ 247.7", abs(j.get("ratio", 0) - 247.7) < 1, str(j.get("ratio")))

print("\n" + ("=" * 50))
print("✅ 全部通过" if ok else "❌ 有失败项")
sys.exit(0 if ok else 1)
