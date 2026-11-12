#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPv6 例外机制：默认强制 IPv4，仅对「只有 IPv6 通」的域名放行

背景（2026-11-12 实测）：
  容器 IPv6 在 2026-11-12 已可正常访问外网，但**东方财富的 K 线接口**
  （push2his.eastmoney.com / *.push2his.eastmoney.com / 1.push2.eastmoney.com …）
  只剩 IPv6 可达：IPv4 全部 RemoteDisconnected，IPv6 200 OK。
  而本机全局规矩是「对外接口一律强制 IPv4」（历史坑：IPv6 静默卡死）。

正确做法不是「一刀切禁 IPv6」也不是「全部放开」，而是：
  · 默认仍强制 IPv4（保留历史防御）
  · 命中白名单域名时，**IPv6 优先**（这些域名 IPv4 已废）
  · 兜底：白名单域名的 IPv6 也解析不出来时，退回 IPv4，不会比原来更差

用法（脚本开头调用一次）：
    from net_guard import install
    install()

    或直接在 import 时生效（模块顶层已自动 install，见文件末尾）

环境变量：
    IPV6_OK_HOSTS  额外放行 IPv6 的域名，逗号分隔（子域名自动匹配）
                    例：IPV6_OK_HOSTS=example.com,foo.cn

为什么不直接把全平台改成 v6 优先：
    1. 历史事故是 IPv6 不通导致**静默卡死**，一刀切放开风险大
    2. tushare / 腾讯 / 同花顺等都能走 IPv4，没必要冒风险
    3. 只有东财 K 线这批必须 IPv6，做白名单最小改动最安全
"""
from __future__ import annotations

import os
import socket

# ── 需要 IPv6 的域名（东财行情族，2026-11-12 实测 IPv4 全废）──────────
_IPV6_OK_DEFAULT = (
    "push2his.eastmoney.com",     # 日/周/月 K 线（盘中/日线主数据源）
    "push2.eastmoney.com",        # 实时快照 clist（IPv4 有时通，v6 更稳）
    "eastmoney.com",              # 兜底：所有 *.eastmoney.com 子域
)

_orig_gai = socket.getaddrinfo


def _extra_hosts():
    raw = os.environ.get("IPV6_OK_HOSTS", "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


_IPV6_OK = tuple(_IPV6_OK_DEFAULT) + _extra_hosts()


def _is_v6_ok(host):
    """域名是否命中 IPv6 白名单（支持子域匹配）"""
    if not host:
        return False
    h = host.lower().rstrip(".")
    for ok in _IPV6_OK:
        if h == ok or h.endswith("." + ok):
            return True
    return False


def _resolve(host, port, family=0, type=0, proto=0, flags=0):
    """改造后的 getaddrinfo：白名单域名 IPv6 优先，其余强制 IPv4"""
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")

    if _is_v6_ok(host):
        # 白名单：先要 v6，拿不到再退回全部（含 v4）——不会比原来更差
        try:
            rs6 = _orig_gai(host, port, socket.AF_INET6, type, proto, flags)
            if rs6:
                try:
                    rs4 = _orig_gai(host, port, socket.AF_INET, type, proto, flags)
                except Exception:
                    rs4 = []
                # v6 在前，v4 在后（v6 挂了还能兜）
                return list(rs6) + list(rs4)
        except Exception:
            pass
        # v6 解析失败 → 原样返回（可能是纯 v4 域名）
        return _orig_gai(host, port, family, type, proto, flags)

    # 非白名单：强制 IPv4（保留历史防御，避免 IPv6 静默卡死）
    rs = _orig_gai(host, port, family, type, proto, flags)
    v4 = [r for r in rs if r[0] == socket.AF_INET]
    # 一个 v4 都没有（纯 v6 域名）→ 只能返回原样，否则连不通
    return v4 if v4 else rs


def install():
    """幂等安装（多次调用无副作用）"""
    if socket.getaddrinfo is not _resolve:
        socket.getaddrinfo = _resolve
    return _resolve


def describe():
    """自检：返回当前生效的白名单"""
    return {"ipv6_ok_hosts": list(_IPV6_OK), "installed": socket.getaddrinfo is _resolve}


# 模块 import 即生效
install()


if __name__ == "__main__":
    import requests
    print("白名单:", describe()["ipv6_ok_hosts"])
    H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    tests = [
        ("东财 K线（应走 v6）", "https://push2his.eastmoney.com/api/qt/stock/kline/get",
         dict(secid="1.600519", klt=101, fqt=1, lmt=3, end="20500101",
              fields1="f1", fields2="f51,f53")),
        ("东财 clist（应走 v6）", "https://push2.eastmoney.com/api/qt/clist/get",
         dict(pn=1, pz=3, po=1, np=1, fltt=2, invt=2, fid="f3",
              fs="m:1+t:2", fields="f2,f12,f14")),
        ("腾讯（应走 v4）", "https://qt.gtimg.cn/q=sh600519", None),
        ("tushare（应走 v4）", "https://api.tushare.pro", None),
    ]
    for name, url, params in tests:
        try:
            r = requests.get(url, params=params, timeout=12, headers=H)
            print("  %-22s %s len=%d" % (name, r.status_code, len(r.content)))
        except Exception as e:
            print("  %-22s FAIL %s %s" % (name, type(e).__name__, str(e)[:50]))
