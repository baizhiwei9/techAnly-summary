#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify · 告警通知注册表（库内只负责**触发**，通道由调用方注册）
=====================================================================
2026-11-24 大佬：「它这边应该有一个触发预警的逻辑，预警了调用上层的预警。
但是你这个预警的注册函数，是你调任务调用的时候注册进去的，比如说发送邮件
或者发送飞书。这样的话你这个库（techAnly-summary）就能公开了。」

为什么这样拆
--------------------------------
公开库不能内置任何本机账号、路径或第三方发信模块：
- 以前 `mailer.send_alert()` 直接按路径 import 本机 `email_sender.py`，
  连口令都从它的 `DEFAULT_CONFIG` 读——库一旦公开就带上了本机依赖；
- 而且调用方没有选择权，想发飞书/钉钉只能改库。

现在：
- 库只提供 `register()` / `emit()`，**默认零通道**；
- 任务脚本在调用前注册自己的通道（邮件、飞书、webhook、写库……）；
- CLI 跨进程调用时，用环境变量声明注册文件，库启动时自动 import：

      export TECHANLY_NOTIFIERS=/path/to/local_notifier.py
      python3 charts/chan_fractal.py --stack ... --alert-email

通道签名（同步、无返回值要求，返回 False 视为失败）
--------------------------------
    def my_send(event: AlertEvent) -> bool:
        ...
    notify.register("email", my_send)

注册文件在 import 时调用 `register` 即可，例如：

    # local_notifier.py（放在本机，不进公开库）
    import notify                      # 库已把 misc/core 放进 sys.path

    def _email(event):
        return my_email_sender(
            subject=event.subject, text=event.body,
            attachments=list(event.attachments),
            to_addr=(event.meta or {}).get("to"),
        )

    notify.register("email", _email)

查看 / 自测
--------------------------------
    python3 misc/core/notify.py --list     # 列出当前已注册通道
    python3 misc/core/notify.py --test     # 用已注册通道发一条测试告警（不真发需 --dry-run）
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import os
import re
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable


# ═══════════════════════════ 事件 ═══════════════════════════
@dataclass
class AlertEvent:
    """一次告警的完整上下文。通道按需取字段，不要修改它。"""
    subject: str
    body: str
    attachments: tuple = ()        # 本地文件路径（图片/报告）
    source: str = ""               # 来源任务名，如「技术分析汇总」
    meta: dict = field(default_factory=dict)   # 通道自定义参数（如 {"to": "..."}）
    dry_run: bool = False


# ═══════════════════════════ 注册表 ═══════════════════════════
_LOCK = threading.RLock()
_REGISTRY: "OrderedDict[str, Callable[[AlertEvent], Any]]" = OrderedDict()
_LOADED: set = set()               # 已尝试加载过的模块标识（失败也不重复刷屏）

_THIS = sys.modules[__name__]
# 让被加载的注册文件无论以 notify / misc.core.notify / __main__ 哪种方式进来，
# 都能找到同一个注册表。
sys.modules.setdefault("_techanly_notify", _THIS)
if __name__ != "__main__":
    sys.modules.setdefault("notify", _THIS)
else:
    sys.modules["notify"] = _THIS


def register(name: str, fn: Callable[[AlertEvent], Any], *, replace: bool = True) -> None:
    """注册一个通知通道。同名通道默认覆盖（便于任务重复初始化）。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("通道名不能为空")
    if not callable(fn):
        raise TypeError("通知通道必须可调用：fn(event) -> bool")
    with _LOCK:
        if not replace and name in _REGISTRY:
            raise ValueError("通道已存在：%s" % name)
        _REGISTRY[name] = fn


def unregister(name: str) -> bool:
    """注销通道，返回是否存在过。"""
    with _LOCK:
        return _REGISTRY.pop(str(name or "").strip(), None) is not None


def clear() -> None:
    """清空所有通道（测试用）。"""
    with _LOCK:
        _REGISTRY.clear()


def channels() -> list:
    """当前已注册通道名（按注册顺序）。"""
    with _LOCK:
        return list(_REGISTRY)


# ═══════════════════════════ 加载注册文件 ═══════════════════════════
def _load_one(spec: str) -> None:
    """加载一个通知模块：支持 .py 路径或已安装的模块名。"""
    spec = os.path.expanduser(str(spec).strip())
    if not spec:
        return
    key = spec
    if spec.endswith(".py") or os.sep in spec:
        key = os.path.abspath(spec)
        if not os.path.isfile(key):
            print("[notify] ⚠️ 通知模块不存在：%s" % key)
            return
        modname = "_techanly_notifier_%s" % hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
        mspec = importlib.util.spec_from_file_location(modname, key)
        if mspec is None or mspec.loader is None:
            print("[notify] ⚠️ 无法加载通知模块：%s" % key)
            return
        mod = importlib.util.module_from_spec(mspec)
        mspec.loader.exec_module(mod)
    else:
        importlib.import_module(spec)
    print("[notify] 已加载通知模块：%s" % os.path.basename(key))


def autoload(spec: str | None = None) -> list:
    """按 `TECHANLY_NOTIFIERS`（os.pathsep 分隔）或显式 spec 加载注册文件。

    幂等：同一个文件只执行一次。加载失败只告警，不抛异常。
    返回本次成功新加载的模块数。
    """
    raw = spec if spec is not None else os.environ.get("TECHANLY_NOTIFIERS", "")
    if not raw:
        return []
    before = len(_REGISTRY)
    loaded = 0
    for item in str(raw).split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        ident = os.path.abspath(os.path.expanduser(item)) if (
            item.endswith(".py") or os.sep in item) else item
        with _LOCK:
            if ident in _LOADED:
                continue
            _LOADED.add(ident)
        try:
            _load_one(item)
            loaded += 1
        except Exception as e:                                   # noqa: BLE001
            print("[notify] ⚠️ 通知模块加载失败：%s → %s"
                  % (os.path.basename(item), _scrub(e)[:160]))
    if len(_REGISTRY) == before and loaded:
        print("[notify] ⚠️ 注册文件已执行但没有调用 register()，"
              "请在模块 import 时注册通道")
    return loaded


# ═══════════════════════════ 脱敏 ═══════════════════════════
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _mask(addr: str) -> str:
    s = str(addr or "").strip()
    if "@" not in s:
        return s or "—"
    local, _, domain = s.partition("@")
    if len(local) <= 2:
        return local[:1] + "***@" + domain
    if len(local) <= 4:
        return local[:1] + "***" + local[-1:] + "@" + domain
    return local[:3] + "***" + local[-3:] + "@" + domain


def _scrub(text: Any) -> str:
    return _EMAIL_RX.sub(lambda m: _mask(m.group(0)), str(text))


def _fmt_attach(attachments) -> str:
    names = [os.path.basename(str(f)) for f in (attachments or ()) if f]
    return ", ".join(names) or "—"


# ═══════════════════════════ 触发 ═══════════════════════════
def emit(subject: str, body: str = "", *, attachments=None, source: str = "",
         meta: dict | None = None, dry_run: bool = False,
         autoload_first: bool = True) -> dict:
    """把告警交给所有已注册通道。返回 {通道名: 是否成功}。

    规则：
    - 没有通道 → 打印醒目提示，返回 {}（调用方据此判断「未送达」）；
    - 单通道异常不影响其它通道；
    - dry_run → 只打印将要发往哪些通道，不调用通道本身（结果记为 True）。
    """
    if autoload_first:
        autoload()
    files = attachments if isinstance(attachments, (list, tuple)) else (
        [attachments] if attachments else [])
    files = tuple(str(f) for f in files if f)
    ev = AlertEvent(subject=_scrub(subject), body=body, attachments=files,
                    source=str(source or ""), meta=dict(meta or {}),
                    dry_run=bool(dry_run))

    with _LOCK:
        items = list(_REGISTRY.items())
    if not items:
        print("[notify] ⚠️ 没有注册任何通知通道，告警未送达。"
              "调用方需先 register()，或设置 TECHANLY_NOTIFIERS=/path/to/notifier.py")
        return {}

    results = OrderedDict()
    for name, fn in items:
        if ev.dry_run:
            print("[notify] DRY-RUN 通道=%s ｜ 标题=%s ｜ 附件=%s"
                  % (name, ev.subject, _fmt_attach(files)))
            results[name] = True
            continue
        try:
            r = fn(ev)
            results[name] = True if r is None else bool(r)
        except Exception as e:                                   # noqa: BLE001
            print("[notify] ❌ 通道 %s 异常：%s" % (name, _scrub(e)[:160]))
            results[name] = False

    ok = [n for n, v in results.items() if v]
    bad = [n for n, v in results.items() if not v]
    if not ev.dry_run:
        if ok:
            print("[notify] ✅ 已送达：%s" % ", ".join(ok))
        if bad:
            print("[notify] ❌ 未送达：%s" % ", ".join(bad))
    return dict(results)


def emit_bool(*args, **kwargs) -> bool:
    """`emit()` 的布尔封装：至少一个通道成功即 True。"""
    res = emit(*args, **kwargs)
    return bool(res) and any(res.values())


# ═══════════════════════════ CLI ═══════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(description="告警通知注册表（列出/自测通道）")
    ap.add_argument("--list", action="store_true", help="列出当前注册的通道")
    ap.add_argument("--test", action="store_true", help="发一条测试告警")
    ap.add_argument("--dry-run", action="store_true", help="只打印不真发")
    a = ap.parse_args(argv)
    autoload()
    if a.list or not a.test:
        ch = channels()
        print("[notify] 通道：%s" % (", ".join(ch) if ch else "（无）"))
        print("[notify] 注册文件：%s"
              % (os.environ.get("TECHANLY_NOTIFIERS") or "（未设置 TECHANLY_NOTIFIERS）"))
        if not a.test:
            return 0
    res = emit("[notify] 测试告警", "如果你收到这条消息，说明通知通道已接通。",
               source="notify-selftest", dry_run=a.dry_run)
    return 0 if res and any(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
