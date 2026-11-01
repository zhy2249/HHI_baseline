# 视频编码基线项目协作约定

- 本项目是 NextSoftware2 的 fixed baseline 快照，不宣称未经修改的上游原版。先读 `docs/experiments/Baseline_Audit.md`。
- 修改前核对工作树、基线 manifest、实验需求和源码。不将其它仓库的宏、实验编号、已有 anchor 或结论直接套入本项目。
- 实验不限于 TS。根据编码器搜索、共同算法、语法、状态、统计等实际改动选择检查项；只有隐式共同推导要求信息在两端同步可得。
- 算法实验开关优先直接放入 `source/Lib/CommonLib/TypeDef.h`，默认关闭；同一工具互斥实验须显式检查，运行日志须能确认身份。CMake 只负责构建，不暗中决定实验模式。
- 配套统计宏紧接对应工具开启宏；工具和统计的运行默认值分别由各自宏决定，配置或命令行可显式覆盖默认值。
- 不擅自切换既有修复宏或更新 baseline manifest。实验关闭的恢复目标须明确，统计不能污染真实决策。
- 未获授权不运行序列级编解码或完整 CTC。短帧仍属于编解码。编译、脚本单测与 dry-run 不冒充码流验证。
- 复用 `scripts/batch_test.py`；默认不输出重建，实验共享任务池，分组即时出表，续跑要求指纹及成功证据一致。不要在运行期间修改二进制、配置或输入。
- 项目文档放 `docs/experiments/`，外部输入放 `docs/external_inputs/`，结果收件放 `experiments/`，本地运行输出放 `runs/`。外部原文保留，不当作已核验事实。
- 本仓库后续实验设计文档完成后，按用户授权直接提交并推送到对应实验分支的 `docs/experiments/`，必要时更新该目录 `README.md` 索引，无需逐次确认。当前 GTT 实验 2 对应分支为 `experiment/gtt-nx2high`。此约定仅覆盖实验设计文档及必要索引更新，其他上传继续按用户已明确授权的范围执行。
- 后续正式实验先登记自己的 Current anchor 身份、配置、序列、QP、帧数与采样规则。旧项目工作簿只能在可比性确认后引用；不自动补点。
- 结果登记保存每序列每 QP 码率与 Y/U/V PSNR；先分别计算分量 BD-rate，再以 6:1:1 加权。序列均值与类别均值分开标记；其他指标须另行约定。
- 维护 `docs/experiments/Experiment_Results.md`（实验身份、原始结果、BD-rate）和 `docs/experiments/Experiment_Analysis.md`（对结果的分析），两者分开并上传 Git。当前 4×4 GTT 为实验 1。各实验表格和 CSV 放 `experiments/experiment_<三位编号>_<名称>/`，默认不上传；复制 `results.xlsm` 模板到实验目录后，按序列、QP、配置匹配 CSV 与 Test 行，保留 Reference 和 VBA，缺点留空并清除旧计算缓存，后续实验沿用此流程。
- 保留用户变更。Git 上传按用户已明确授权的范围执行，不自动上传原始视频、码流、工作簿、重建、大日志、构建产物和私密配置；不强推、不改写历史。
