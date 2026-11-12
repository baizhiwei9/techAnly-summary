# -*- coding: utf-8 -*-
"""全局网络出口规则（2026-11-12 大佬定：东财可以用 IPv6）

由 net_guard_hook.pth 在**每个 python3 解释器启动时自动 import**，
所有脚本（含 cron / 手工跑 / 子进程）自动生效，不需要每个脚本自己写。

规则：
  · 默认强制 IPv4          —— 历史坑：容器 IPv6 曾不通，域名解析出 v6 会静默卡死到超时
  · 东财行情族走 IPv6       —— 2026-11-12 实测：东财 K 线 IPv4 全部 RemoteDisconnected，
                              只有 IPv6 可达（130~190ms，并发 100 次零失败，60 秒持续零失败）
  · 白名单域名 v6 解析失败 → 自动退回 v4，不会比原来更差
  · 纯 v6 域名（一个 v4 都没有）→ 原样返回，避免人为连不通

⭐ 关键技术点：工作区有 176 个脚本硬编码 `socket.getaddrinfo = lambda ... AF_INET`，
   它们在自己的 import 阶段会覆盖掉任何"先设置好"的补丁。
   而 `socket` 是内置模块，`type(socket).__setattr__` **不可修改**
   （cannot set '__setattr__' attribute of immutable type 'module'）。

   解法：把 `sys.modules['socket']` 换成一个**自定义子类实例**：
       class _SocketModule(types.ModuleType):
           def __setattr__(self, name, value):
               if name == "getaddrinfo" and not 是我们的策略:
                   return          # 静默吞掉脚本的一刀切禁 IPv6
               super().__setattr__(name, value)
   之后脚本 `import socket` 拿到的是这个实例，它写 `socket.getaddrinfo = lambda...`
   会被吞掉，规则永远生效。

环境变量：
  NET_GUARD=0              完全关掉（退回脚本自己的行为）
  IPV6_OK_HOSTS=a.com,b.cn 额外放行 IPv6 的域名（子域自动匹配）
  NET_GUARD_VERBOSE=1      启动时打印一次规则

自检：
  python3 -c "import socket;print(socket.getaddrinfo('push2his.eastmoney.com',443)[0][0])"
  → 应输出 AddressFamily.AF_INET6
"""
from __future__ import annotations

import os
import sys
import types

_ENABLED = os.environ.get("NET_GUARD", "1") not in ("0", "false", "no", "")
_VERBOSE = os.environ.get("NET_GUARD_VERBOSE", "0") not in ("0", "false", "no", "")

# ── 需要走 IPv6 的域名（2026-11-12 实测东财 K 线 IPv4 全废）──────────
_IPV6_OK_DEFAULT = (
    "push2his.eastmoney.com",     # 日/周/月 K 线（盘中 + 日线主数据源）
    "push2.eastmoney.com",        # 实时快照 clist
    "eastmoney.com",              # 兜底：所有 *.eastmoney.com 子域
)


def _extra_hosts():
    raw = os.environ.get("IPV6_OK_HOSTS", "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


_IPV6_OK = tuple(_IPV6_OK_DEFAULT) + _extra_hosts()

# ── 拿到真正的 socket 模块并保存原始 getaddrinfo ─────────────────────
# ⚠️ .pth 在 site 初始化早期执行，此时 sys.modules['socket'] 可能还不存在
#    → 用 _get_socket() 延迟获取，而不是模块级直接索引
_socket = None
_ORIG = None


def _get_socket():
    global _socket, _ORIG
    if _socket is None:
        import socket as _s
        _socket = _s
        if not hasattr(_socket, "__net_guard_orig__"):
            _socket.__net_guard_orig__ = _socket.getaddrinfo
        _ORIG = _socket.__net_guard_orig__
    return _socket


def _is_v6_ok(host):
    if not host:
        return False
    h = host.lower().rstrip(".")
    for ok in _IPV6_OK:
        if h == ok or h.endswith("." + ok):
            return True
    return False


def _policy(host, port, family=0, type=0, proto=0, flags=0):
    """出口策略：白名单走 v6，其余强制 v4"""
    sk = _get_socket()
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")

    if _is_v6_ok(host):
        try:
            rs6 = _ORIG(host, port, sk.AF_INET6, type, proto, flags)
            if rs6:
                try:
                    rs4 = _ORIG(host, port, sk.AF_INET, type, proto, flags)
                except Exception:
                    rs4 = []
                return list(rs6) + list(rs4)   # v6 优先，v4 兜底
        except Exception:
            pass
        return _ORIG(host, port, family, type, proto, flags)

    rs = _ORIG(host, port, family, type, proto, flags)
    v4 = [r for r in rs if r[0] == sk.AF_INET]
    return v4 if v4 else rs    # 纯 v6 域名不能硬砍，否则连不通


_policy.__net_guard_policy__ = True


class _GuardedModule(types.ModuleType):
    """包一层 socket 模块：拦住「脚本自己硬编码禁 IPv6」的赋值"""

    def __setattr__(self, name, value):
        if (name == "getaddrinfo" and _ENABLED
                and not getattr(value, "__net_guard_policy__", False)):
            # 脚本想在 socket.getaddrinfo 上装自己的过滤器 → 忽略，保持策略
            super().__setattr__("__net_guard_blocked__", True)
            return
        super().__setattr__(name, value)


def _install():
    if not _ENABLED:
        return
    sk = _get_socket()
    # 1) 先把策略装到真实模块上（让已有 `from socket import getaddrinfo` 也生效）
    sk.getaddrinfo = _policy
    # 2) 再把 sys.modules['socket'] 换成受管子类，拦截后续赋值
    if not isinstance(sk, _GuardedModule):
        guarded = _GuardedModule("socket")
        guarded.__dict__.update(sk.__dict__)
        try:
            guarded.__doc__ = sk.__doc__
            guarded.__file__ = sk.__file__
            guarded.__spec__ = sk.__spec__
            guarded.__loader__ = sk.__loader__
            guarded.__path__ = getattr(sk, "__path__", [])
        except Exception:
            pass
        guarded.getaddrinfo = _policy
        sys.modules["socket"] = guarded
    if _VERBOSE:
        print("[net_guard] 出口策略已生效｜白名单 IPv6: %s" % ",".join(_IPV6_OK),
              file=sys.stderr)


_install()


def describe():
    cur = sys.modules["socket"]
    return {
        "enabled": _ENABLED,
        "ipv6_ok_hosts": list(_IPV6_OK),
        "guarded_module": isinstance(cur, _GuardedModule),
        "policy_installed": getattr(cur.getaddrinfo, "__net_guard_policy__", False),
        "orig_saved": hasattr(cur, "__net_guard_orig__"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), ensure_ascii=False, indent=2))
    cur = sys.modules["socket"]
    for h in ("push2his.eastmoney.com", "api.tushare.pro", "qt.gtimg.cn"):
        try:
            print("%-30s → %s" % (h, cur.getaddrinfo(h, 443)[0][0].name))
        except Exception as e:
            print("%-30s → 失败 %s" % (h, type(e).__name__))
