# 皮影水墨交互系统快速上手说明书

皮影水墨交互展示系统  技术文档系列

QS 02  |  版本 1.0  |  2026年11月2日

本说明书适用于已配置比赛SD镜像与依赖包的板卡，目标是在同一硬件任务中完成皮影、水墨、暂停、继续和正常停止。空白SD卡需要先安装匹配的PYNQ镜像及依赖，本手册不能替代系统镜像安装。

## 1 上电与网络

连接摄像头、HDMI OUT、网线和串口，上电后等到Linux提示符。串口工具使用115200 8N1，关闭RTS/CTS流控，发送命令需带换行。COM口按设备管理器的当前编号选择。

在板卡串口或Linux终端逐行执行：

```bash
ip -4 addr show eth0
cat /proc/sys/kernel/random/boot_id
uptime
```

以实际IP为准。若板卡仍为192.168.2.99/24，电脑有线接口应在同一子网且IP不同，例如192.168.2.1/24。先在Windows管理员PowerShell确认网卡名称，再使用下面的临时地址配置；将“以太网”替换为实际连接板卡的接口。

```powershell
Get-NetAdapter
$nic = Get-NetAdapter -Name "以太网"
Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4
# 仅当该接口尚无192.168.2.1时添加
New-NetIPAddress -InterfaceIndex $nic.ifIndex `
  -IPAddress 192.168.2.1 -PrefixLength 24 -PolicyStore ActiveStore
ping -n 3 -w 1000 192.168.2.99
```

本直连地址不需要默认网关。ActiveStore设置可能在电脑重启后失效；不要盲用旧网卡索引17。浏览器打开 http://192.168.2.99:9090，按当前镜像凭据登录。

## 2 准备摄像头和运行文件

只在没有旧硬件任务时准备USB省电状态。在板卡Linux终端逐行运行：

```bash
echo on | sudo tee /sys/bus/usb/devices/1-1/power/control
cat /sys/bus/usb/devices/1-1/power/control
cat /sys/bus/usb/devices/1-1/power/runtime_status
```

后两次输出应为on和active。sudo密码不会回显。若提示路径不存在，应先检查设备是否接入；写on并不等于成功采集，也不能修复已经阻塞的原生读取。

### Notebook代码和终端命令要分清

上述echo与cat是Linux命令，不能直接粘贴进普通Python格。若在Jupyter执行，使用明确的Shell调用；首次操作建议直接用串口Linux终端，避免sudo密码交互混在Notebook里。

### 上传配套文件

```text
projection_shadow_ink_combined.ipynb
projection_shadow_ink_combined_resources.zip
```

两份文件一起放到 /home/xilinx/jupyter_notebooks，ZIP不解压、不改名。合体版只恢复独立附加包，板上仍须保留原240文件包与新PL依赖包。缺依赖时，先按对应包的恢复说明安装，不能用另一个历史Notebook或任意位流替代。

### 现场准备

关闭摄像头透传、其他视频程序和旧演示。固定机位、背景和灯光，人物先离开视野。确认是合体版8格入口，不是旧30秒或ENTRY_PROBE_ONLY文件。禁止Run All。

## 3 按八个代码格运行

| 格号 | 操作 | 需要观察的结果 |
| --- | --- | --- |
| 1 | 准备 | 读取当前boot_id，核对文件和USB；不启动硬件 |
| 2 | 启动或恢复 | 输入START后按键盘Enter；等待空背景提示，再输入EMPTY并按Enter |
| 3 | 皮影并继续 | 仅LIVE或PAUSED状态接收命令，复用相机与位流 |
| 4 | 水墨并继续 | 切到PL水墨渲染；首次初始化可能有额外耗时 |
| 5 | 暂停 | 冻结显示；相机仍在后台采集、检查和取走新帧 |
| 6 | 继续 | 恢复当前效果并清空旧水墨留痕 |
| 7 | 只读查看 | 核对状态、帧数、报告和真实退出证据 |
| 8 | 正常停止 | 提交协作停止请求；等待清理及进程真实退出 |

输入START或EMPTY后按Enter，不要再次点Run。收到EMPTY后，等待第7格状态进入LIVE且帧数增长，再进入镜头、切换风格。第二格刚返回时可能仍显示旧的WAITING状态；若长时间不更新，不应反复发确认或启动。

常见正常状态为CAMERA_WARMUP、EXPOSURE_SETTLE、WAITING_FOR_EMPTY_CONFIRMATION、COLLECTING_BACKGROUND和LIVE。状态长时间不变要结合更新时间和日志，不能单凭状态名称推断正在正常等待。

命令采用单槽方式。上一条命令尚未处理时，只运行第7格观察，不覆盖待处理命令。不以点击更快、重复Run或重启Notebook内核来恢复设备。

## 4 停止与下次演示

第8格是结束任务，不是暂停。程序没有固定30秒时限，但不保证长期相机稳定。同一次开机停止后，保留的预留记录会阻止新开第二个硬件任务；第2格只恢复原任务。

| 检查项 | 正常完成条件 |
| --- | --- |
| 状态 | FINISHED_SAFELY |
| 所有者 | owner_running=False |
| 实际退出 | exit_code=0 且 exit_evidence_valid=True |
| 设备清理 | cleanup_safe=True |
| 综合结果 | effective_success=True |

这组条件用于确认有实际运行帧并安全完成。STOPPED_SAFELY表示失败或提前退出后安全停止，不能写成演示成功。UNSAFE_REBOOT_REQUIRED表示清理没有被证明安全，不能直接再开第二格。

### 重新演示与关机

需要新任务时，先保存结果目录并正常重启板卡。在Linux终端执行sudo reboot；重启完成后核对boot_id已变化，再设置USB on/active并从第1格重新准备。合体版自动读取新编号，不需要手工修改Notebook里的UUID。

```bash
sudo reboot
# 重新出现提示符后核对
cat /proc/sys/kernel/random/boot_id
uptime
```

结束当天使用时，先确认任务退出并清理安全，再执行sudo poweroff。只有串口明确完成关机后才断电。若服务停止长期卡住，保存串口日志，不把反复发送关机命令当作恢复方法。

### 保存本次证据

记录第7格给出的完整结果目录，保存status.json、final_report.json、exit.json、events.jsonl与实际存在的日志。49秒视频及单个成功状态不能替代整轮退出证据。
