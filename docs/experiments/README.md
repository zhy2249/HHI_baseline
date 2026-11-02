# 实验文档索引

本目录记录此独立 fixed baseline 的核验、实现、验证和后续实验。它不导入旧项目的实验结论或参考曲线。

- [基线核验](Baseline_Audit.md)：接收状态、源码身份、补齐内容、已测和未测范围。
- [构建与运行准备](Getting_Started.md)：可复制命令与续跑约定。
- [基线文件指纹](baseline_manifest.json)：源码、配置、依赖和模型的可移植 SHA256 清单。
- [GTT4x4 nx2High 设计审查](GTT4x4_nx2High_Design_Review.md)：外部方案的源码核对、实施补充、优化顺序与待验收项。
- [GTT4x4 专用上下文实现](GTT4x4_Implementation.md)：版本 2、最终 flag 占比日志、构建与合成验证、任务预览。
- [实验 2：多尺寸 GTT 设计与 Codex 实施规格](GTT_Experiment2_MultiSize_Design.md)：子实验1/2/3为8×8/16×16/32×32等分，4/5为16×16/32×32固定首CG对照，6为4×4迁移检查；宏与CLI按编号选择。
- [实验 2：实施与验证记录](GTT_Experiment2_Implementation.md)：`e2-r1`的多CG量化、宽幅变换、v2/v3语法、统计及构建/合成验证；子实验1服务器结果已于2026-11-02登记。
- [实验 2：任务预览](GTT_Experiment2_jobs_preview.csv)：六子实验、RA/LB、四QP的48任务dry-run清单；8帧仅为规划示例，不是正式实验帧数。
- [实验 2-1：数据复核与后续实验设计](GTT_Experiment2_1_Review_and_Followup_Design.md)：独立复算、II/R 与额外全零候选的机制分析、16×16 成对试验、编码侧消融及新核/门控方向；后续方案尚未运行。
- [实验结果数据](Experiment_Results.md)：各实验身份、逐序列/配置/QP 原始结果、整体及逐QP BD-rate 和缺失状态；含实验1及实验2_1的78个实测点、日志核验与GTT统计。
- [实验分析](Experiment_Analysis.md)：单独维护对结果的分析、解释与后续建议；已登记实验2_1的配置/序列/QP差异、有效旋转覆盖和搜索成本分析。

后续新增实验沿用两份文档登记；表格和 CSV 放在 `experiments/experiment_<三位编号>_<名称>/`，保留本地，不上传 Git。
