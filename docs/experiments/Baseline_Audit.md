# 后续编码实验基线核验

核验日期：2026-10-01。对象为用户指定的 `HHI_baseline_fixed_2` 目录。

## 结论与限制

适合作为后续实验的独立 fixed baseline 起点：源码、常规 AI/RA/LB 配置、SADL/SIMDe 依赖、NNLF 模型和构建文件齐全，独立 Release 构建成功。此次只补齐运行保障、记录和仓库组织，未修改编解码算法、修复宏、模型或编码配置。

不能称为已经证明的上游原版，也不能直接认定与旧项目 Current 的编码结果相同。目录接收时没有 Git 历史；上游精确 revision 不明。本次没有运行任何视频编解码，没有 bit-exact、hash 闭环或 BD-rate 结论。后续应先冻结本仓库提交，再核验既有 anchor 的可比性；未获授权不自动重跑 anchor。

## 源码与已有修复

根 README 标明 NextSoftware2 及 JVET-AN0267 来源；COPYING 和第三方许可证保留。

`source/Lib/CommonLib/TypeDef.h` 中已有 `ENABLE_NNLF=1`，`JVET_TRANSSION_DEBUG=0`，`JVET_TRANSSION_VALGRIND=1`，`JVET_TRANSSION_BUGFIX=1`，`JVET_TRANSSION_IMPROVE=1`。这些不是本次增加的实验宏，不应为了“原版”而随意关闭。

- `IntraPrediction.cpp`：VALGRIND 分支初始化代价 pair，并在索引 neighborFlags 前检查范围。
- `EncCu.cpp`：BUGFIX 分支在若干提前退出路径恢复 EIP LUT。
- `DecLib.cpp`：IMPROVE 分支影响解码耗时输出条件。
- 未发现旧 TS predictor 实验的 `JVET_BJUT` 系列实现；NNLF 采用 `int16_t` 类型。

辅助比较使用另一份本地仓库初始提交 `16f3250`，仅作来源线索，不把它当上游权威快照：

| 范围 | 核验结果 |
| --- | --- |
| source | 322 个受版本控制文件均存在；14 个文件有内容差异，主要移除了当时关闭的 GEO/SGPM、浮点 NNLF 和 post-CfE 条件分支 |
| cfg | 408 个文件均存在；358 个仅 CRLF/LF 不同，归一化后无内容差异 |
| cmake / thirdparty / models | 对照的 11 / 527 / 2 个文件均存在且字节一致 |
| 根 CMakeLists | 字节一致 |

上述数量包括本地原始目录中随附的示例密钥；远端有意不收录私钥，故 Git 中文件数略少。编译所需文件不依赖这些示例密钥。签名类 SEI 测试若需要密钥，须单独按 keystore 原文在本地准备，不能使用示例私钥做生产签名。

## 本次补齐内容

- 建立通用 AGENTS、文档索引、外部材料和实验结果收件目录。
- 增加可移植 baseline manifest 及检查器，检测源码、配置、依赖和模型变化；忽略自动生成 version.h，文本 CRLF/LF 归一化。
- 复用旧 `batch_test.py` 和 Excel 辅助脚本，没有导入历史 TS 实验模式表。
- 默认 `-o ''` 关闭编码重建输出；解码校验输出到 `/dev/null`。
- 实验可通过 CSV 的独立 encoder/decoder 路径共用一个任务池，不设置实验组间完成屏障；每组结束立即生成其 summary 和工作簿副本。
- 成功标记绑定二进制和配置 SHA256、命令、输入元数据及输出证据；残缺文件、缺少必要 hash、帧数不符均不作为成功。冲突不会静默覆盖。
- 修复解码日志错误优先级，失败任务独立记录；保存完整计划、逐任务身份和逐次完成进度。
- Excel 只写输出副本，清空 Test 原测量值以防继承旧数据；Reference、VBA 和原有计算结构保留。不计算/伪造缺失点。
- 排除工作簿、视频、码流、日志、旧构建、IDE 状态与私钥；所有原有文件仍保留本地，不做清理删除。

## 验证边界

在排除旧 build/bin/lib、工作簿和私钥的独立临时副本中，用 CMake 3.22.1、GNU C++ 11.4.0、Release、C++17、4 路并行构建 EncoderApp 和 DecoderApp，成功。仅为编译核验显式关闭 LTO，其余构建和算法默认设置不变；不把该二进制当性能计时基准。

16 项脚本单元测试通过，使用合成小文件及 mock 子进程，不启动编解码器。测试覆盖无重建、成功续跑、指纹变化、输出篡改、截断帧数、缺失 hash、错误日志、分组报告，以及 Excel 模板只读、Reference/VBA 保留和未测/失败数值清空。2,486 个基线文件的归一化指纹检查通过；编译副本的 source 与接收目录逐文件字节一致。

LB CE dry-run 得到 7 序列 × 4 QP = 28 任务。脚本读取原有 LBeu INI 的 framecount，实际为半帧：BasketballDrill/PartyScene 250、BQMall 300、RaceHorsesC 150、FourPeople/Johnny/KristenAndSara 300。不是时间抽样，也不再额外除以二。

尚未验证：视频编码和解码正确性、与旧 anchor 的 bit-exact、所有工具组合、Windows 构建、正式长跑恢复和性能。脚本单测不能替代这些验证。

## 继续使用前的约束

完整视频、可信的参考工作簿和正式结果本就应独立保存，不视为缺失源码。旧服务器二进制不要直接作为本次新构建证明；旧工作簿也不能仅凭名字沿用。先核验源码/修复、模型、配置、输入、帧数、采样、QP 和构建模式。

不默认支持同一输出目录的两个调度器同时写入；每次运行独占输出目录。大视频默认按路径、大小和 mtime 识别，不逐字节哈希；正式实验应保持只读输入，并另存数据集校验清单。额外外置模型/文件需用 `--identity-file` 声明。动态库和运行环境仍需实验记录，不声称一个任务指纹覆盖全部机器状态。
