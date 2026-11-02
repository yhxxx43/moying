# board 目录说明
> `board/` 存放皮影交互系统板端 Python 业务代码，负责 Overlay 加载、硬件身份校验、分阶段离线诊断、30秒受控演示、常驻运行、进程监护、资源锁、结果报告输出。
> Vivado 硬件 BD / 约束文档放在上层 `../board_hw/`，**本目录不存放 bit / hwh 比特流文件**。

## 📂 文件清单
```
board/
├── **init**.py               # 包入口，导入不会打开任何硬件设备
├── render_device.py         # PYNQ 硬件设备封装：overlay 加载、MMIO 身份校验、benchmark 离线测试
├── render_launch.py          # 一次性诊断阶段的 supervise/worker 进程监护
├── render_benchmark.py       # PL 渲染与 CPU 参考对照基准测试
├── render_offline.py         # 离线校验层：文件哈希校验、分阶段执行合法性校验
├── render_live.py            # 完整 30 秒受控现场演示业务逻辑
├── render_live_launch.py     # live 演示独立监督进程
├── render_live_resources.py  # live 模式硬件资源初始化、overlay 关闭、资源释放
├── owner_lease.py            # 全局排他文件锁，防止多进程抢占 DMA 硬件
├── recover_render.py         # 离线安装包 zip 解压、完整性校验
├── resident_launch.py        # 常驻模式启动、进程管理
├── resident_runtime.py       # 常驻模式完整运行逻辑
├── resident_loop.py          # 常驻帧循环（无 30s 倒计时）
├── resident_support.py       # 日志轮转、控制命令消息、异常定义
├── resident_recover.py       # 常驻附加包 zip 恢复校验
├── combined_resources.py     # shadow (CPU 皮影) /ink (PL 水墨) 双模式资源
├── combined_control.py       # 运行时模式切换控制指令处理
├── combined_loop.py          # 双模式帧循环
├── math_probe.py             # CPU 数学单元校验，不依赖硬件
└── render_worker.py          # 通用 worker 执行、journal 事件日志、boot_id 获取
```
## 核心模块说明

### 1. 离线诊断模块 render_offline / render_device / render_benchmark
**适用场景**：开发调试、赛前硬件预检；不运行完整摄像头演示，只做硬件通路校验。

**使用方法**
1. 分阶段顺序执行：`precheck` → `load` → `identity` → `render_synthetic` → `render_saved_masks`
2. 每个阶段必须依赖上一阶段成功输出，不可跳过阶段。
3. `render_synthetic` 使用合成掩码做基准；`render_saved_masks` 使用保存的真实掩膜。
4. 输出至 `offline_results/`，包含像素差异、各环节耗时报告。

**失效条件**
- bit 与 hwh 版本不匹配，硬件身份 `0x50524a00` 校验失败。
- 跳过前置阶段直接执行后续诊断。
- CMA 内存分配失败。

**经验来源**
项目现场演示容易直接跑完整Demo，硬件故障被掩盖。通过分阶段离线诊断，可以提前拦截 overlay、DMA、渲染核相关故障。

### 2. 一次性完整演示 render_live（30秒皮影交互）
**适用场景**：竞赛现场受控30秒演示流程。

**使用方法**
1. 需要全套离线诊断全部通过，才允许进入 live。
2. 执行流程：文件哈希校验 → 获取全局owner锁 → overlay加载 → GPIO身份校验 → 等待空场背景确认 → 30秒帧采集分割渲染HDMI输出 → 自动保存样本、metrics报告、事件日志。
3. 异常策略：**不自动重试**；清理不安全时保留硬件所有权，提示需要重启板子。

**失效条件**
- USB摄像头不是 `046d:0825`，设备未激活。
- 板子重启导致 boot_id 改变，旧会话不可复用。
- 前置离线阶段未全部通过。

**经验来源**
解决现场演示崩溃后无故障日志问题；设计完整证据留存，失败保留报告，不盲目自动重试掩盖底层问题。

### 3. 常驻模式 resident（combined双模式）
**适用场景**：展馆长时间连续运行；支持切换 `shadow`（CPU皮影）、`ink`（PL水墨），支持暂停。

**使用方法**
1. 附加包zip校验恢复，获取排他锁。
2. 进入持续帧循环，没有30秒时间限制。
3. 接收外部控制命令切换渲染模式、暂停。
4. 退出必须执行完整资源释放，输出运行统计。

**失效条件**
- 多进程抢占硬件资源。
- 资源释放失败，必须重启板子。
- boot_id 发生变更，旧handle全部失效。

**经验来源**
30秒为竞赛受控演示版本；展馆长期运行需要独立常驻业务，两套运行入口互相隔离。

### 4. owner_lease 硬件排他锁
**适用场景**：防止Jupyter多次执行、多Python进程同时操作DMA/Overlay，引发总线挂死。

**使用方法**：所有加载overlay的worker，启动首先获取`OwnerLease`排他锁；确认执行安全完成之后再释放锁。

**失效条件**：进程异常崩溃未释放锁；后续进程拿不到锁，提示其他owner正在运行。

**经验来源**：多次出现重复加载overlay、多进程抢占硬件造成PYNQ系统卡死，增加文件锁做互斥保护。

### 5. 校验与恢复 recover_*.py
**适用场景**：交付zip包部署至板端；检测文件篡改、文件损坏。

**使用方法**：读取`manifest.json`内sha256哈希，对每一个文件完整性校验；限制安全路径，拒绝`..`路径逃逸。

**失效条件**：zip包被修改、文件损坏、包含不安全路径。

**经验来源**：为保证竞赛交付包可复现，全部交付资源强制做哈希完整性校验。

## 关键硬件常量
- PL渲染核MMIO基地址：`0x43c10000`
- ctrl_gpio预期身份值：`0x50524a00`
- render_accel身份：`0x504c5231`，版本 `0x10000`
- USB摄像头VID/PID：`046d:0825`
- 图像输入分辨率：`240×320`；HDMI输出：`640×480`

## 产物输出约定
所有运行输出报告、日志、样本图片，统一输出到包目录下 `offline_results/`：
- `final_report.json`：结构化测试报告，包含success标志、耗时、错误信息
- `events.jsonl`：运行时序事件日志
- `*.log`：supervise、worker原始stdout/stderr输出

## 安全约束
1. 硬件故障**不会自动重试**；清理不安全时保留硬件占有，提示人工重启开发板，不强行杀死原生驱动。
2. 硬件操作顺序：文件哈希校验 → owner锁获取 → overlay加载 → 身份校验，之后再执行业务逻辑。
3. `board/`只存放Python源代码；bit/hwh硬件文件放置在上层交付包，不在此目录。

## 故障快速排查清单
1. overlay加载失败：核对bit‑hwh是否同一编译产物；检查是否旧进程持有owner锁。
2. identity身份校验报错：位流版本不对，未加载正确stem的bit文件。
3. live模式USB异常：确认摄像头是`046d:0825`，usb电源状态为active。
4. 提示`UNSAFE_REBOOT_REQUIRED`：资源无法安全释放，**需要重启PYNQ板子，不要继续运行**。
5. 获取owner lease失败：存在其他worker/Jupyter占用硬件，重启板子释放锁。

## 文档跳转链接
- 硬件BD/时钟/约束文档： [`../board_hw/README.md`](../board_hw/README.md)
- 可复用技能包： [`../skill/README.md`](../skill/README.md)
- 参考比对测试脚本： [`../tests/reference_comparison.py`](../tests/reference_comparison.py)

## 完整仓库目录概览
```
project-root/
├── board/                     # 本目录，板端 python 业务代码
├── board_hw/                  # Vivado 硬件工程说明文档
├── skill/                     # 竞赛技能包，可复用经验文档
├── tests/                     # 测试脚本
├── docs/                      # 设计说明书、框图、实物图片
└── manifest.json              # 交付包哈希清单
```
