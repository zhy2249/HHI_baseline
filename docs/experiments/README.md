# 实验文档索引

本目录记录此独立 fixed baseline 的核验、实现、验证和后续实验。它不导入旧项目的实验结论或参考曲线。

- [基线核验](Baseline_Audit.md)：接收状态、源码身份、补齐内容、已测和未测范围。
- [构建与运行准备](Getting_Started.md)：可复制命令与续跑约定。
- [基线文件指纹](baseline_manifest.json)：源码、配置、依赖和模型的可移植 SHA256 清单。
- [GTT4x4 nx2High 设计审查](GTT4x4_nx2High_Design_Review.md)：外部方案的源码核对、实施补充、优化顺序与待验收项。
- [GTT4x4 专用上下文实现](GTT4x4_Implementation.md)：版本 2、最终 flag 占比日志、构建与合成验证、任务预览。
- [实验 2：多尺寸 GTT 设计与 Codex 实施规格](GTT_Experiment2_MultiSize_Design.md)：子实验1/2/3为8×8/16×16/32×32等分，4/5为16×16/32×32固定首CG对照，6为4×4迁移检查；宏与CLI按编号选择。
- [实验 2：实施与验证记录](GTT_Experiment2_Implementation.md)：`e2-r1`的多CG量化、宽幅变换、v2/v3语法、统计及构建/合成验证；六子实验完整CSV结果已于2026-11-04登记。
- [实验 2：任务预览](GTT_Experiment2_jobs_preview.csv)：六子实验、RA/LB、四QP的48任务dry-run清单；8帧仅为规划示例，不是正式实验帧数。
- [实验 2-1：数据复核与后续实验设计](GTT_Experiment2_1_Review_and_Followup_Design.md)：基于2026-11-02批次的独立复算、II/R与额外全零候选分析、成对试验及新核/门控设计快照；原文保留，最新六模式数据见下方汇总。
- [实验结果数据](Experiment_Results.md)：各实验身份、原始结果、整体及逐QP BD-rate；保留实验1和2_1历史批次，新增六子实验528点的完整汇总。
- [实验2完整数值明细（2026-11-04）](GTT_Experiment2_Results_20261104.md)：六子实验528实测点、88共同Reference点、逐序列/分量/类别/逐QP指标及分组机制直接对照。
- [实验分析](Experiment_Analysis.md)：保留2_1日志分析，新增六子实验横向比较、分量抵消、QP差异、分组直接对照和4×4迁移诊断。

后续新增实验沿用两份文档登记；表格和 CSV 放在 `experiments/experiment_<三位编号>_<名称>/`，保留本地，不上传 Git。
