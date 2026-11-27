#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mailer · 统一的邮件发送（收件人/发件人**全部脱敏输出**）
=====================================================================
2026-11-16 建。大佬：「它发邮件，但是这邮件地址你得给我脱敏了啊」。
2026-11-24 起本文件**只是通用 SMTP 工具**：告警不再直接调它，公开库只负责
触发、通道由调用方注册（见 `misc/core/notify.py`）；通知模块可以复用这里的
`send()`，也可以换成本机任何发信方式。

为什么要单独一层
--------------------------------
之前每个脚本自己拼 `smtplib` / 调外部 `send_email.py`，账号密码散在配置文件里，
日志里还直接把完整邮箱打出来。本文件把三件事收成一份：

1. **配置只从外部读**（环境变量 > 配置文件），**仓库里绝不写真实地址和口令**
2. **日志一律脱敏**：`example123456@qq.com` → `exa***456@qq.com`，
   `someone@163.com` → `som***one@163.com`
3. 发送失败不抛异常（报警链路不能因为邮箱挂了把主流程带死），只返回 False

配置来源（按顺序取第一个可用的）
--------------------------------
① 环境变量（推荐，部署时注入，不落盘）
     MAIL_FROM / MAIL_PASS / MAIL_SMTP_HOST / MAIL_SMTP_PORT / MAIL_TO / MAIL_FROM_NAME
② 配置文件（默认 `~/.config/openclaw/mail.json`，可用 `MAIL_CONFIG` 指定）
     {"from_addr": "...", "from_pass": "...", "smtp_host": "smtp.163.com",
      "smtp_port": 465, "to": "...", "from_name": "..."}
③ 本机遗留配置（`~/.openclaw/workspace/scripts/email_config.json`，只读兼容，不推荐新用）

用法
--------------------------------
    import mailer
    mailer.send("标题", "# 正文 markdown", attach="/path/x.png")
    mailer.send("标题", body, to="a@b.com", dry_run=True)   # 只打印不真发
    mailer.mask("example123456@qq.com")    # → exa***456@qq.com
    mailer.whoami()                         # 打印当前配置（已脱敏），排障用

    python3 misc/core/mailer.py --check                 # 只看配置
    python3 misc/core/mailer.py --subject S --body "hi" # 直接发一封
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

# ── 网络：容器 IPv6 不通，统一强制 IPv4（库内硬规矩）──────────────────────
try:
    from misc.core.net_guard import install as _net_install
    _net_install()
except Exception:                                                # noqa: BLE001
    import socket as _socket
    _o = _socket.getaddrinfo
    _socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k)
                                           if r[0] == _socket.AF_INET]


# ═══════════════════════════ 脱敏 ═══════════════════════════
def mask(addr: str) -> str:
    """邮箱脱敏：保留首尾少量字符，中间用 *** 盖掉。

    example123456@qq.com → exa***456@qq.com
    someone@163.com      → som***one@163.com
    a@b.com            → a***@b.com
    不是邮箱的原样返回（比如 "—"）。
    """
    s = str(addr or "").strip()
    if "@" not in s:
        return s or "—"
    local, _, domain = s.partition("@")
    n = len(local)
    if n <= 2:
        head = local[:1]
        masked = head + "***"
    elif n <= 4:
        masked = local[:1] + "***" + local[-1:]
    else:
        masked = local[:3] + "***" + local[-3:]
    return "%s@%s" % (masked, domain)


def mask_many(addrs) -> str:
    """一串收件人 → 脱敏后用逗号连起来（日志/报告里只允许出现这个）"""
    if isinstance(addrs, str):
        addrs = [addrs]
    return ", ".join(mask(a) for a in addrs if str(a).strip())


# 明文邮箱/口令的兜底清洗：万一有异常信息把完整地址带出来，统一抹掉
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def scrub(text: str) -> str:
    """把任意文本里的完整邮箱替换成脱敏形式（异常/日志输出前过一遍）"""
    return _EMAIL_RX.sub(lambda m: mask(m.group(0)), str(text))


# ═══════════════════════════ 配置 ═══════════════════════════
KEYS = ("from_addr", "from_pass", "smtp_host", "smtp_port", "to", "from_name")
ENV_MAP = {
    "from_addr": ("MAIL_FROM", "MAIL_USER", "SMTP_USER"),
    "from_pass": ("MAIL_PASS", "MAIL_PASSWORD", "SMTP_PASS"),
    "smtp_host": ("MAIL_SMTP_HOST", "SMTP_HOST"),
    "smtp_port": ("MAIL_SMTP_PORT", "SMTP_PORT"),
    "to": ("MAIL_TO", "MAIL_TO_ADDR"),
    "from_name": ("MAIL_FROM_NAME", "SMTP_FROM_NAME"),
}


def _config_paths():
    """配置文件候选（环境变量优先，其次用户目录，最后本机遗留）"""
    out = []
    env = os.environ.get("MAIL_CONFIG")
    if env:
        out.append(os.path.expanduser(env))
    home = os.path.expanduser("~")
    out.append(os.path.join(home, ".config", "openclaw", "mail.json"))
    # 本机遗留（只读兼容）：~/.openclaw/workspace/scripts/email_config.json
    out.append(os.path.join(home, ".openclaw", "workspace", "scripts", "email_config.json"))
    return out


def load_config(extra=None) -> dict:
    """读配置：环境变量 > 配置文件 > 调用方传入的默认值。返回 dict（可能缺项）。"""
    cfg = {}
    for p in _config_paths():
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg.update(json.load(f) or {})
                break
            except Exception:                                    # noqa: BLE001
                continue
    for k, envs in ENV_MAP.items():
        for e in envs:
            v = os.environ.get(e)
            if v and str(v).strip():
                cfg[k] = v.strip()
                break
    # 2026-11-24：不再去 import 本机 `email_sender.py` 的 DEFAULT_CONFIG 兜底。
    #   公开库只认环境变量 / 配置文件；告警通道由调用方通过 misc/core/notify.py 注册。
    if extra:
        for k, v in extra.items():
            if v not in (None, ""):
                cfg[k] = v
    if "smtp_port" in cfg:
        try:
            cfg["smtp_port"] = int(cfg["smtp_port"])
        except Exception:                                        # noqa: BLE001
            cfg["smtp_port"] = 465
    cfg.setdefault("smtp_host", "smtp.163.com")
    cfg.setdefault("smtp_port", 465)
    cfg.setdefault("from_name", "技术分析报警")
    return cfg


def whoami(cfg=None, verbose=True):
    """打印当前配置（**全部脱敏**），排障用。返回 dict。"""
    cfg = cfg or load_config()
    info = {
        "smtp": "%s:%s" % (cfg.get("smtp_host"), cfg.get("smtp_port")),
        "from": mask(cfg.get("from_addr", "")),
        "from_name": cfg.get("from_name", ""),
        "to": mask_many(str(cfg.get("to", "")).replace(";", ",").split(",")),
        "pass_set": bool(cfg.get("from_pass")),
    }
    if verbose:
        print("[mailer] " + " ｜ ".join("%s=%s" % (k, v) for k, v in info.items()))
    return info


def configured(cfg=None) -> bool:
    """配置是否够发信（发件人 + 口令 + 收件人齐了）"""
    cfg = cfg or load_config()
    return all(cfg.get(k) for k in ("from_addr", "from_pass", "to"))


# ═══════════════════════════ 发送 ═══════════════════════════
def _htmlify(body: str) -> str:
    """优先用工作区那套 Markdown→HTML 渲染器（报告里都是表格）；没有就退化为纯文本。"""
    home = os.path.expanduser("~")
    cand = [
        os.environ.get("MAIL_RENDERER"),
        os.path.join(home, ".openclaw", "workspace", "scripts", "send_email.py"),
    ]
    for p in cand:
        if not p or not os.path.exists(p):
            continue
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_mail_renderer", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)               # 只借它的 md_to_html
            if hasattr(mod, "md_to_html"):
                return mod.md_to_html(body)
        except Exception:                                    # noqa: BLE001
            continue
    # 退化：最少保证换行不糊成一行
    esc = (body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return ('<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
            'font-size:14px;line-height:1.65;color:#1f2328;white-space:pre-wrap">%s</div>' % esc)


def send(subject, body, to=None, attach=None, html=True,
         dry_run=False, cfg=None, timeout=60):
    """发一封邮件。成功 True；失败 False（**不抛异常**）。

    Args:
        subject: 标题
        body:    Markdown 正文
        to:      收件人（字符串或列表）；None → 用配置里的默认收件人
        attach:  附件路径（str 或 list），图片按内联附件发
        html:    True → Markdown 渲染成 HTML（自动带纯文本备选）
        dry_run: 只打印（脱敏）不发信
    """
    cfg = cfg or load_config(extra=None)
    # 全局干跑开关（测试/排障用）：MAIL_DRY_RUN=1 时只打印不真发
    if os.environ.get("MAIL_DRY_RUN", "").lower() in ("1", "true", "yes"):
        dry_run = True
    if not cfg.get("from_addr") or not cfg.get("from_pass"):
        print("[mailer] ⚠️ 未配置发件账号，跳过发送（%s）" % scrub(subject))
        return False

    rcpts = to if to is not None else cfg.get("to")
    if isinstance(rcpts, str):
        rcpts = [x.strip() for x in rcpts.replace(";", ",").split(",") if x.strip()]
    rcpts = [x for x in (rcpts or []) if x]
    if not rcpts:
        print("[mailer] ⚠️ 没有收件人，跳过发送（%s）" % scrub(subject))
        return False

    if dry_run:
        print("[mailer] DRY-RUN 收件人=%s ｜ 标题=%s ｜ 附件=%s"
              % (mask_many(rcpts), scrub(subject),
                 mask_attach(attach)))
        return True

    msg = MIMEMultipart()
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(cfg.get("from_name", ""), "utf-8")),
                              cfg["from_addr"]))
    msg["To"] = ", ".join(rcpts)

    plain = MIMEText(body, "plain", "utf-8")
    if html:
        alt = MIMEMultipart("alternative")
        alt.attach(plain)
        alt.attach(MIMEText(_htmlify(body), "html", "utf-8"))
        msg.attach(alt)
    else:
        msg.attach(plain)

    files = attach if isinstance(attach, (list, tuple)) else ([attach] if attach else [])
    for f in files:
        if not f or not os.path.exists(f):
            continue
        try:
            with open(f, "rb") as fh:
                raw = fh.read()
            ext = os.path.splitext(f)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                part = MIMEImage(raw, _subtype=ext.lstrip(".").replace("jpg", "jpeg"))
            else:
                part = MIMEApplication(raw)
            part.add_header("Content-Disposition", "attachment",
                            filename=os.path.basename(f))
            msg.attach(part)
        except Exception as e:                                   # noqa: BLE001
            print("[mailer] ⚠️ 附件读取失败 %s: %s" % (os.path.basename(f), scrub(e)))

    host, port = cfg.get("smtp_host"), int(cfg.get("smtp_port", 465))
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout) as s:
                s.login(cfg["from_addr"], cfg["from_pass"])
                s.sendmail(cfg["from_addr"], rcpts, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(cfg["from_addr"], cfg["from_pass"])
                s.sendmail(cfg["from_addr"], rcpts, msg.as_string())
        print("[mailer] ✅ 已发送 → %s ｜ %s" % (mask_many(rcpts), scrub(subject)))
        return True
    except Exception as e:                                       # noqa: BLE001
        print("[mailer] ❌ 发送失败 → %s ｜ %s" % (mask_many(rcpts), scrub(e)[:200]))
        return False


def mask_attach(attach) -> str:
    """附件名脱敏（只留文件名，目录不进日志）"""
    if not attach:
        return "—"
    files = attach if isinstance(attach, (list, tuple)) else [attach]
    return ", ".join(os.path.basename(str(f)) for f in files if f) or "—"


# ═══════════════════════════ CLI ═══════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(description="统一邮件发送（地址全部脱敏）")
    ap.add_argument("--check", action="store_true", help="只打印当前配置（脱敏）")
    ap.add_argument("--subject", default=None)
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file", default=None)
    ap.add_argument("--to", default=None)
    ap.add_argument("--attach", action="append", default=None)
    ap.add_argument("--plain", action="store_true", help="不渲染 HTML")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.check or not a.subject:
        info = whoami()
        print("[mailer] 配置完整：%s" % ("是" if configured() else "否（缺发件账号或收件人）"))
        print("[mailer] 配置文件候选：")
        for p in _config_paths():
            print("   %s %s" % ("✅" if os.path.exists(p) else "  ", p))
        return 0 if info else 1

    body = a.body
    if a.body_file:
        with open(a.body_file, encoding="utf-8") as f:
            body = f.read()
    ok = send(a.subject, body, to=a.to, attach=a.attach,
              html=not a.plain, dry_run=a.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
