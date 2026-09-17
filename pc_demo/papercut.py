"""
剪纸风格化 v0.1 —— 剪影 -> 剪纸
================================
对应简报三效果之二（剪纸）的实现思路:

    1. 米白衬底 + 纸纤维噪纹（剪纸贴在背板上）
    2. 剪影填正红（传统剪纸色）
    3. 金色锯齿描边：粗边缘环带(金色) → 叠加规则三角波锯齿 → 剪进红色
    4. 内部镂空：剪影区域填左右镜像的对称几何花纹（对称性=剪纸的折叠剪法），
       按"镂空率"从图库中减运算，露出米白衬底

用法:
    from papercut import Papercutter
    cutter = Papercutter(h, w)      # 预生成纸纹/花纹库，只做一次
    img = cutter(mask)              # 每帧调用，mask 为 0/255 二值剪影

单独看效果:
    python src/papercut.py          # 合成人形演示 -> experiments/demo_papercut.png
"""

import time

import cv2
import numpy as np


class Papercutter:
    """剪影 -> 剪纸。构造一次，逐帧调用。"""

    # BGR 颜色约定（OpenCV 标准）
    RED = np.array([40, 40, 220], dtype=np.float32)      # 剪纸正红
    GOLD = np.array([55, 180, 225], dtype=np.float32)    # 描边金
    BG = np.array([250, 250, 245], dtype=np.float32)     # 米白衬底

    def __init__(self, h, w, *, seed=7,
                 border_ratio=0.018,  # 描边宽度占图短边比例
                 cutout=0.5,          # 镂空率 0~1：越大露出的衬底越多
                 frame_frac=0.1,      # 镂空花纹占剪影 bbox 的宽度比例
                 cavity_frac=0.08,    # 镂空留白占花纹盒的最小宽度
                 line_ratio=1.6):     # 镂空条纹的椭圆长宽比
        rng = np.random.RandomState(seed)
        self.h, self.w = h, w

        # 锯齿环带的固定宽度（像素）：比 border_width 大一些，锯齿才剪得动
        self.band_w = max(int(min(h, w) * border_ratio), 6)

        # ---- 预生成：米白纸纹 ----
        noise = rng.normal(0, 3.5, (h, w, 1))
        fiber = cv2.blur(rng.normal(0, 1, (h, w)).astype(np.float32),
                         (71, 3)) * 6.0
        self.paper = np.clip(self.BG + noise + fiber[..., None], 0, 255).astype(np.float32)

        # ---- 预生成：三档镂空花纹库 ----
        self.bank = [
            self._mirror_tile(frame_frac, cavity_frac, rng)
            for _ in range(3)
        ]
        self.cutout = float(cutout)

    # ---------- 内部工具 ----------

    def _mirror_tile(self, frame_frac, cavity_frac, rng):
        """生成一小块左右镜像的镂空花纹（对称=剪纸折叠剪法的味道）。

        返回 0/1 float 图：1 表示"要镂空"的区域。
        保证花纹盒内的镂空宽度 >= cavity_px，避免被剪碎。
        """
        cavity_px = max(int(self.band_w * 1.5), 4)
        box_h = max(int(self.h * frame_frac), cavity_px * 4)
        box_w = max(int(self.w * frame_frac), cavity_px * 6)

        tile = np.zeros((box_h, box_w), np.uint8)
        n = 0
        while tile.sum() == 0 and n < 50:   # 防空盒
            n += 1
            # 条纹：拉长方向的椭圆（椭圆比矩形"像剪出来的"）
            th = rng.randint(cavity_px, box_h // 3 + 1)
            tw = int(th * rng.uniform(1.2, 2.4))
            cx = rng.randint(0, box_w)
            cy = rng.randint(0, box_h)
            ang = rng.randint(0, 180)
            cv2.ellipse(tile, (cx, cy), (tw, th), int(ang), 0, 360, 1, -1)
            # 几何块：三角/菱形
            if rng.random_sample() < 0.5:
                pts = np.array([
                    [rng.randint(0, box_w), rng.randint(0, box_h)],
                    [rng.randint(0, box_w), rng.randint(0, box_h)],
                    [rng.randint(0, box_w), rng.randint(0, box_h)],
                ], np.int32)
                cv2.fillPoly(tile, [pts], 1)

        # 左右镜像 -> 对称
        tile = np.concatenate([tile, tile[:, ::-1]], axis=1)
        return tile.astype(np.float32)

    # ---------- 主流程 ----------

    def __call__(self, mask):
        """mask: 二值剪影图（0/255），返回 BGR 剪纸图。"""
        if mask.ndim == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        m = (mask > 127).astype(np.uint8)

        # 1) 红色剪影主体
        red = m.astype(np.float32) * 255.0

        # 2) 金色锯齿描边
        band_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (self.band_w, self.band_w))
        band = cv2.dilate(m, band_kernel) - m          # 外扩环带
        # 规则三角波锯齿：沿水平扫描，波峰处保留金色，波谷处剪断
        xs = np.arange(self.w, dtype=np.float32)
        saw = np.abs((xs * (2 * np.pi / self.band_w)) % (2 * np.pi) - np.pi) / np.pi
        saw = cv2.resize(saw[None, :], (self.w, self.h))
        band = band.astype(np.float32) * (saw > 0.35)  # 0.35 控制锯齿占空比
        gold = np.clip(band, 0, 1)

        # 3) 内部镂空：从花纹库选一档贴到剪影 bbox，按镂空率减运算
        cut = self._carve(m)

        # 4) 合成（BGR 三通道）
        # 底 = 纸纹
        out = self.paper.copy()
        # 红区 = 正红
        out[red > 0] = self.RED
        # 金边 = 金色（叠在红区边缘）
        out[gold > 0] = self.GOLD
        # 镂空 = 露出米白衬底（剪掉的孔）
        out[cut > 0] = self.BG

        return np.clip(out, 0, 255).astype(np.uint8)

    def _carve(self, m):
        """在剪影内部贴对称镂空花纹。返回 0/1 镂空 mask。"""
        ys, xs = np.where(m > 0)
        if len(ys) == 0:
            return np.zeros_like(m, np.float32)

        y1, y2, x1, x2 = ys.min(), ys.max(), xs.min(), xs.max()
        bw, bh = x2 - x1 + 1, y2 - y1 + 1

        # 花纹盒不能比剪影大
        bw = min(bw, self.w - 4)
        bh = min(bh, self.h - 4)
        if bw < 8 or bh < 8:
            return np.zeros_like(m, np.float32)

        tile = self.bank[np.random.randint(0, len(self.bank))]
        tile = cv2.resize(tile, (bw, bh), interpolation=cv2.INTER_NEAREST)

        cut = np.zeros_like(m, np.float32)
        cut[y1:y1 + bh, x1:x1 + bw] = tile
        cut = cut * m  # 镂空不能超出剪影
        # 镂空率控制：低于 cutout 时按连通域整片取舍，避免碎屑
        n, labels = cv2.connectedComponents(cut.astype(np.uint8))
        if n > 1:
            areas = np.bincount(labels.ravel())[1:]
            keep = np.zeros_like(cut)
            for lbl in range(1, n):
                if areas[lbl - 1] >= self.cutout * 100:  # 面积阈值
                    keep[labels == lbl] = 1
            cut = keep
        return cut


def make_person_mask(h=520, w=640):
    """合成一个'人形'剪影，供无摄像头时演示/测试用。"""
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (320, 120), 50, 255, -1)                       # 头
    cv2.ellipse(m, (320, 300), (105, 125), 0, 0, 360, 255, -1)   # 躯干
    cv2.line(m, (230, 220), (160, 440), 255, 42)                 # 左臂
    cv2.line(m, (410, 220), (480, 440), 255, 42)                 # 右臂
    cv2.line(m, (295, 410), (272, 505), 255, 44)                 # 左腿
    cv2.line(m, (345, 410), (368, 505), 255, 44)                 # 右腿
    return m


if __name__ == "__main__":
    # 演示：python src/papercut.py（在项目根目录运行）
    import os

    h, w = 520, 640
    mask = make_person_mask(h, w)
    cutter = Papercutter(h, w)
    out = cutter(mask)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "experiments")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "demo_papercut.png")
    cv2.imwrite(path, out)
    print(f"演示图已存: {path}")

    # 性能粗测
    t0 = time.perf_counter()
    for _ in range(30):
        cutter(mask)
    ms = (time.perf_counter() - t0) / 30 * 1000
    print(f"剪纸单帧耗时 ≈ {ms:.2f} ms  (尺寸 {w}x{h})")

    cv2.imshow("papercut demo (按任意键关闭)", out)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
