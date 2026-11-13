#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""terrain.py · 黎曼流形地形图（Riemannian Manifold Terrain）
==========================================================
把「成分股相关结构」画成一张地形图，用来看**哪里堵、哪里通**：

  左图（艺术化地形）—— 从真实数据渲染，但不是 K 线：
    · 弯曲曲面      = SPD 流形在 2D 主成分平面上的「局部一致性」高程场
    · 🔴 红色裂焰区 = 局部一致性极高 + 曲率为正 → 拓扑坍缩 / 相关性锁死
    · 🔵 蓝色回流区 = 一致性低 → 独立方向阵营 / 拓扑再生（机会）
    · 🟡 黄色胶着带 = 一致性居中且梯度陡 → 喉咙状瓶颈 / 结构卡滞
    · 空中持续同调条码 + Persistence 点图（Vietoris-Rips H1，真实算）
  右图（数据驱动）—— 真实 SPD 流形：
    · 按第一主成分重排的相关矩阵曲面（对角脊线金色 / 正相关红峰 / 负相关蓝谷）
    · 特征值谱浮球 + H1 发光线圈

⚠️ 全部数值来自 data_loader 取的真实日线（走 dh_bridge → data_hub），
   纯 numpy / scipy / ripser / matplotlib 计算，**零 LLM、不编数据**。

用法：
  python3 topology/terrain.py --target 沪深300
  python3 topology/terrain.py --target 沪深300 --days 90 --out reports/topology
  python3 topology/terrain.py --target 中证500 --dpi 150
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import cm
from matplotlib.colors import LinearSegmentedColormap, LightSource, Normalize
from matplotlib.patches import Patch

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from topology import targets, data_loader, spd, plot  # noqa: E402
else:
    from . import targets, data_loader, spd, plot  # noqa: E402

# ───────────────────────── 配色（深空蓝黑 · 无文字版） ─────────────────────────
BG = "#03060d"
BG2 = "#060b16"
RIFT = "#ff2d55"        # 红色裂焰
RIFT_HOT = "#ff8fa3"
FLOW = "#2ee6ff"        # 蓝色回流
FLOW_DIM = "#1b6b8f"
CHOKE = "#ffc233"       # 黄色胶着
RIDGE = "#f5c451"       # 金色脊线
VALLEY = "#123a63"

plt.rcParams.update({
    "figure.facecolor": BG,
    "savefig.facecolor": BG,
    "axes.facecolor": BG2,
    "text.color": "#dbe6f5",
})


# ───────────────────────── 高程场：局部一致性 ─────────────────────────
def _local_coherence(pts, corr, grid=90, sigma=0.13):
    """2D 嵌入点云 + 平均两两相关 → 网格「局部一致性」高程场。

    pts : (n,2) 成分股 2D 嵌入（已标准化）
    corr: (n,n) 相关矩阵（对称，对角=1）
    """
    from scipy.ndimage import gaussian_filter

    g = np.linspace(-3.0, 3.0, grid)
    GX, GY = np.meshgrid(g, g)
    pos = np.clip(((pts + 3.0) / 6.0 * (grid - 1)).astype(int), 0, grid - 1)

    # 每只股票把自己与「本地邻居」的平均相关性打到网格上
    acc = np.zeros((grid, grid))
    wgt = np.zeros((grid, grid))
    C = corr.copy()
    np.fill_diagonal(C, np.nan)
    local = np.nanmean(C, axis=1)                 # 该股与全体平均相关
    local = np.nan_to_num(local, nan=0.0)
    for i in range(len(pts)):
        acc[pos[i, 1], pos[i, 0]] += local[i]
        wgt[pos[i, 1], pos[i, 0]] += 1.0

    # 稀疏网格 → 高斯核平滑，得到连续地形
    num = gaussian_filter(acc, sigma * grid)
    den = gaussian_filter(wgt, sigma * grid)
    Z = np.where(den > 1e-9, num / np.maximum(den, 1e-9), np.nan)
    Z = _fill_nan(Z)
    Z = gaussian_filter(Z, sigma * grid * 0.55)
    # 归一化到 0~1（1=最锁死）
    lo, hi = np.percentile(Z, 2), np.percentile(Z, 98)
    Z = np.clip((Z - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    return GX, GY, Z


def _fill_nan(Z):
    """用最近有效值填补 NaN（迭代扩散，够用且不引入依赖）。"""
    Z = Z.copy()
    mask = np.isnan(Z)
    if not mask.any():
        return Z
    Z[mask] = np.nanmean(Z)
    for _ in range(6):
        from scipy.ndimage import uniform_filter
        sm = uniform_filter(np.where(mask, 0.0, Z), size=5)
        cnt = uniform_filter((~mask).astype(float), size=5)
        fill = np.where(cnt > 1e-6, sm / np.maximum(cnt, 1e-6), Z)
        Z = np.where(mask, fill, Z)
        mask = np.isnan(Z)
        if not mask.any():
            break
    return Z


def _curvature(Z):
    """离散平均曲率近似（高程场二阶导），正 = 凸起/脊，负 = 凹/谷。"""
    gy, gx = np.gradient(Z)
    gyy, _ = np.gradient(gy)
    _, gxx = np.gradient(gx)
    return gxx + gyy


# ───────────────────────── 左图：艺术化地形 ─────────────────────────
def _blend(base, color, mask, alpha):
    """把 color 按 mask（可为软掩码 0~1）叠到 base RGB 上。base/返回 (H,W,4)。"""
    m = np.clip(mask, 0.0, 1.0)[..., None] * alpha
    c = np.array(matplotlib.colors.to_rgba(color))[:3]
    out = base.copy()
    out[..., :3] = out[..., :3] * (1 - m) + c * m
    return out


def _soft(mask_hi, mask_lo):
    """硬阈值 → 软过渡（用高程分位做 0~1 权重）。"""
    from scipy.ndimage import gaussian_filter
    m = np.asarray(mask_hi, dtype=float)
    m = gaussian_filter(m, 2.0)
    m = np.clip((m - 0.35) / 0.45, 0.0, 1.0)
    return m * np.asarray(mask_lo, dtype=float)


def _rank01(x):
    """秩次归一化（0~1，均匀分布）——避免量级差异（K 只有 1e-3）把权重吃掉。"""
    x = np.asarray(x, dtype=float)
    flat = x.ravel()
    order = np.argsort(np.argsort(flat))
    r = order.astype(float) / max(len(flat) - 1, 1)
    return r.reshape(x.shape)


def _draw_artistic(ax, GX, GY, Z, res, rips_mod):
    ls = LightSource(azdeg=305, altdeg=34)
    span = GX.max() - GX.min()

    # ── 掩码先算好：红=高一致性+正曲率，蓝=低一致性，黄=中一致性+陡梯度 ──
    K = _curvature(Z)
    grad = np.hypot(*np.gradient(Z))

    zr = _rank01(Z)          # 高程秩次
    kr = _rank01(K)          # 曲率秩次
    gr = _rank01(grad)       # 梯度秩次

    # 红：高程前 ~40% 且曲率偏正 → 撕裂；两者相乘再用残差加亮
    z_hi = np.clip((zr - 0.58) / 0.34, 0, 1)
    k_pos = np.clip((kr - 0.40) / 0.55, 0, 1)
    rift_m = np.clip(z_hi * (0.55 + 0.45 * k_pos) + 0.35 * z_hi * k_pos, 0, 1)

    # 蓝：高程后 22% → 谷底/分化区
    flow_m = np.clip((0.24 - zr) / 0.22, 0, 1)

    # 黄：中高程 + 陡梯度 → 喉胡瓶颈
    z_mid = np.exp(-((zr - 0.52) / 0.20) ** 2)
    choke_m = np.clip(z_mid * np.clip((gr - 0.45) / 0.45, 0, 1) * 1.9, 0, 1)

    # ── 主曲面：暗色山脉 + 碗状山谷，再按掩码叠三色（单一 surface，保证一定显示）──
    cmap = LinearSegmentedColormap.from_list(
        "manifold", ["#050d1a", "#0a1c30", "#0f2c45", "#154a63", "#1e6f78"], N=256)
    rgb = ls.shade(Z, cmap=cmap, vert_exag=1.4, blend_mode="soft", fraction=1.05)
    rgb = _blend(rgb, FLOW_DIM, flow_m, 0.35)
    rgb = _blend(rgb, FLOW, flow_m ** 1.5, 0.50)
    rgb = _blend(rgb, CHOKE, choke_m, 0.70)
    rgb = _blend(rgb, RIFT, rift_m, 0.92)
    rgb = _blend(rgb, RIFT_HOT, np.clip(rift_m - 0.55, 0, 1) / 0.45, 0.95)

    ax.plot_surface(GX, GY, Z, facecolors=rgb, rstride=1, cstride=1,
                    linewidth=0, antialiased=True, shade=False, alpha=1.0)

    # 金色脊线（高一致性山脊上的细描边）
    hi = Z > np.percentile(Z, 90)
    ax.plot_surface(GX, GY, np.where(hi, Z + 0.004, np.nan), color=RIDGE,
                    linewidth=0, antialiased=True, alpha=0.22, shade=False)

    # 爆亮奇点（红色焰心里最亮的那几处）
    ys, xs = np.where(rift_m > 0.72)
    if len(ys):
        pick = np.linspace(0, len(ys) - 1, min(18, len(ys))).astype(int)
        ax.scatter(GX[ys[pick], xs[pick]], GY[ys[pick], xs[pick]],
                   Z[ys[pick], xs[pick]] + 0.055, s=52, c=RIFT_HOT,
                   alpha=0.95, edgecolors="none", depthshade=False)

    # 光脉：沿高程梯度上升的若干条流线（从谷底出发）
    gy, gx = np.gradient(Z)
    n = Z.shape[0]
    starts = [(np.random.RandomState(7 + k).randint(6, n - 6),
               np.random.RandomState(11 + k).randint(6, n - 6)) for k in range(26)]
    ok = 0
    for (iy, ix) in starts:
        if Z[iy, ix] > np.percentile(Z, 38):
            continue
        path = [(iy, ix)]
        for _ in range(46):
            iy2 = int(np.clip(path[-1][0] + gy[path[-1]] / (np.abs(gy).max() + 1e-9) * 2.0, 1, n - 2))
            ix2 = int(np.clip(path[-1][1] + gx[path[-1]] / (np.abs(gx).max() + 1e-9) * 2.0, 1, n - 2))
            if (iy2, ix2) == path[-1]:
                break
            path.append((iy2, ix2))
        if len(path) < 8:
            continue
        path = np.array(path)
        z = Z[path[:, 0], path[:, 1]] + 0.02
        ax.plot(GX[path[:, 0], path[:, 1]], GY[path[:, 0], path[:, 1]], z,
                color=FLOW, lw=1.5, alpha=0.55)
        ax.plot(GX[path[:, 0], path[:, 1]], GY[path[:, 0], path[:, 1]], z,
                color="#bff6ff", lw=0.5, alpha=0.35)
        ok += 1

    # 胶着带的黏稠颗粒（黄色瓶颈处的小光簇）
    ys, xs = np.where(choke_m > 0.55)
    if len(ys):
        pick = np.linspace(0, len(ys) - 1, min(46, len(ys))).astype(int)
        ax.scatter(GX[ys[pick], xs[pick]], GY[ys[pick], xs[pick]],
                   Z[ys[pick], xs[pick]] + 0.028, s=10, c=CHOKE,
                   alpha=0.75, edgecolors="none", depthshade=False)

    # 空中：持续同调条码 + Persistence 点图（真实 H1）
    _draw_sky(ax, GX, Z, res, rips_mod)

    ax.set_axis_off()
    ax.view_init(elev=30, azim=-58)
    ax.set_xlim(GX.min(), GX.max())
    ax.set_ylim(GY.min(), GY.max())
    ax.set_zlim(-0.02, Z.max() * 1.62)
    ax.set_box_aspect((span, span, span * 0.42))
    return ok


def _draw_sky(ax, GX, Z, res, rips_mod):
    """在空中画持续同调条码（半透明）+ Persistence 点。"""
    try:
        pts = spd.stock_2d_embedding(res["ret"].values)
        pts = spd.subsample(pts, 180)
        dgms = rips_mod.Rips(maxdim=1, verbose=False).fit_transform(pts)
        h1 = dgms[1] if len(dgms) > 1 else np.empty((0, 2))
    except Exception:
        h1 = np.empty((0, 2))

    x0, x1 = GX.min(), GX.max()
    zt = Z.max() * 1.30
    if len(h1):
        life = (h1[:, 1] - h1[:, 0])
        life = life[np.isfinite(life)]
        top = np.sort(life)[::-1][:26]
        tmax = top.max() if len(top) else 1.0
        for k, lf in enumerate(top):
            xa = x0 + (k / max(len(top), 1)) * (x1 - x0) * 0.72
            ln = (lf / tmax) * (x1 - x0) * 0.16 + 0.02
            ax.plot([xa, xa + ln], [x1 * 0.62] * 2, [zt + k * 0.004] * 2,
                    color="#7ff0ff", lw=2.0, alpha=0.16 + 0.5 * (lf / tmax))
        # Persistence 点图
        b = h1[:, 0]
        d = h1[:, 1]
        keep = np.isfinite(b) & np.isfinite(d)
        b, d = b[keep], d[keep]
        if len(b):
            idx = np.argsort(d - b)[::-1][:60]
            ax.scatter(x0 + (d[idx] - b[idx]) / (tmax + 1e-9) * (x1 - x0) * 0.30,
                       np.full(len(idx), x1 * 0.30),
                       zt + 0.03 + (b[idx] / (np.abs(b).max() + 1e-9)) * 0.10,
                       s=8, c="#2ee6ff", alpha=0.55, edgecolors="none", depthshade=False)


# ───────────────────────── 右图：真实 SPD 流形 ─────────────────────────
def _draw_spd(ax, res):
    """按第一主成分重排的相关矩阵 → 3D 曲面 + 特征值浮球。"""
    R = res["ret"].values
    Z = (R - R.mean(0)) / (R.std(0, ddof=1) + 1e-12)
    n = Z.shape[1]
    C = (Z.T @ Z) / max(Z.shape[0] - 1, 1)
    # 按第一主成分载荷排序，让相关结构连续化
    _, _, Vt = np.linalg.svd(Z - Z.mean(0), full_matrices=False)
    order = np.argsort(-Vt[0])
    Cs = C[np.ix_(order, order)]
    Cs = np.nan_to_num(Cs, nan=0.0, posinf=0.0, neginf=0.0)

    # 平滑一下，画出曲面质感（仍是同一份真实矩阵）
    from scipy.ndimage import gaussian_filter
    S = gaussian_filter(Cs, sigma=max(0.4, n / 60.0))
    g = np.arange(S.shape[0])
    GX, GY = np.meshgrid(g, g)

    ls = LightSource(azdeg=300, altdeg=45)
    cmap = LinearSegmentedColormap.from_list(
        "corr", ["#0a2547", "#14496e", "#1e8fa0", "#2dd4bf", "#f5c451", "#ff5c7a"], N=256)
    lo = float(np.percentile(S, 3))
    hi = float(np.percentile(S, 99.5))
    norm = Normalize(lo, max(hi, lo + 1e-6))
    rgb = ls.shade(S, cmap=cmap, norm=norm, vert_exag=1.1, blend_mode="soft", fraction=1.0)
    ax.plot_surface(GX, GY, S, facecolors=rgb, rstride=1, cstride=1,
                    linewidth=0, antialiased=True, shade=False, alpha=0.95)

    # 对角脊线（金色）
    ax.plot(g, g, np.full_like(g, 1.0), color=RIDGE, lw=1.4, alpha=0.85)

    # 特征值谱浮球（SPD 特征值 → 空中发光球）
    eig = np.sort(res["eig_ref"])[::-1][:10]
    eig = np.clip(eig, 1e-12, None)
    emax = eig.max()
    for k, ev in enumerate(eig):
        xa = (k + 0.5) / 10 * (n - 1)
        ax.scatter([xa], [n * 0.5], [1.15 + 0.75 * (ev / emax)],
                   s=28 + 190 * (ev / emax), c="#ffd166", alpha=0.65,
                   edgecolors="none", depthshade=True)

    # H1 发光线圈（呼应左图拓扑符号）
    t = np.linspace(0, 2 * np.pi, 220)
    cx, cy = n / 2.0, n / 2.0
    rr = n * 0.42
    h1n = res["h1n"][-1] if len(res.get("h1n", [])) else 0.2
    for scale, alpha in [(1.0, 0.10), (0.94, 0.22), (0.88, 0.62)]:
        ax.plot(cx + rr * scale * np.cos(t), cy + rr * scale * np.sin(t),
                np.full_like(t, 1.72),
                color="#7ff0ff", lw=2.2, alpha=alpha * (0.25 + 0.75 * h1n))

    ax.set_axis_off()
    ax.view_init(elev=44, azim=-64)
    ax.set_xlim(0, n - 1)
    ax.set_ylim(0, n - 1)
    ax.set_zlim(-1.0, 2.0)
    ax.set_box_aspect((1, 1, 0.62))


# ───────────────────────── 主流程 ─────────────────────────
def _safe(name):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))


def build(target, days=60, out_dir="reports/topology", dpi=150):
    from ripser import Rips

    codes = data_loader.components(target)
    if not codes:
        raise SystemExit("[terrain] 取不到成分股：%s" % target.name)
    print("[terrain] %s 成分股 %d 只" % (target.name, len(codes)), flush=True)

    dates, close = data_loader.close_panel(codes, days)
    if close is None:
        raise SystemExit("[terrain] 成分股日线面板取数失败")
    idx_close = data_loader.index_close(target.code, days) if target.kind == "index" else None

    res = spd.compute(close, idx_close, days, verbose=False)
    if res is None:
        raise SystemExit("[terrain] 有效样本不足（<16 交易日或 <5 成分股）")
    print("[terrain] g=%.3f  H1=%.3f  corr=%.3f  熵=%.3f  Hurst=%.3f"
          % (res["g"][-1], res["h1"][-1], res["corr"][-1],
             res["ent"][-1], res["hurst_full"]), flush=True)

    # 高程场：SPD 切空间嵌入 + 真实相关矩阵
    pts = spd.stock_2d_embedding(res["ret"].values)
    Zc = (res["ret"].values - res["ret"].values.mean(0)) / (res["ret"].values.std(0, ddof=1) + 1e-12)
    corr = np.nan_to_num(np.corrcoef(Zc.T))
    GX, GY, Z = _local_coherence(pts, corr, grid=110)

    fig = plt.figure(figsize=(17.5, 9.4), dpi=dpi)
    ax1 = fig.add_subplot(1, 2, 1, projection="3d")
    ax2 = fig.add_subplot(1, 2, 2, projection="3d")
    ax1.set_position([0.005, 0.02, 0.50, 0.96])
    ax2.set_position([0.500, 0.02, 0.495, 0.96])

    _draw_artistic(ax1, GX, GY, Z, res, Rips)
    _draw_spd(ax2, res)

    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, "黎曼流形地形图_%s.png" % _safe(target.name or target.raw))
    fig.savefig(png, dpi=dpi, facecolor=BG, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    print("[terrain] [saved] %s" % png, flush=True)
    return png


def main(argv=None):
    ap = argparse.ArgumentParser(description="黎曼流形地形图（SPD 流形 + TDA + 里奇曲率）")
    ap.add_argument("--target", required=True, help="标的：沪深300 / 指数:中证500 / 行业:电子 / 概念:人工智能")
    ap.add_argument("--days", type=int, default=60, help="收益窗口交易日数（默认 60）")
    ap.add_argument("--out", default="reports/topology", help="输出目录")
    ap.add_argument("--dpi", type=int, default=150, help="出图 dpi（默认 150，16:9）")
    a = ap.parse_args(argv)

    bi = data_loader.board_list("行业") or []
    bc = data_loader.board_list("概念") or []
    t = targets.resolve(a.target, bi, bc)
    if t.kind == "unknown":
        print("[terrain] 无法识别的标的：%s" % a.target, file=sys.stderr)
        return 2
    if t.kind == "stock":
        print("[terrain] SPD 流形不支持单只个股，请给指数/行业/概念", file=sys.stderr)
        return 2
    build(t, days=a.days, out_dir=a.out, dpi=a.dpi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
