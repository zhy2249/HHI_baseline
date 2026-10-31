# 实验结果收件目录

按实验编号建立 `experiment_<三位编号>_<名称>/` 子目录，接收服务器 CSV、工作簿等原始结果。表格数据默认不上传；原始结果数值与 BD-rate 登记在 [实验结果数据](../docs/experiments/Experiment_Results.md)，分析另记在 [实验分析](../docs/experiments/Experiment_Analysis.md)，两份文档上传 Git。

当前 4×4 GTT 登记为实验 1，目录为 `experiment_001_gtt4x4/`。原始 `results.xlsm` 与 `1.csv` 保留；实验目录中的工作簿为模板副本，CSV 按序列、QP、配置写入 Test 对应行。Reference、VBA 和未涉及的公式保留；清除旧计算缓存，缺失点不补零或自动补测。后续实验使用各自目录，禁止把上个实验的 Test 数值当作新结果。

2026-10-31 收到实验 1 的外部结果，用户确认模板 Reference 为其正式 anchor，配置、序列、QP、帧数及采样规则一致；实际服务器配置和二进制指纹尚未随 CSV 提供。本地本轮只做结果登记和数值复算，没有运行视频编解码。
