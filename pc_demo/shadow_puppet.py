"""
皮影风格化 v0.1 —— 剪影 -> 皮影戏人偶
======================================
对应简报三效果之三（皮影）的实现思路:

    1. 关节分割（几何启发，不用 ML）:
       - 头部 = bbox 顶部 18% 高度带（人形站立时头必在最高处）
       - 躯干 = bbox 中部区域（18%~55% 高度 ∩ 中线 ±35% 宽度）
       - 四肢 = 剩余像素按方位归类: 中线分左右、55% 高度分上下
                -> 左臂/右臂/左腿/右腿
    2. 经典皮影五色: 头黄(肤色)、躯干红、四肢绿、描金、关节黑钉
    3. 描金: 外轮廓 + 各关节交界处画金色环带（皮影"分片缝制"的味道）
    4. 微透光: 按距离变换让边缘略亮（光从驴皮影背面透过来）
       + 整体叠牛皮纸纹

局限（先讲清楚）: 几何法假设"站立人形"。蹲姿/翻滚会错分关节——
演示场景（站立摆造型）够用。真要加强得上姿态估计，ARM 上跑不动。

用法:
    from shadow_puppet import ShadowPuppeter
    pup = ShadowPuppeter(h, w)
    img = pup(mask)

单独看效果:
    python src/shadow_puppet.py   # 合成人形演示 -> experiments/demo_shadow_puppet.png
"""

import time

import cv2
import numpy as np


class ShadowPuppeter:
    """剪影 -> 皮影。构造一次，逐帧调用。"""

    # BGR 皮影五色
    SKIN = np.array([140, 210, 235], np.float32)   # 头部肤色偏黄
    BODY_RED = np.array([50, 50, 190], np.float32)  # 躯干红
    LIMB_GREEN = np.array([70, 150, 70], np.float32)  # 四肢绿
    GOLD = np.array([50, 185, 230], np.float32)       # 描金
    PIN = np.array([30, 30, 30], np.float32)          # 关节黑钉
    BG = np.array([215, 235, 245], np.float32)        # 幕布暖白

    def __init__(self, h, w, *, seed=11,
                 head_frac=0.18,     # 头部带高度占 bbox 比例
                 waist_frac=0.55,   # 上下身分界线（占 bbox 高度）
                 torso_half=0.35,   # 躯干半宽占 bbox 宽度比例
                 edge_width=3,      # 描金宽度
                 glow=0.35):        # 透光强度 0~1
        rng = np.random.RandomState(seed)
        self.h, self.w = h, w
        self.head_frac = head_frac
        self.waist_frac = waist_frac
        self.torso_half = torso_half
        self.glow = glow

        # 幕布纸纹（暖白 + 细噪 + 竖向幕布褶皱）
        noise = rng.normal(0, 4, (h, w, 1))
        folds = cv2.blur(rng.normal(0, 1, (h, w)).astype(np.float32),
                         (3, 91)) * 7.0
        self.curtain = np.clip(self.BG + noise + folds[..., None], 0, 255)

        self.edge_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (edge_width * 2 + 1, edge_width * 2 + 1))

    # ---------- 关节分割（核心难点）----------

    def _segment_joints(self, m):
        """把剪影分成 头/躯干/左臂/右臂/左腿/右腿 六个区域。

        返回 dict(name -> 0/1 float mask)。找不到人形时全归躯干兜底。
        """
        ys, xs = np.where(m > 0)
        H, W = m.shape
        parts = {k: np.zeros((H, W), np.float32)
                 for k in ("head", "torso", "l_arm", "r_arm", "l_leg", "r_leg")}
        if len(ys) < 200:   # 剪影太小，分割没意义
            parts["torso"][:] = m
            return parts

        y1, y2, x1, x2 = ys.min(), ys.max(), xs.min(), xs.max()
        bh, bw = y2 - y1 + 1, x2 - x1 + 1
        cx = (x1 + x2) // 2                     # 身体中线
        head_y = y1 + int(bh * self.head_frac)  # 头/躯干分界
        waist_y = y1 + int(bh * self.waist_frac)  # 上下身分界

        Y, X = np.mgrid[0:H, 0:W]
        is_fg = m > 0

        # 头：bbox 顶部带
        parts["head"] = (is_fg & (Y <= head_y)).astype(np.float32)

        # 躯干：中部高度 ∩ 中线附近（先圈躯干，剩下的四肢才好分）
        torso = (is_fg & (Y > head_y) & (Y < waist_y)
                 & (np.abs(X - cx) <= bw * self.torso_half))
        parts["torso"] = torso.astype(np.float32)

        # 四肢：剩余前景，按方位四象限
        rest = is_fg & (Y > head_y) & (parts["torso"] == 0)
        upper = rest & (Y < waist_y)
        lower = rest & (Y >= waist_y)
        parts["l_arm"] = (upper & (X < cx)).astype(np.float32)
        parts["r_arm"] = (upper & (X >= cx)).astype(np.float32)
        parts["l_leg"] = (lower & (X < cx)).astype(np.float32)
        parts["r_leg"] = (lower & (X >= cx)).astype(np.float32)
        return parts

    # ---------- 主流程 ----------

    def __call__(self, mask):
        """mask: 二值剪影 (0/255)，返回 BGR 皮影图。"""
        if mask.ndim == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        m = (mask > 127).astype(np.uint8)

        parts = self._segment_joints(m)

        # 透光系数：边缘略亮（光从皮影背面透过来）
        dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
        dmax = dist.max()
        bright = (0.75 + self.glow * (1.0 - dist / dmax)) if dmax > 0 \
            else np.ones_like(dist)
        bright = bright[..., None]   # 广播到 3 通道

        # 幕布底
        out = self.curtain.copy()

        # 分件染色（每件都乘透光系数）
        out[parts["l_arm"] > 0] = self.LIMB_GREEN
        out[parts["r_arm"] > 0] = self.LIMB_GREEN
        out[parts["l_leg"] > 0] = self.LIMB_GREEN
        out[parts["r_leg"] > 0] = self.LIMB_GREEN
        out[parts["torso"] > 0] = self.BODY_RED
        out[parts["head"] > 0] = self.SKIN
        # 透光调制（只对剪影区域生效）
        fg = (m > 0)[..., None]
        out = np.where(fg, out * bright, out)

        # 描金：外轮廓 + 各件交界处（"分片缝制"的接缝）
        outline = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, self.edge_kernel)
        seams = np.zeros_like(m)
        for name in ("head", "torso", "l_arm", "r_arm", "l_leg", "r_leg"):
            p = parts[name].astype(np.uint8)
            seams |= cv2.morphologyEx(p, cv2.MORPH_GRADIENT, self.edge_kernel)
        seam_band = ((outline > 0) | (seams > 0)).astype(np.uint8)
        seam_band = cv2.dilate(seam_band, self.edge_kernel) & m
        out[seam_band > 0] = self.GOLD

        # 关节黑钉：各件质心处钉一颗小黑点（皮影的铆钉关节）
        for name in ("l_arm", "r_arm"):
            p = parts[name]
            if p.sum() > 300:
                ys, xs = np.where(p > 0)
                # 钉子钉在肢体靠近躯干的一端（取离中线最近的点）
                joint = np.argmin(np.abs(xs - self.w // 2))
                cv2.circle(out, (int(xs[joint]), int(ys[joint])),
                           5, tuple(self.PIN.tolist()), -1)

        return np.clip(out, 0, 255).astype(np.uint8)


def make_person_mask(h=520, w=640):
    """合成人形剪影（与 ink_wash/papercut 一致）。"""
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (320, 120), 50, 255, -1)
    cv2.ellipse(m, (320, 300), (105, 125), 0, 0, 360, 255, -1)
    cv2.line(m, (230, 220), (160, 440), 255, 42)
    cv2.line(m, (410, 220), (480, 440), 255, 42)
    cv2.line(m, (295, 410), (272, 505), 255, 44)
    cv2.line(m, (345, 410), (368, 505), 255, 44)
    return m


if __name__ == "__main__":
    import os

    h, w = 520, 640
    mask = make_person_mask(h, w)
    pup = ShadowPuppeter(h, w)
    out = pup(mask)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "experiments")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "demo_shadow_puppet.png")
    cv2.imwrite(path, out)
    print(f"演示图已存: {path}")

    t0 = time.perf_counter()
    for _ in range(30):
        pup(mask)
    ms = (time.perf_counter() - t0) / 30 * 1000
    print(f"皮影单帧耗时 ≈ {ms:.2f} ms  (尺寸 {w}x{h})")

    cv2.imshow("shadow puppet demo (按任意键关闭)", out)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
