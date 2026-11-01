# 实验文档索引

本目录记录此独立 fixed baseline 的核验、实现、验证和后续实验。它不导入旧项目的实验结论或参考曲线。

- [基线核验](Baseline_Audit.md)：接收状态、源码身份、补齐内容、已测和未测范围。
- [构建与运行准备](Getting_Started.md)：可复制命令与续跑约定。
- [基线文件指纹](baseline_manifest.json)：源码、配置、依赖和模型的可移植 SHA256 清单。
- [GTT4x4 nx2High 设计审查](GTT4x4_nx2High_Design_Review.md)：外部方案的源码核对、实施补充、优化顺序与待验收项。
- [GTT4x4 专用上下文实现](GTT4x4_Implementation.md)：版本 2、最终 flag 占比日志、构建与合成验证、任务预览。
- [实验 2：多尺寸 GTT 设计与 Codex 实施规格](GTT_Experiment2_MultiSize_Design.md)：子实验1/2/3为8×8/16×16/32×32等分，4/5为16×16/32×32固定首CG对照，6为4×4迁移检查；宏与CLI按编号选择，包含跨CG量化、宽幅变换、统计及验收计划。当前为设计规格，尚未实现或运行实验2。
- [实验结果数据](Experiment_Results.md)：各实验身份、逐序列/配置/QP 原始结果、BD-rate 和缺失状态；当前 4×4 GTT 为实验 1。
- [实验分析](Experiment_Analysis.md)：单独维护对结果的分析、解释与后续建议。

后续新增实验沿用两份文档登记；表格和 CSV 放在 `experiments/experiment_<三位编号>_<名称>/`，保留本地，不上传 Git。
