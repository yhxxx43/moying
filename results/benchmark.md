# 性能测试记录 Benchmark

更新日期：2026-11-02。对象：PYNQ皮影水墨交互展示系统。单位：耗时为毫秒（ms），时长为秒（s），帧率为帧/秒（FPS）。本记录整理历史实测结果并重新计算统计量，本次未启动板卡或重新跑性能测试。

## 1 结果摘要

- 新PL水墨渲染的保存掩膜离线记录：CPU中位耗时 **163.307 ms**，PL路径 **111.232 ms**，中位耗时加速比 **1.468×**，减少 **31.89%**。这是渲染边界的结果，不是整机帧率。
- 已核对的历史整机演示中，基础剪影为 **7.255 FPS**，暖纸皮影为 **2.126 FPS**，20%留痕CPU水墨为 **3.258 FPS**。三者视觉效果、运行时段和worker不同，不能作为同条件优化前后对比。
- 当前皮影水墨合体版没有与49.37秒现场视频匹配的完整逐帧日志，本记录不为它填写推测FPS。视频文件的30 fps是录像帧率，不是板卡处理帧率。

## 2 测试环境与版本

| 项目 | 记录配置 |
| --- | --- |
| 硬件 | PYNQ-Z2，Zynq-7020；USB UVC摄像头046d:0825；HDMI显示器 |
| 板上软件 | PYNQ 2.5，Python 3.6.5，NumPy 1.13.3，OpenCV 3.2.0 |
| 图像规模 | 采集与分割320×240；显示图像640×480；人物掩膜为0或255的uint8 |
| 历史实时数据路径 | PL参与前景先验，PS完成人物后处理；本表列出的历史艺术渲染在CPU侧 |
| 新PL离线数据路径 | PS时间历史与打包，PL掩膜放大、边缘分类、纹理与留痕混合 |
| 条件 | 固定机位、绿色背景；不同历史运行的动作、背景和系统负载未统一控制 |
| 原始依赖包SHA256 | 9f50fbd2b3daaadb548b72dfadc16be8b22a06960192a2c71c4fdf245b75492d |
| 新PL依赖包SHA256 | 923b6ab2fba9b54d2a8b15521eac06fd3d43a906567103d479b607cb03ff1122 |

硬件和软件配置来自项目源码及运行记录；5次实时结果的包摘要、boot_id和worker摘要分别保存在[证据清单](benchmark_evidence/source_manifest.json)。不将这5次历史运行归为新PL合体版的重复试验。

## 3 统计口径

1. **整机应用FPS** = `displayed_frames / live_elapsed_s`，直接按原报告的显示帧数和应用运行时段重算。这里的显示帧数是应用提交给显示封装的计数，没有独立测量HDMI屏幕刷新或扫描出的光子时刻。
2. **application_ms** 为逐帧应用处理边界，包含取新帧等待、人物分割、渲染与显示提交等。**segment_ms**是PS/PL协同人物分割，**render_ms**是艺术渲染，**hdmi_ms**是显示封装调用耗时。
3. **receipt_to_submit_ms** 从软件完成采集后记录的接收时间到VDMA提交；不包括完整相机曝光与传感器链路，不是曝光到屏幕的端到端延迟。
4. 每轮按原CSV全部记录重算算术均值、中位数和P95。P95使用排序后位置`(N-1)×0.95`的线性插值，与原报告一致。实时表未擅自删去首帧或初始化较慢帧。
5. 各阶段中位数之和不必等于总耗时中位数；FPS也不能用`1000 / application_ms中位数`代替。需要比较加速比时，分子分母必须是同一数据集和同一测量边界。
6. 49秒录像、摄像头配置30 fps和显示器刷新参数不参与应用FPS统计；缺少原始相机帧计数时，不计算USB丢帧率。

## 4 历史整机运行实测

数据来自5个真实结果ZIP内的原始报告与CSV，共468条逐帧记录。所有报告均记录`execution_success=true`、`cleanup_safe=true`、相机和HDMI已启动，抽样掩膜对照差异为0。ZIP未包含`exit.json`，因此本文件仅核验报告与逐帧数据，独立的实际进程退出证明需从原结果目录补充。

| 编号 | 风格 | 帧数 N | 应用运行时长 s | 应用FPS N/T | CSV行数 |
| --- | --- | --- | --- | --- | --- |
| L06 | silhouette | 218 | 30.048141 | 7.255 | 218 |
| L08 | shadow_static | 41 | 30.282921 | 1.354 | 41 |
| L09 | shadow_static | 47 | 30.125393 | 1.560 | 47 |
| L10 | warm-paper-shadow-v1 | 64 | 30.102685 | 2.126 | 64 |
| L11 | xuan-ink-trail20-fast-v1 | 98 | 30.075795 | 3.258 | 98 |

L06为基础剪影；L08与L09为旧静态皮影；L10为暖纸美化皮影；L11为快速20%留痕CPU水墨。L11的“fast”不表示新版PL渲染，五份报告都来自原始依赖包9f50fbd2…的不同worker。

### 4.1 应用逐帧耗时

| 编号 | 均值 ms | 中位数 ms | P95 ms | 接收到提交 中位数 ms | 接收到提交 P95 ms |
| --- | --- | --- | --- | --- | --- |
| L06 | 130.335 | 131.732 | 142.414 | 160.552 | 194.334 |
| L08 | 719.226 | 913.405 | 949.098 | 944.964 | 1001.961 |
| L09 | 623.035 | 893.470 | 939.863 | 926.377 | 996.949 |
| L10 | 455.520 | 453.964 | 512.985 | 482.272 | 547.113 |
| L11 | 292.793 | 305.866 | 356.083 | 329.403 | 374.369 |

均值与P95均按每轮CSV独立统计，不把多个风格混合成一个性能分数。以上延迟边界没有测量屏幕扫描、相机曝光或人的主观响应时间。

### 4.2 分阶段中位耗时

| 编号 | 协同人物分割 ms | CPU艺术渲染 ms | 显示提交 ms | 报告的跳过接收帧数 |
| --- | --- | --- | --- | --- |
| L06 | 87.836 | 10.986 | 29.927 | 233 |
| L08 | 89.895 | 787.245 | 31.904 | 401 |
| L09 | 88.445 | 769.726 | 23.540 | 393 |
| L10 | 88.180 | 330.157 | 25.458 | 382 |
| L11 | 93.042 | 187.307 | 27.401 | 791 |

跳过接收帧数对应最新帧消费策略及序号间隔，不等于USB链路丢帧。静态艺术效果较复杂时，CPU渲染耗时显著高于分割；不同风格不应拿来直接宣称相同画质下的加速。

### 4.3 可复核文件

| 编号 | 原始报告 | 逐帧CSV | 复算结果 |
| --- | --- | --- | --- |
| L06 | [report](benchmark_evidence/live_trial_06/final_report.json) | [metrics](benchmark_evidence/live_trial_06/metrics.csv) | [recomputed](benchmark_evidence/live_trial_06/recomputed.json) |
| L08 | [report](benchmark_evidence/live_trial_08/final_report.json) | [metrics](benchmark_evidence/live_trial_08/metrics.csv) | [recomputed](benchmark_evidence/live_trial_08/recomputed.json) |
| L09 | [report](benchmark_evidence/live_trial_09/final_report.json) | [metrics](benchmark_evidence/live_trial_09/metrics.csv) | [recomputed](benchmark_evidence/live_trial_09/recomputed.json) |
| L10 | [report](benchmark_evidence/live_trial_10/final_report.json) | [metrics](benchmark_evidence/live_trial_10/metrics.csv) | [recomputed](benchmark_evidence/live_trial_10/recomputed.json) |
| L11 | [report](benchmark_evidence/live_trial_11/final_report.json) | [metrics](benchmark_evidence/live_trial_11/metrics.csv) | [recomputed](benchmark_evidence/live_trial_11/recomputed.json) |


## 5 新PL水墨渲染离线对照

该项测量对应`render_saved_masks_8b70d02b11f4`，启动编号`d818862c-9938-4ace-9fdb-c92dccdadabc`。使用14组保存掩膜、3轮、42次测量，另有2次预热；记录称逐像素比较差异为0、清理安全且实际退出码为0。此阶段不启动摄像头、HDMI或人物分割。

| 同一渲染边界 | CPU参考 ms | PL路径 ms | CPU/PL 加速比 | 耗时减少 |
| --- | --- | --- | --- | --- |
| 中位数 | 163.306970 | 111.232360 | 1.468161× | 31.8876% |
| P95 | 164.982447 | 111.557947 | 1.478895× | 32.3819% |

PL路径包含PS配套的时间历史、输入打包、缓存同步、提交/等待和结果复制边界；不能当作仅PL核内计算耗时。`1000/111.232360 ≈ 8.990次/秒`只是离线渲染中位耗时的倒数，不是整机实时FPS，不能写入实时帧率列。
P95行的比值是CPU与PL各自P95耗时之比，不是逐样本加速比的P95。

**证据等级说明：** 本次本地找到的是开发记录`progress.md`和说明书原生成材料`artifact.md`对原板上报告的摘录，尚无该轮原始42行CSV可重新逐行核算。这里如实收录已记录值并复算比值，不声称本次已重验原始离线样本。摘录见[new_pl_record_excerpt.md](benchmark_evidence/new_pl_record_excerpt.md)。

**数值勘误：** 历史说明书中的“263.31 ms→111.23 ms、下降57.75%”与原记录不符，不采用。按本轮已有记录，应为“163.31 ms→111.23 ms、减少31.89%、加速比约1.47×”。

## 6 长时间运行与失败记录

历史常驻PL水墨运行曾报告878帧、运行217.546668 s后发生`No fresh camera frame within timeout`，且`reader_stopped=false`、`cleanup_safe=false`、`operator_stopped=false`。这是故障前部分运行记录，不作为稳定性通过，也不与30秒成功演示混合平均。该记录来自会话回传，未取得同轮完整CSV，本文件不为它补造阶段耗时或P95。
合体版曾停留在`WAITING_FOR_EMPTY_CONFIRMATION`且没有进入LIVE；49.37秒现场视频随后展示了两种效果，但其对应worker、boot_id和完整退出记录尚未关联。因此，当前合体版的可复核应用FPS、连续稳定时长和正常退出成功率仍记为“待测/待关联日志”。

## 7 数据解读与后续记录

- 已证实的局部收益：保存掩膜同一渲染边界的中位耗时比约1.47×。没有受控整机CPU/PL对照，不填写“整机加速比”。
- 优先关注CPU艺术合成、PS历史打包与数据搬运，而不能只看PL等待时间。需以同样输入和输出画质验证后续优化。
- 新增测试应记录：boot_id、包/位流/HWH/worker摘要，图像尺寸，测试风格、样本和预热数，帧数与实际应用时长，各阶段mean/median/P95，像素差异、错误和清理/真实退出证据。
- 运行失败也须留档。连续稳定性测试应单独记录目标时长、实际时长与退出原因；不得因为录像只有49秒而填写“稳定运行49秒以上”或长期无故障。

## 8 数据来源与复算

实时部分来源：`trial_result (6).zip`、`trial_result (8).zip`、`trial_result (9).zip`、`trial_result (10).zip`、`trial_result (11).zip`。原ZIP未修改；仅将报告、CSV和复算结果复制到本文件相邻的`benchmark_evidence/`。每个原ZIP和提取成员的SHA256见[source_manifest.json](benchmark_evidence/source_manifest.json)。
新PL离线部分来源：项目开发记录与说明书生成过程的证据摘要，保留原文件SHA256与摘录。视频编码与30 fps来自媒体文件检查，不作为计算性能数据来源。
复算脚本为文档工作目录中的`build_benchmark.py`，仅读取已保存文件并计算统计量，不导入PYNQ，不操作摄像头、DMA或HDMI。
