"""PYNQ-Z2 完整演示：USB 摄像头 -> PL 剪影提取 -> PS 风格化 -> HDMI。

兼容环境：PYNQ 2.5 / Python 3.6 / NumPy 1.13。
把本文件、system.bit、system.hwh 和三个风格模块放在同一目录，运行：
    %run complete_camera_hdmi.py
"""

import time
import cv2
import numpy as np
from pynq import Overlay, allocate
from pynq.lib.video import VideoMode

from ink_wash import InkWasher
from papercut import Papercutter
from shadow_puppet import ShadowPuppeter


PROC_W, PROC_H = 320, 240
OUT_W, OUT_H = 640, 480
CAMERA_INDEX = 0
THRESHOLD = 25
STYLE = "ink"                 # ink / paper / puppet / silhouette
DMA_WORDS_MAX = 4000          # 16000 B；14 位 DMA 长度字段的合法上限是 16383 B
DMA_LENGTH_LIMIT = 16383
DMA_TIMEOUT = 2.0


def configure_vtc(vtc):
    """640x480p60 VTC generator，数值遵循 AMD XVtc 驱动定义。"""
    vtc.write(0x000, 0)
    vtc.write(0x060, (480 << 16) | 640)
    vtc.write(0x068, 0)
    vtc.write(0x06C, 0x73)                    # VGA H/V sync 为低有效
    vtc.write(0x070, 800)
    vtc.write(0x074, (525 << 16) | 525)
    vtc.write(0x078, (752 << 16) | 656)
    vtc.write(0x07C, (640 << 16) | 640)
    vtc.write(0x080, (491 << 16) | 489)
    vtc.write(0x084, (656 << 16) | 656)
    vtc.write(0x088, (640 << 16) | 640)
    vtc.write(0x08C, (491 << 16) | 489)
    vtc.write(0x090, (656 << 16) | 656)
    vtc.write(0x094, 480 << 16)
    vtc.write(0x000, 7)                       # core + update + generator


def wait_dma_idle(dma, status_offset, name, timeout=DMA_TIMEOUT):
    """带超时等待，硬件异常时抛错，不让 Notebook 永久卡住。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = dma.read(status_offset)
        if status & 0x70:
            raise RuntimeError("{} DMA error, status=0x{:08X}".format(name, status))
        if status & 0x02:
            return status
        time.sleep(0.0005)
    raise RuntimeError("{} DMA timeout, status=0x{:08X}".format(
        name, dma.read(status_offset)))


class PlSilhouette(object):
    """封装 AXI DMA 分块和背景/处理控制。"""

    def __init__(self, overlay, threshold):
        self.dma = overlay.axi_dma_0
        self.gpio = overlay.ctrl_gpio.channel1
        self.gpio.setdirection("out")
        self.gpio.setlength(16)
        self.threshold = int(threshold) & 0xFF
        self._set_ctrl(self.threshold << 8)

        total_words = PROC_W * PROC_H // 4
        self.sizes = []
        left = total_words
        while left:
            n = min(left, DMA_WORDS_MAX)
            self.sizes.append(n)
            left -= n
        if max(self.sizes) * 4 > DMA_LENGTH_LIMIT:
            raise RuntimeError("DMA chunk too large: {} bytes".format(max(self.sizes) * 4))
        print("DMA 安全分块(字节): {}".format([n * 4 for n in self.sizes]))
        self.in_bufs = [allocate(shape=(n,), dtype=np.uint32) for n in self.sizes]
        self.out_bufs = [allocate(shape=(n,), dtype=np.uint32) for n in self.sizes]

    @staticmethod
    def _words(gray):
        gray = np.ascontiguousarray(gray, dtype=np.uint8)
        return gray.reshape(-1).view(np.uint32)

    def _set_ctrl(self, value):
        value &= 0xFFFF
        self.gpio.write(value, 0xFFFF)
        actual = int(self.gpio.read()) & 0xFFFF
        if actual != value:
            raise RuntimeError("GPIO write failed: wrote 0x{:04X}, read 0x{:04X}".format(
                value, actual))

    def load_background(self, gray):
        words = self._words(gray)
        self._set_ctrl(self.threshold << 8)              # 先拉低 load_bg
        time.sleep(0.002)
        self._set_ctrl((self.threshold << 8) | 2)
        pos = 0
        for src, discard, n in zip(self.in_bufs, self.out_bufs, self.sizes):
            src[:] = words[pos:pos + n]
            src.flush()
            self.dma.recvchannel.transfer(discard)      # 背景也走标准先收后发
            self.dma.sendchannel.transfer(src)
            wait_dma_idle(self.dma, 0x04, "MM2S(background)")
            wait_dma_idle(self.dma, 0x34, "S2MM(background)")
            discard.invalidate()
            pos += n
        self._set_ctrl(self.threshold << 8)

    def process(self, gray):
        words = self._words(gray)
        result = np.empty(PROC_W * PROC_H // 4, dtype=np.uint32)
        pos = 0
        for src, dst, n in zip(self.in_bufs, self.out_bufs, self.sizes):
            src[:] = words[pos:pos + n]
            src.flush()
            self.dma.recvchannel.transfer(dst)          # 必须先接收、后发送
            self.dma.sendchannel.transfer(src)
            wait_dma_idle(self.dma, 0x04, "MM2S")
            wait_dma_idle(self.dma, 0x34, "S2MM")
            dst.invalidate()
            result[pos:pos + n] = dst[:]
            pos += n
        return result.view(np.uint8).reshape(PROC_H, PROC_W).copy()

    def close(self):
        for channel in (self.dma.sendchannel, self.dma.recvchannel):
            try:
                channel.stop()
                channel.start()
            except Exception:
                pass
        for buf in self.in_bufs + self.out_bufs:
            try:
                buf.freebuffer()
            except Exception:
                pass


def make_renderer(style):
    if style == "ink":
        obj = InkWasher(PROC_H, PROC_W)
        return lambda mask: cv2.cvtColor(obj(mask), cv2.COLOR_GRAY2BGR)
    if style == "paper":
        obj = Papercutter(PROC_H, PROC_W)
        return obj
    if style == "puppet":
        obj = ShadowPuppeter(PROC_H, PROC_W)
        return obj
    return lambda mask: cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


def start_hdmi(vdma):
    """紧邻首帧启动 VDMA，避免无有效视频期间通道因错误而停机。"""
    channel = vdma.writechannel
    if channel.running:
        channel.stop()
    channel.mode = VideoMode(OUT_W, OUT_H, 24, 60)
    channel.start()
    status = int(vdma.read(0x04))
    if not channel.running:
        raise RuntimeError("VDMA start failed, status=0x{:08X}".format(status))
    print("HDMI VDMA 已启动，status=0x{:08X}".format(status))


def write_hdmi(vdma, image):
    """写一帧；若 MM2S 意外停机，软复位后自动恢复一次。"""
    channel = vdma.writechannel
    if not channel.running:
        old_status = int(vdma.read(0x04))
        print("VDMA 停机，自动复位重启；旧 status=0x{:08X}".format(old_status))
        channel.reset()
        channel.start()
        if not channel.running:
            raise RuntimeError("VDMA restart failed, status=0x{:08X}".format(
                int(vdma.read(0x04))))

    hdmi_frame = channel.newframe()
    hdmi_frame[:] = image
    try:
        channel.writeframe(hdmi_frame)
    except Exception:
        try:
            hdmi_frame.freebuffer()
        except Exception:
            pass
        raise


def main():
    print("加载完整 overlay v0.4.4（安全分块 + HDMI 延迟启动版）...")
    ol = Overlay("system.bit")
    print("IP:", sorted(ol.ip_dict.keys()))

    vdma = ol.axi_vdma_0

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, PROC_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, PROC_H)
    if not cap.isOpened():
        raise RuntimeError("打不开 USB 摄像头 /dev/video{}".format(CAMERA_INDEX))

    pl = PlSilhouette(ol, THRESHOLD)
    render = make_renderer(STYLE)

    try:
        print("请离开摄像头画面，2 秒后自动采集背景...")
        deadline = time.time() + 2.0
        bg = None
        while time.time() < deadline:
            ok, frame = cap.read()
            if ok:
                frame = cv2.resize(frame, (PROC_W, PROC_H))
                bg = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if bg is None:
            raise RuntimeError("摄像头没有返回背景帧")
        pl.load_background(bg)
        print("背景加载完成；现在进入画面。STYLE={}".format(STYLE))

        # 单独 HDMI 测试的可靠顺序：配置时序后立即启动并送首帧。
        configure_vtc(ol.vtc_0)
        start_hdmi(vdma)

        count = 0
        report_t = time.time()
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.resize(frame, (PROC_W, PROC_H))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mask = pl.process(gray)
            styled = render(mask)
            shown = cv2.resize(styled, (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)

            write_hdmi(vdma, shown)

            count += 1
            now = time.time()
            if now - report_t >= 3.0:
                print("fps={:.1f}  style={}".format(count / (now - report_t), STYLE))
                count = 0
                report_t = now
    except KeyboardInterrupt:
        print("停止运行。")
    finally:
        cap.release()
        pl.close()
        try:
            vdma.writechannel.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
