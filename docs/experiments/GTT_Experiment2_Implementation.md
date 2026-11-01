# 实验 2：多尺寸 GTT 实现与验证记录

- 日期：2026-11-01；算法修订：`e2-r1`。
- 分支：`experiment/gtt-nx2high`；实现起点：`20bc2fc2d3a073af22d516e89c2a496657030bd6`。
- 固定项目基线：`5da139f22ffe9a281d8d9e4f0b7b4b22cba5326b`，性质及既有修复见 [Baseline_Audit.md](Baseline_Audit.md)。本轮不更新 baseline manifest 或既有修复宏。
- 设计依据：[GTT_Experiment2_MultiSize_Design.md](GTT_Experiment2_MultiSize_Design.md)，`e2-design-r1`。
- 状态：实现已接入；四种 Release 完整构建及相应回归通过，修复后的 ASan/UBSan 合成回归 8/8 通过，48 任务 dry-run 通过。未运行任何序列编解码。

本文记录实际实现、使用方法和验证边界。启动日志的 `revision=e2-r1` 是算法修订名，不是源码提交 SHA。构建所用源码文件指纹、宏覆盖、二进制指纹及日志路径另存于本地 `runs/gtt_experiment2_validation/validation_identity.json` 和 `build_paths.json`；只记录起点提交不能完整标识本次实现。

## 1. 子实验与运行开关

所有入口复用 `getGttMode()` 的同一模式表，尺寸、掩码和分组方式不单独接受命令行自由组合。

| `GTT` / `HHI_GTT` | 子实验 | 尺寸 | `sizeMask` | `groupMode` | G1/G2/G3/G4 系数个数 |
|---:|---|---|---|---:|---|
| 0 | 关闭，不占子实验编号 | — | `0x00` | 0 | — |
| 1 | 8×8 等分 | 8×8 | `0x02` | 0 | 16/16/16/16 |
| 2 | 16×16 等分 | 16×16 | `0x04` | 0 | 64/64/64/64 |
| 3 | 32×32 等分 | 32×32 | `0x08` | 0 | 256/256/256/256 |
| 4 | 16×16 固定首 CG | 16×16 | `0x04` | 1 | 16/80/80/80 |
| 5 | 32×32 固定首 CG | 32×32 | `0x08` | 1 | 16/336/336/336 |
| 6 | 4×4 迁移对照 | 4×4 | `0x01` | 0 | 4/4/4/4 |

源码中的开关位于 [TypeDef.h](../../source/Lib/CommonLib/TypeDef.h)，提交默认值均为关闭；统计宏紧接相应开启宏：

```cpp
#define HHI_GTT4X4 0
#define HHI_GTT4X4_STATS 0
#define HHI_GTT 0
#define HHI_GTT_STATS 0
#define HHI_GTT_COMMON (HHI_GTT4X4 || HHI_GTT != 0)
```

`HHI_GTT=1…6` 同时编译实验 2 编码能力并选择相应运行默认值；同一个已编译二进制可以通过 `GTT=1…6` 切换子实验，也可以用 `GTT=0` 关闭。`HHI_GTT=0` 时默认关闭，显式请求开启实验 2 会报错。统计默认值独立由 `HHI_GTT_STATS` 决定，`GTTStats=0/1` 可以覆盖；统计启用不会自动开启算法。

原实验 1 仍使用 `GTT4x4`、`GTT4x4Stats`。两个编码入口不能同时启用；两个编译宏同时非零可以构建，但必须通过配置或命令行关闭其中一个入口。`HHI_GTT_COMMON` 控制共享算法及 v2/v3 解码能力，不替代两个编码入口各自的能力检查。

配置与命令行按读取顺序生效，后读覆盖先读。覆盖参数应放在相关 `-c` 之后。例如，下列仅为参数排列片段：

```text
-c cfg/encoder_randomaccess_nx2High.cfg -c cfg/experiments/gtt_exp2_1.cfg --GTT=3 --GTTStats=0
```

此例最终选择子实验 3、关闭统计。六份 `cfg/experiments/gtt_exp2_<编号>.cfg` 均明确写入 `GTT4x4: 0`、对应 `GTT` 编号和 `GTTStats: 1`。参数解析拒绝不完整整数、非法统计值和最终不在 0…6 内的模式；已经被后续合法值覆盖的整数模式不会提前触发最终配置检查。

启用时检查 DQ=1、RDOQ/RDOQTS 开启、符号隐藏关闭、缩放列表关闭、正常有损配置、内部亮度 10 bit、变换动态范围 15，以及所选尺寸不超过实际最大 TU 等条件。未启用尺寸不增加 GTT 搜索、规范零候选或 flag=0。

启动身份行包含以下字段，最终状态以它为准：

```text
EXPERIMENT: GTT experiment=2 sub=1 compile=1 default_sub=1 enabled=1 version=3 sizes=8 mask=0x02 groupMode=0 component=Y DQ=1 kernel=I,R724 flag=context-by-size signPred=local-off stats_default=0 stats=1 dct=int64-partial-butterfly revision=e2-r1
```

这是字段示例，`stats=1` 表示已由配置覆盖默认值。`compile` 是实验 2 编码能力，`default_sub` 是宏默认编号，`sub` 和 `enabled` 是最终运行状态。日志还保留原 `GTT4x4` 身份行，两者分别解释。

## 2. 共同算法与宽幅 DCT

实验 2 对完整亮度方形 TU 做对应尺寸的整数 DCT-II，再通过工程原 `SCAN_GROUPED_4x4` 对角扫描聚集系数，按模式表分为四组。它没有把大 TU 拆成独立空间 4×4 变换。

`Gtt.h/.cpp` 提供布局、四条 I/R724 路径、扫描聚集/散回、最终等级奇偶推导和通用量化结果结构。旋转以 64 位完成乘加，采用 Q10 系数 724，负半整数按远离零方向舍入，检查 `TCoeff` 范围，不额外裁剪到 15/16 bit。R724 不是严格整数可逆映射，因此没有将“任意整数正逆无损往返”当作测试标准。

`GttDct.h/.cpp` 的正式核是 64 位递归 partial butterfly。它复用既有整数 DCT 矩阵、位移、舍入和逆变换阶段裁剪，通过对称和反对称分解减少乘加，不经过旧 SIMD 的 int16 饱和打包。测试中的稠密 64 位矩阵点积仅充当独立参考。当前生产核是标量实现，尚无速度收益结论。

宽幅核覆盖 4/8/16/32：前向第一、第二阶段分别使用 `log2(N)+1`、`log2(N)+6` 位移；逆向分别为 7、10，保留第一阶段动态范围裁剪和最终 `Pel` 裁剪。逆端接受设计要求的系数幅度 55929。测试覆盖完整 `Pel` 范围以及边界、常数、棋盘、脉冲和随机输入。

`TrQuant` 按 SPS 版本分派：v2 保留冻结的原 4×4 路径；v3 使用通用布局和宽幅 DCT。逆端先对完整 TU 完成既有 DQ 反量化，再从最终量化等级推导路径并逆旋转、逆 DCT。子实验 6 是通用 v3 的迁移对照，不改写实验 1 已发布的 v2 身份。

## 3. 多 CG 受约束 DQ 与搜索

多 CG 求解保留原 DQ1 四个物理状态，并增加当前逻辑组奇偶，共八个联合槽。当前、前一位置和 CG 入口 skip 快照各自持有 State bank；`historySlot` 与物理状态号分离，使同一物理态的两个奇偶分支有独立邻域历史。真实 SOC/EOS、CG 率代价及 CommonCtx 两代历史按原普通 DQ 的时序更新。

整 CG skip 从入口快照跳过 16 个零，保留物理状态及累计奇偶，在逻辑组边界才校验、重置奇偶。trace 显式区分普通一步和整 CG 跳转，扫描位置使用足以覆盖 1024 系数的类型。每次调用重新初始化量化器、估计器、历史、有效位和回溯，不复用另一候选的临时状态。

4×4 布局继续调用冻结的旧求解器。新多 CG 初始化根据实际最大源系数估计 PQ 最大索引，包含近零分支、裁剪及四个相邻候选的上界；按 64 位乘积与舍入界选择代理精度，进入 DP 前检查累计代理代价范围。独立审查与差分验证定位到最初使用过粗乘积界会改变部分 32×32 低幅源的代理舍入，已改为实际源幅度决定的 PQ 上界。修复后的无奇偶约束检查与原普通多 CG DQ 逐项一致；旧 4×4 初始化数值仍保持冻结。

每个适用搜索调用只计算一次基础 DCT，生成四条独立路径；按最后非零所在逻辑组 τ 枚举 `1+2+4+4=11` 类非零约束。DQ 的代理代价用于各约束内部求解，最终候选交回 Intra/Inter 原有的重建、失真和 CABAC RD 评分流程。规范全零候选与非零 GTT 分开处理；严格更低代价才替换已有最优候选。

搜索接线按候选恢复入口 CABAC 状态，获选结果恢复对应 TU、系数、重建、CBF、变换及上下文状态。当前完整 Intra/Inter 搜索的胜者恢复只有源码审查证据；合成 TU 测试没有执行真实帧级模式决策，不能据此声称该闭环已验证。

## 4. 协议与 flag 时序

实验 2 使用 SPS 私有扩展版本 3，发送 8 bit 尺寸掩码和 1 bit 分组方式。子实验编号由这些参数表达，不额外写进 TU。空掩码、保留的高四位、固定首 CG 与 4×4 的非法组合及未知版本均显式拒绝。

v2 的原 4×4 payload 与语义保持不变。编译了 `HHI_GTT_COMMON` 的新解码器支持 v2/v3；两个算法宏全零时拒绝相应扩展。旧 v2 解码器不能解码 v3，不把新增协议称为与旧二进制兼容。

TU 仍只发送一个专属上下文编码的 GTT flag。v3 按 4/8/16/32 使用四个独立 context，初始参数与实验 1 相同；新增 context 不移动旧 context 的编号。Writer、码率估计器及 Reader 使用一致的尺寸选择。

flag 位于 TS 判定之后、last 之前，也早于后续 NST/MTS 的确认。满足公共资格的非 TS 旧候选发送 0，包括可能继续采用 NST/MTS 的候选；flag=1 才限定固定 DCT-II 并关闭相应 GTT 亮度 NST/MTS、符号预测及延迟符号处理。CBF=0、TS、不满足公共资格或尺寸未开启时不发送 flag。公共资格排除矩形、IBC、PLT、BDPCM、SBT、noResidual 等情况，仅作用于亮度。

sanitizer 验证还暴露了原 `CABACReader::residual_coding_subblock` 的零符号边界：合法的空首 CG 可使 `numSigns=0`，原表达式会对 `decodeBinsEP(0)` 的结果左移 32 位，产生未定义行为。此次必要安全修复在产品源码中为 `numSigns==0` 增加保护，使该分支保持零值、不读取符号 bin；不改变合法语法、修复宏或 baseline manifest。该修复及复验结果在下表单独追踪。

## 5. 统计口径

`GTTStats=1` 时按 EncLib/layer 和尺寸输出统计；无样本的比例写 `N/A`。统计分为最终提交结构/真实 Writer 的计数，以及搜索过程诊断，两类人口不能混用。

### 最终结构和真实 flag

| 输出字段 | 含义与分母 |
|---|---|
| `total`、`zeros`、`ones`、`percent` | 真实 Writer 实际写出的 flag 数；`percent=100×ones/total`，不是全部 TU 的使用率 |
| `estimated_bits0/1` | 在真实 `encodeBin` 之前读取当前 context 的分数比特，分别累计 0/1；是估计成本，不是算术编码器输出字节的精确分摊 |
| `tu_count` | 最终结构中该尺寸亮度 TU 数，在 skip/root-CBF 等提前返回之前收集 |
| `eligible_count/area` | 满足公共资格的最终 TU 数及面积，尚未扣除 CBF=0 或 TS |
| `flag_area`、`gtt_area` | 分别为 `total×N²`、`ones×N²` |
| `luma_samples` | PPS 编码亮度网格累计样本数；所有尺寸行复用同一分母，不能跨行相加，也不是裁剪后显示区域面积 |
| `tau1…tau4`、`path_II/IR/RI/RR` | 最终非零 GTT 的终止组及有效前缀路径计数（未激活后缀核归 I） |
| `prefix_r_tus/area` | 有效前缀中选择了 R 的最终 TU 数/面积；不等于输出系数确实发生变化 |
| `nonzero_levels` | 最终 GTT 中非零量化等级数 |
| `last_cg_histogram` | 最终 GTT 的最后非零位置按 CG 聚合后的直方图 |

观察器只在真实 Writer 的 `isEncoding()` 路径收集 flag，读取 context 不推进概率状态，原 `encodeBin` 仍只更新一次。最终 TU 计数取已提交结构，避免混入 RDO 临时调用和双树色度。`area_grid=pps-coded-luma` 随统计身份行输出，方便后续计算各类面积覆盖率。

### 搜索诊断

`GTT_SEARCH_STATS` 输出 `calls`、`dq_calls`、`feasible`、`scored`、`comparisons`、`local_wins`、`dct_ms/dq_ms/score_ms` 和 `delta_j_sum`，标明 `population=search_calls`。`delta_j_sum` 是同一次预测/搜索条件下“最佳非零 GTT 的完整局部 RD 代价减旧候选代价”的累计值，只累计两者均有有限代价的比较；负值表示这些局部比较的总差值有利。`local_wins` 不等于最终被整帧搜索选中的 GTT TU 数，也不等于序列级收益。

`GTT_SEARCH_ENERGY` 的 `g1_ratio` 为所有相关搜索调用的 G1 基础 DCT 平方能量之和除以全部基础 DCT 平方能量之和，即能量加权比例。它既不是每 TU 比例的算术均值，也不是最终获选 TU 的能量分布；日志明确写入 `weighting=base_dct_energy`。

这些时间仅覆盖被插桩的调用，不等同于总编码时间或算法加速比。统计关闭时搜索计时器不读取时钟。正式性能比较应使用 `GTTStats=0`，并另行登记相同条件的 anchor。当前没有输出“实际系数变化率”或完整系数/符号码率拆分，不从已有计数推造这些指标。

## 6. 代码位置

| 范围 | 主要文件 |
|---|---|
| 宏、默认值、解析与身份 | `CommonLib/TypeDef.h`、`EncoderLib/EncCfg.h`、`App/EncoderApp/EncAppCfg.cpp` |
| 模式、布局、旋转、宽幅 DCT | `CommonLib/Gtt.h/.cpp`、`CommonLib/GttDct.h/.cpp` |
| 共同量化与逆变换 | `CommonLib/DepQuant.h/.cpp`、`CommonLib/GttQuantRouting.h`、`CommonLib/TrQuant.h/.cpp` |
| SPS 与资格 | `CommonLib/SequenceParameterSet.h`、`Unit.h/.cpp`、`UnitTools.h/.cpp`、两端 `VLCReader/Writer.cpp` |
| context、TU 语法和真实统计 | `CommonLib/Contexts.h/.inl`、两端 `CABACReader/Writer.h/.cpp` |
| 搜索与状态恢复 | `EncoderLib/GttSearch.h/.cpp`、`IntraSearch.h/.cpp`、`InterSearch.h/.cpp`、`EncCu.cpp` |
| 统计归属及输出 | `EncoderLib/EncLib.h/.cpp`、`GttSearch.h/.cpp` |
| 合成测试与解析探针 | `tests/gtt/`、保留回归的 `tests/gtt4x4/` |
| 配置和任务预览 | `cfg/experiments/gtt_exp2_1.cfg` 至 `gtt_exp2_6.cfg`、[任务预览 CSV](GTT_Experiment2_jobs_preview.csv) |

表内库路径相对于 `source/Lib/`，应用路径相对于 `source/`。旧 `Gtt4x4` 算法文件继续保留，以共享编译 guard 支持旧协议及迁移检查。

## 7. 构建和 dry-run 用法

以下命令用于用户准备环境，不包含序列编解码。先在专用构建工作副本中将 `TypeDef.h` 的 `HHI_GTT` 设为 1（或其它已登记非零编号），保持 `HHI_GTT4X4=0`，按需要设置统计默认值。仓库提交的默认值仍为 0。

```bash
cmake -S . -B build/gtt-e2 -DCMAKE_BUILD_TYPE=Release \
  -DNX2_ENABLE_LINK_TIME_OPT=OFF \
  -DGTT4X4_BUILD_TESTS=ON -DGTT_BUILD_TESTS=ON
cmake --build build/gtt-e2 --target EncoderApp DecoderApp Gtt4x4UnitTests GttUnitTests GttConfigProbe -j 4
ctest --test-dir build/gtt-e2 -R '^Gtt' --output-on-failure
```

`GTT_BUILD_TESTS` 和 `GTT4X4_BUILD_TESTS` 只决定是否构建测试，不设置算法宏。这里关闭 LTO 仅用于构建核验；该构建不自动成为正式计时配置。命令沿用工程默认输出位置 `bin/release/`、`lib/release/`；不同宏配置必须使用独立源码工作副本，在运行期间不得修改其源、配置或二进制。

真实配置解析可以独立测试，探针只构造参数解析器，不初始化视频编码流程：

```bash
python3 tests/gtt/ConfigTests.py \
  --probe bin/release/GttConfigProbe --repo-root . \
  --output runs/gtt_experiment2_validation/config_local
```

已有 `summary.json` 的输出目录不会覆盖；重测时选择新目录。新任务预览使用六个子实验、RA/LB、BasketballDrill、QP 22/27/32/37、8 帧，共 48 个展开任务。这仅用于检查分组、参数与输出路径，8 帧不是正式实验配置，也没有因此获得运行授权。

```bash
python3 scripts/batch_test.py \
  --manifest docs/experiments/GTT_Experiment2_jobs_preview.csv \
  --encoder bin/release/EncoderApp --decoder bin/release/DecoderApp \
  --input-dir /absolute/path/to/yuv --out-dir runs/gtt_exp2_preview \
  --jobs 4 --dry-run --allow-missing-input --no-xlsm-report
```

该命令只生成计划。六个子实验以 `exp2_1`…`exp2_6` 分组，共享任务池；`xlsm_tag` 保留 RA/LB 配置标签。预览明确开启统计；正式计时应建立统计关闭且身份明确的任务清单。既有调度器默认不保存重建，后续实际运行和续跑仍要求输入、配置、二进制指纹及成功证据匹配。

## 8. 验证结果与边界

本地证据目录为 `runs/gtt_experiment2_validation/`，不作为正式实验数据上传。四个完整 Release 构建的最终源码快照与工作树逐文件核对哈希，仅有记录在案的算法宏覆盖及生成 `version.h` 差异。下表的 Release 和 sanitizer 最终结果均包含零符号保护及测试夹具修复；完整日志为 `ctest_<enabled/disabled/legacy/both>_zero_sign_fix.log` 与 `ctest_sanitized_final.log`，构建身份和二进制指纹记录在 `validation_identity.json`。

| 检查 | 当前状态与实际范围 |
|---|---|
| 开启配置的首轮合成回归 | 原 4×4 与通用 Common/Quant/Syntax/Search 共 8 套通过；不是序列运行 |
| 最新共同算子检查 | 68,829 项通过，覆盖布局、扫描、旋转、路径、旧 4×4 对照及宽幅正式核与独立矩阵参考 |
| 最新量化检查 | 168,344 项通过，含多 CG 路由/回溯、无约束原 DQ 差分、两种分组、正反 last、宽幅 DCT 多路径与不同 QP 合成输入 |
| 最新语法与搜索检查 | 通用 Syntax 596 项、Search 4,657 项通过；旧 Common 152 项、Quant 17,278 项、Search 656 项及旧 Syntax 套件通过 |
| 默认子实验 1 的严格解析检查 | 55/55 通过，真实 `EncAppCfg` 路径，不运行编码器 |
| 宏默认 2/3/4/5 的严格解析检查 | 每份 55/55 通过；分别编译真实解析器对象和探针，复用静态库，不是四份完整编解码库构建 |
| Release：旧宏 0 / 新宏 1 / 新统计宏 0 | 完整构建成功，9 套通过：8 套合成测试与 55 用例参数解析 |
| Release：旧宏 0 / 新宏 0 / 新统计宏 0 | 完整构建成功，3 套通过、6 套算法按返回码 77 跳过；共同模式表执行 9 项检查后报告 skip，未将关闭算法算作执行通过 |
| Release：旧宏 1 / 新宏 0 / 新统计宏 0 | 完整构建成功，9 套通过；包含旧入口默认值及新入口不可用检查 |
| Release：旧宏 1 / 新宏 6 / 新统计宏 1 | 完整构建成功，9 套通过；包含运行入口互斥和覆盖检查 |
| ASan + UBSan、Debug、SIMD 关闭 | 独立副本构建成功，修复后 8/8 套合成回归通过；验证专用基线补丁及运行选项见下文 |
| sanitizer 触发的修复 | `GttSyntaxTests` 补全夹具 `cs.pcv`；产品 `CABACReader` 增加 `numSigns==0` 的零符号保护；Search 回归明确检查合法空首 CG 确被覆盖 |
| 48 任务 dry-run | 已执行并通过；六个子实验 × RA/LB × 四个 QP，默认不保存重建；日志 `dry_run.log`，没有启动编解码进程或读取视频 |
| 统计开关不改变候选/局部码流 | 有合成检查；真实序列统计开关的 bit-exact 尚未验证 |
| 完整 Intra/Inter 胜者恢复 | 已审查源码；真实帧级闭环未运行 |
| 序列编解码、hash 闭环、正式 BD-rate、速度 | 均未执行、无结论 |

四个完整构建各执行 55 项参数解析，加上默认 2…5 的四份解析对象构建，共覆盖八种宏默认组合、440 项参数检查。它们验证宏默认、命令行/配置覆盖、非法值、能力与互斥检查；不能据此写成八种完整编解码构建。

sanitizer 副本应用本地 `sanitizer-baseline-only.patch`，处理基线两个文件中影响验证的既有问题：`TrQuant_EMT.cpp` 标量分支变量名，以及 `Rom.cpp` 负数左移。补丁仅用于独立验证副本，工作树中的这两个基线文件没有因此修改；本验证不能写成“未改动基线的全流程 sanitizer 通过”。使用 `-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer`，关闭 SIMD 和 LTO，运行设置为 `ASAN_OPTIONS=detect_leaks=0`、`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`。因当前运行环境的 LeakSanitizer 限制关闭泄漏检测，本记录不作无内存泄漏结论；参数探针另由 Release 检查覆盖。

dry-run 只展开并打印任务，不探测二进制的实际启动日志；其中的 `EXPERIMENT: none` 不代表 GTT 被关闭。实际启用身份须由后续获准执行的编码日志确认。

## 9. 正式结果登记边界

实验 2 的正式 Current anchor 尚未登记。实验 1 的 Reference 已获准作为实验 1 anchor，不自动成为实验 2 的正式对照。后续先确认源码/二进制、配置、序列、QP、帧数及采样规则，再按已授权范围运行。

结果沿用 `experiments/experiment_002_gtt_sizes/` 下的 `anchor/` 和 `1/`…`6/` 独立目录，每个子实验各有工作簿与 CSV，避免多个子实验写同一 Test 页。按准确序列/QP/配置匹配导入，保留 Reference/VBA，缺点留空并清除旧计算缓存。原始表格、码流、大日志和视频不上传 Git。

实验身份、原始结果、各分量及 6:1:1 加权 BD-rate 记入 [Experiment_Results.md](Experiment_Results.md)，解释与机制分析另记入 [Experiment_Analysis.md](Experiment_Analysis.md)。当前文档中的局部代价、能量、合成检查和任务数量均不是正式压缩性能结果。
