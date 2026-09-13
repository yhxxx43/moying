"""
水墨风格化 v0.1 —— 剪影 -> 水墨画
================================
对应简报三效果之一（水墨）的实现思路:

    1. 内部浓淡渐变 —— 距离变换: 剪影内离边缘越远墨越浓（墨往中心聚），
       gamma>1 让靠边的地方淡下来，形成"渗开"的层次
    2. 晕染 —— 对浓度图做高斯模糊，墨会轻微溢出轮廓，像在宣纸上洇开
    3. 边缘笔锋 —— 形态学梯度取边缘环带并加粗，模拟毛笔勾勒
    4. 抖动 —— 预生成的随机位移场 remap 边缘，模拟手绘颤笔
       （位移场固定不随帧变：抖动像"笔触纹理"，每帧都变反而像噪点闪烁）
    5. 飞白 —— 边缘墨量乘横向拉丝噪声，枯笔扫出的白道
    6. 合成 —— 按"总墨量 alpha"把墨色半透明叠到程序化宣纸底纹上

用法:
    from ink_wash import InkWasher
    washer = InkWasher(h, w)        # 预生成纸纹/噪声场，只做一次
    ink_img = washer(mask)          # 每帧调用，mask 为 0/255 二值剪影

单独看效果:
    python src/ink_wash.py          # 合成人形演示 -> experiments/demo_ink_wash.png
"""

import time

import cv2
import numpy as np


class InkWasher:
    """剪影 -> 水墨。构造一次，逐帧调用。"""

    def __init__(self, h, w, *, seed=42,
                 paper_base=235,     # 宣纸底色（米白）
                 ink_color=38,       # 浓墨灰度值
                 body_gamma=1.3,     # 内部渐变 gamma：>1 墨向中心聚、边缘留白
                 bleed=7,            # 晕染模糊核（自动取奇数）
                 edge_width=5,       # 边缘笔锋粗细（形态学核大小）
                 jitter=2.0,         # 边缘抖动幅度（像素）
                 feibai=0.55,        # 飞白强度 0~1，越大白道越明显
                 edge_strength=0.95):  # 边缘带最大墨量
        rng = np.random.RandomState(seed)

        # ---- 预生成（只做一次，逐帧复用）----
        # 宣纸底纹 = 米白底 + 细噪 + 横向长纤维
        noise = rng.normal(0, 5, (h, w)).astype(np.float32)
        fiber = cv2.blur(rng.normal(0, 1, (h, w)).astype(np.float32),
                         (81, 3)) * 10.0
        self.paper = np.clip(paper_base + noise + fiber, 0, 255)

        # 抖动位移场（固定随机场，整场演出用同一套"手感"）
        xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                             np.arange(h, dtype=np.float32))
        self.mapx = xs + rng.uniform(-jitter, jitter, (h, w)).astype(np.float32)
        self.mapy = ys + rng.uniform(-jitter, jitter, (h, w)).astype(np.float32)

        # 飞白拉丝噪声（横向拉长的亮暗条纹，归一化到 0~1）
        streak = cv2.blur(rng.normal(0, 1, (h, w)).astype(np.float32), (35, 3))
        self.streak = cv2.normalize(streak, None, 0, 1, cv2.NORM_MINMAX)

        # 形态学核
        self.edge_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (edge_width, edge_width))
        self.bleed_kernel = (bleed if bleed % 2 == 1 else bleed + 1,) * 2

        # 常用参数
        self.ink_color = float(ink_color)
        self.body_gamma = body_gamma
        self.feibai = float(feibai)
        self.edge_strength = float(edge_strength)

    def __call__(self, mask):
        """mask: 二值剪影图（0/255），返回单通道水墨图（uint8 灰度）。"""
        if mask.ndim == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        m = (mask > 127).astype(np.uint8)

        # 1) 内部浓淡：距离变换（到边缘的距离），归一化后做 gamma
        dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
        dmax = dist.max()
        body = dist / dmax if dmax > 0 else dist
        body = np.power(body, self.body_gamma).astype(np.float32)

        # 2) 晕染：模糊让墨洇开（也会轻微溢出轮廓，正是想要的）
        body = cv2.GaussianBlur(body, self.bleed_kernel, 0)

        # 3) 边缘笔锋：梯度取环带 -> 加粗 -> 随机场抖动
        edge = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, self.edge_kernel)
        edge = cv2.dilate(edge, self.edge_kernel)
        edge = cv2.remap(edge, self.mapx, self.mapy, cv2.INTER_LINEAR)
        edge_f = edge.astype(np.float32) * (self.edge_strength / 255.0)

        # 4) 飞白：边缘墨量按拉丝噪声衰减，亮纹处留白
        edge_f *= (1.0 - self.feibai * self.streak)

        # 5) 合成：总墨量 = 内部淡墨 + 边缘浓墨，半透明叠到宣纸上
        alpha = np.clip(body * 0.9 + edge_f, 0.0, 1.0)
        out = self.paper * (1.0 - alpha) + self.ink_color * alpha
        return np.clip(out, 0, 255).astype(np.uint8)


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
    # 演示：python src/ink_wash.py（在项目根目录运行）
    import os

    h, w = 520, 640
    mask = make_person_mask(h, w)
    washer = InkWasher(h, w)
    out = washer(mask)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "experiments")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "demo_ink_wash.png")
    cv2.imwrite(path, out)
    print(f"演示图已存: {path}")

    # 性能粗测（给以后 PS vs PL 对比表攒基线数据）
    t0 = time.perf_counter()
    for _ in range(30):
        washer(mask)
    ms = (time.perf_counter() - t0) / 30 * 1000
    print(f"水墨单帧耗时 ≈ {ms:.2f} ms  (尺寸 {w}x{h})")

    cv2.imshow("ink wash demo (按任意键关闭)", out)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


