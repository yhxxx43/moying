# PYNQ‑Z2 FPGA协同人物分割皮影交互系统 · 技能包

> 本技能包沉淀本项目大模型辅助软硬件协同开发经验，可复用于其他PYNQ‑Z2 FPGA图像加速、PS‑PL协同视觉项目。

## 📂 文件清单
| 文件 | 用途 |
|---|---|
| [`prompt‑workflow.md`](./prompt-workflow.md) | 大模型辅助FPGA开发提示词协作工作流 |
| [`ps‑pl‑partition.md`](./ps‑pl‑partition.md) | Zynq PS/PL软硬件任务划分方法论 |
| [`axi‑dma‑cache‑sync.md`](./axi-dma-cache-sync.md) | AXI‑DMA传输与DDR缓存同步踩坑指南 |
| [`demo‑reproducible.md`](./demo-reproducible.md) | 竞赛现场可复现演示、分阶段准入校验方案 |
| [`notebook‑resource‑load.md`](./notebook-resource-load.md) | Jupyter Notebook资源加载与启动性能优化 |

## ⚠️ 重要约束
1. `skill/**`仅存放文档，**不存放Python执行代码、位流、测试样本**。
2. 参考对比校验代码：`tests/reference_comparison.py`。
3. 每一份文档强制包含4项：**适用场景、使用方法、失效条件、经验来源**。

## 适用范围
面向固定机位绿幕皮影交互系统，通用迁移到PYNQ图像软硬件协同项目。
