# 实验结果收件目录

按实验编号建立 `experiment_<三位编号>_<名称>/` 子目录，接收服务器 CSV、工作簿等原始结果。表格数据默认不上传；原始结果数值与 BD-rate 登记在 [实验结果数据](../docs/experiments/Experiment_Results.md)，分析另记在 [实验分析](../docs/experiments/Experiment_Analysis.md)，两份文档上传 Git。

当前 4×4 GTT 登记为实验 1，目录为 `experiment_001_gtt4x4/`。原始 `results.xlsm` 与 `1.csv` 保留；实验目录中的工作簿为模板副本，CSV 按序列、QP、配置写入 Test 对应行。Reference、VBA 和未涉及的公式保留；清除旧计算缓存，缺失点不补零或自动补测。后续实验使用各自目录，禁止把上个实验的 Test 数值当作新结果。

2026-10-31 收到实验 1 的外部结果，用户确认模板 Reference 为其正式 anchor，配置、序列、QP、帧数及采样规则一致；实际服务器配置和二进制指纹尚未随 CSV 提供。本地本轮只做结果登记和数值复算，没有运行视频编解码。

2026-11-02 收到实验 2_1（8×8 等分 GTT）的 `2_1/` 原始目录，保留不动；已将 CSV 副本、填好的 `results.xlsm`、BD-rate 及统计审计存入 `experiment_002_gtt_sizes/1/`。用户确认本次继续使用当前 Reference，测试范围一致。登记 78 个实测点（AI 28、RA 22、LB 28），RA 不完整点不补齐；详细数值与解释分别见上述两份文档。工作簿及原始日志仍不上传 Git。

2026-11-04 收到 `experiment_002_gtt_sizes/1.csv`…`6.csv`，每个子实验88点（AI28、RA32、LB28），用户确认同一Reference、测试范围一致且仅切换子实验。每个编号的 `batch_20261104/` 保存新CSV副本、`results.xlsm`、`bd_rate.json` 和填写核验；共同Reference快照在 `anchor/batch_20261104/`，横向与独立核验资料在 `summary_20261104/`。旧2_1的78点及工作簿原样保留，本次仅新增其10个RA点。新批只有CSV，旧日志统计不自动扩展到新增点或其它模式。上传数值见 [完整明细](../docs/experiments/GTT_Experiment2_Results_20261104.md)；工作簿、CSV和本地派生文件仍不上传。
