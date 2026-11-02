# 皮影水墨交互系统故障排查手册

皮影水墨交互展示系统  技术文档系列

OPS 04  |  版本 1.0  |  2026年11月2日

先保存状态和启动身份，再决定是否恢复。本手册覆盖网络、USB、Notebook、PL总线和关机故障。各段命令标注运行环境，Linux命令不能直接当作Python代码。

## 1 先判定任务状态

| 看到的状态 | 解释 | 下一步 |
| --- | --- | --- |
| LIVE或PAUSED，帧数或相机消费有变化 | 硬件任务仍在运行 | 使用原句柄控制；需要结束则运行第8格 |
| In [*]且没有首条输出 | Notebook可能排队、输入未提交或执行尚未进入 | 停止追加Run；核对输入框和第7格 |
| WAITING且长时间无更新 | 可能已确认EMPTY但阻塞于后续原生调用 | 核对empty文件身份和事件时间；不要重复启动 |
| STOPPED_SAFELY并有有效实际退出 | 执行失败或提前停止，但清理已完成 | 读errors并保存报告；不当作成功演示 |
| UNSAFE_REBOOT_REQUIRED或清理不明 | 设备、读帧线程或总线停止未证明安全 | 保留所有者与缓冲，正常重启板卡后再准备 |

### 三个判断误区

DONE灯亮不能证明相机、DMA和显示都正常；灯灭后又亮也不能单独证明Linux重启，应核对boot_id和uptime。

owner_running=True是缺少有效退出证据时的保守标记，不独立证明进程存活。FINISHED_SAFELY也需配合真实退出证据。

暂停仍取帧，不能消除相机故障。禁止删标记、强杀或重载位流绕过未知设备所有权。

## 2 网络与串口故障

### 电脑不能打开板卡网页

先在串口Linux终端确认真实地址与启动状态，再核对电脑有线网卡，不要依据旧截图猜IP。

```bash
ip -4 addr show eth0
cat /proc/sys/kernel/random/boot_id
uptime
```

eth0出现LOWER_UP说明物理链路已建立。若地址是192.168.2.99/24，电脑对应接口可用192.168.2.1/24。电脑若只有192.168.137.1，需按快速上手配置同子网临时地址；先确认网卡名称，避免改到Wi-Fi或VPN。

```powershell
# Windows PowerShell
Get-NetAdapter
Get-NetIPAddress -AddressFamily IPv4
ping -n 3 -w 1000 192.168.2.99
```

ping成功但网页失败时，核对端口9090和当前Jupyter服务；ping超时先排查线缆、链路灯、子网和电脑接口。电脑或板卡重启后，临时地址和启动身份都要重新确认。

### 串口只有发送记录没有接收

串口打开不等于Linux在处理命令。核对当前COM口、115200 8N1、无硬件流控与发送换行。先发送一个回车观察是否出现提示符；没有提示符时，连续发送sudo reboot不能证明命令已执行。

sudo密码不回显是正常现象；不能把密码输入到未出现密码提示的普通Shell。终端返回Terminated也不能单凭这一行确认已重启，重新读取boot_id验证。

## 3 USB与摄像头故障

| 现象 | 证据解释 | 处理 |
| --- | --- | --- |
| auto与suspended | USB允许运行时自动挂起 | 确认无旧任务后写on；再次读到on与active |
| /dev/video0和video1都存在 | 已查设备分别为图像和元数据节点 | 使用video0；节点存在不证明摄像头可正常传帧 |
| Cannot open video0 | 打开设备失败，可能占用或UVC控制超时 | 只读查占用和内核日志，不立即反复重试 |
| UVC probe control -110 | 该控制传输超时 | 保留时间线；不能仅据此判断摄像头损坏或电源故障 |
| No fresh camera frame | 应用在等待期内未拿到新帧 | 检查reader_stopped与cleanup_safe；不安全则重启 |

板卡Linux终端的只读检查：

```bash
ls -l /dev/video*
cat /sys/bus/usb/devices/1-1/idVendor
cat /sys/bus/usb/devices/1-1/idProduct
cat /sys/bus/usb/devices/1-1/power/control
cat /sys/bus/usb/devices/1-1/power/runtime_status
fuser -v /dev/video0
dmesg | grep -Ei "uvc|usb 1-1|ci_hdrc" | tail -n 40
```

fuser无输出且退出码1仅表示该次检查没有列出占用；不能证明USB健康。on/active也只是电源管理状态。相机曾在电脑正常成像，同样不能排除板卡侧UVC、控制器或读帧链路问题。

若错误包含Camera read remains blocked或reader_stopped=False，线程未安全回收。保留进程与缓冲，保存报告后正常重启。不要用USB热拔插、直接重新开第二格或Python线程超时来声称已解除原生阻塞。

## 4 Notebook与PL故障

### 输入与排队

input要求START或EMPTY时，只在输入框填对应英文并按Enter。再次点Run会追加另一次执行。NameError提示resident、context或模块未定义，通常是没有按顺序运行第1格或内核变量已丢失；已有任务时第1格和第2格应恢复原句柄，不能另起硬件。

### EMPTY后仍然WAITING

先用第7格查看。若empty.json已存在且boot、run_token、empty_token匹配，但状态更新时间已过去数分钟，则不能再解释为普通界面延迟。该版本在确认后进入背景采集时，首次read或曝光检查返回前不一定有新事件；卡点不能仅靠旧WAITING标签精确定位。

请求第8格正常停止一次并观察。若没有有效exit.json或清理仍不明，不强行切风格、不删除empty文件、不重开任务。保存状态和事件末尾，正常重启后再建立新上下文。

### 身份与总线

| 错误 | 含义与处理 |
| --- | --- |
| KeyError render_accel_0 | PYNQ 2.5应核对render_accel_0/s_axi；使用已修复且匹配bit/hwh的包，不能直接篡改冻结包 |
| exit_code=-7 | Linux子进程负退出码通常指向SIGBUS；不能当成安全Python异常。保留日志并正常重启，禁止反复MMIO探测 |
| Wrong identity或地址不符 | 位流、HWH、元数据或驱动契约不匹配；停止后核对发布清单 |
| Render polling timeout | 等待超时不是设备已停止证明；按驱动联合清理结果处理 |
| Joint cleanup not proven safe | 任一设备或线程缺少结束证明会阻止全部关联缓冲释放；不通过手工freebuffer绕开 |

## 5 日志留存与正常重启

从第7格复制当前结果目录。只读取该目录，不重新导入PYNQ、不访问寄存器：

```python
# 板卡Jupyter Python格  将路径替换为第7格结果目录
from pathlib import Path
r = Path("替换为实际结果目录")
for name in ("status.json", "final_report.json", "exit.json"):
    f = r / name
    print("\n==", name, "==")
    print(f.read_text() if f.is_file() else "尚未生成")
f = r / "events.jsonl"
if f.is_file():
    print("\n".join(f.read_text().splitlines()[-20:]))
```

同时保存boot_id、uptime、内核末尾日志、实际存在的supervisor或worker日志，以及运行前后执行了什么。日志文件尚未生成不等于无错误。板卡时间曾被系统校正，跨日志关联应同时记录单调uptime与墙上时间，不能仅靠时间戳猜因果。

### 重启与关机

正常停止无法完成且程序明确要求重启时，在Linux提示符下执行sudo reboot。Jupyter stop job可能等待数分钟；显示的等待上限改变不证明重启成功。保存串口日志，观察新的启动过程，再读取boot_id。重启后必须确认编号变化。

```bash
# Linux终端
sudo reboot
# 系统重新启动并出现提示符后
cat /proc/sys/kernel/random/boot_id
uptime
```

重启后USB可能回到auto/suspended，按快速上手重新准备。关机使用sudo poweroff，确认系统完成关机后再断电；本手册不提供在未知总线活动状态下强制断电的自动恢复方案。

### 交接时必须写清

提交四项信息：实际Notebook和软件包版本；出错阶段与最后状态；是否有有效实际退出证据；是否已经正常重启并取得新boot_id。长期摄像头阻塞的根因尚未闭环，不能把一次短视频成功当作故障已解决。
