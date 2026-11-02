[核验记录_20261102153346.md](https://github.com/user-attachments/files/34260938/_20261102153346.md)

# 交付核验

2026-10-23，主机完整回归138/138通过，109.704s，实际退出码0。10项新增测试经历RED→GREEN。使用独立只读复核者检查控制、实际解包集成与并发发布，无阻断发现。

- Notebook：projection_shadow_ink_combined.ipynb
- ZIP：projection_shadow_ink_combined_resources.zip
- Notebook SHA256：bfeeaa31cf4bbb7a36fc2e9ece7340b41c70cf3df2a2db0df017ad302b8d33ce
- ZIP SHA256：66f9fa7bfa8f91eafd2de193342977fbc7de82bdfc99f771871a91b789d87105
- manifest SHA256：642ab6694c8dc676ac30544631061c4aec01400cc32af79409bd4e5585123a3c

8个代码格，最长3105字符。生成的第一格在禁止numpy/cv2/pynq导入的条件下实际执行通过。所有打包Python文件及代码格通过Python3.6语法解析。原源ZIP的24个文件原字节保留；冻结240文件、已离线通过的新PL bit/HWH、先前水墨常驻版交付文件hash不变。

设计选择：非Git项目，新交付独立保存，旧包不覆盖；暖纸皮影沿用原CPU合成，水墨沿用PL合成，两者PL参与分割，皮影可能较慢。暂停是冻结画面并持续取帧，不是释放相机；选择另一个效果即继续。

已知未解决：约一分钟后摄像头可能卡死。本合体版未做板上实时验证，不承诺长稳；相机或清理不安全仍必须保留所有权，不能立即重跑。当前旧任务已不安全时须先正常重启板卡。

不授权生产使用；主机/模型测试不等于板上、HDMI或比赛稳定性通过。源码、日志与复核记录保存在projection_pl_render_work_20261022/results下。
