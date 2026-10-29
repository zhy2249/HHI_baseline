# GTT4x4 nx2High 设计审查与实施补充

后续用户已指定使用专用 CABAC context 并输出最终 flag=1 占比。当前实现采用协议版本 2，替代本次初始审查中的 EP/version 1 草案；代码、统计和验证状态见[实现记录](GTT4x4_Implementation.md)。以下保留设计审查时的结论与证据。

本记录按用户要求审查原方案并细化实现设计。结论：整块 4×4 分组树变换、最终等级隐式选核、专用受约束 DQ 和语法位置总体自洽，可以作为实现基础；实施前需要补齐帧内全零候选和帧间历史分类两项设计。本文没有实现算法，也没有压缩收益结论。

## 1. 身份与范围

| 项目 | 本次记录 |
| --- | --- |
| 审查日期 | 2026-10-29 |
| 工作目录 | `/home/zhy/HHI/HHI_GTT` |
| 实验分支 | `experiment/gtt4x4-nx2high` |
| 基线提交 | `5da139f22ffe9a281d8d9e4f0b7b4b22cba5326b` |
| 原方案 | [HHI_baseline_GTT4x4_nx2High_Codex_Implementation_Plan.md](../external_inputs/gtt4x4_nx2high/HHI_baseline_GTT4x4_nx2High_Codex_Implementation_Plan.md) |
| 原方案 SHA256 | `e2bb12a04946abd09a8ba45afdaa5f34c13cc8aa85ac2a6d8d48f31576868f12` |
| 本文设计修订 | `design-r1`，尚未形成实现 revision |
| 基线检查 | `python3 scripts/baseline_manifest.py`：PASS，2486 个文件，0 差异 |
| 本轮范围 | 源码核对、公式复算、设计补充；不修改算法、配置和 baseline manifest |

本工程为带既有修复的 fixed baseline。保留 `ENABLE_NNLF=1` 与原 TRANSSION 宏值。工作树已有用户未跟踪文件，保留原样。原方案作为外部输入保留原文，本文记录本地核对及建议。

四套配置已核对：

| 配置 | IntraPeriod | GOPSize | 内部位深 | DQ / SDH / NumSignPred |
| --- | ---: | ---: | ---: | --- |
| `encoder_intra_nx2High.cfg` | 1 | 1 | 10 | 1 / 0 / 4 |
| `encoder_randomaccess_nx2High.cfg` | 32 | 32 | 10 | 1 / 0 / 4 |
| `encoder_lowdelay_nx2High.cfg` | -1 | 8 | 10 | 1 / 0 / 4 |
| `encoder_lowdelay_P_nx2High.cfg` | -1 | 8 | 10 | 1 / 0 / 4 |

四套均启用 RDOQ、RDOQTS、MTS、TS、SBT 和 BIF。正式实验的序列、帧数、采样、输入指纹、构建及二进制身份仍须另行登记；原文的单序列 8 帧仅是预览示例。

## 2. 保留的核心设计

1. 对完整 4×4 亮度 TU 做固定整数 DCT-II，再按已有对角扫描分成四组。K2、K3 采用 I 或 724/Q10 旋转，不拆成四个独立空间块。
2. 从最终传输等级的前两组绝对值和奇偶派生 K2、K3。解码端先对完整 TU 反量化，再逆 K3、逆 K2、逆基础 DCT。没有编码器私有信息参与解码选核。
3. 规范 TU 仅增加亮度 `gttFlag`；路径、终组和代理代价属于编码搜索。`MtsType` 不增加新值，GTT 使用 DCT2 占位并在相关公共入口显式分派。
4. 仅支持 Y 4×4、普通 intra/inter、DQ=1、内部 10 bit、动态范围 15。保留原方案的 SBT/IBC/PLT/BDPCM、缩放矩阵和无损排除条件；其他旧工具保持原配置。
5. 编译宏放 `TypeDef.h`，默认 0；运行开关默认 0。SPS 私有扩展槽 1、版本 1；TU 的 EP flag 放在 TS 后、last 前。实验关闭时不增加 SPS/TU 位。
6. GTT 的亮度在公共符号判断处局部关闭延迟符号及符号预测；必须执行完整原 DQ 反量化。旧候选和色度继续原流程。

这些设计的关键源码依据为 [TrQuant.cpp](../../source/Lib/CommonLib/TrQuant.cpp)、[DepQuant.cpp](../../source/Lib/CommonLib/DepQuant.cpp)、[UnitTools.cpp](../../source/Lib/CommonLib/UnitTools.cpp)、[CABACWriter.cpp](../../source/Lib/EncoderLib/CABACWriter.cpp) 和 [CABACReader.cpp](../../source/Lib/DecoderLib/CABACReader.cpp)。以下行号均针对上述基线提交。

### 2.1 受约束量化

固定核前缀后，对最后非零组 τ 枚举 1+2+4+4，共最多 11 次搜索。每次使用原四物理 DQ 状态乘二种当前组奇偶，双代各八槽；组边界只重置新增奇偶，不重置 DQ、Rice、邻域或 bin 预算。

`State::updateState` 在 `DepQuant.cpp:1333` 按 `decision.prevId` 访问前驱。必须把有效决定中的物理编号改为联合槽编号，同时保持 `State::m_stateId` 为原物理编号。原文的 PQ 映射、near-zero 分支及四态转移与源码一致；单 CG 新路径不需要扩大旧 `CommonCtx`。

求解器只返回合法非零候选；不可行结果不得修改调用者 TU。不能用代理代价与零作提前比较，也不能事后改一个等级修复奇偶。最终评分从等级重新派生路径，并执行真实反量化、整数逆变换和当前入口的失真计算。

## 3. 实施前需要补齐的两项设计

### 3.1 GTT 胜者不能被记为普通 DCT 历史

**源码确认的集成耦合：** [EncCu.cpp](../../source/Lib/EncoderLib/EncCu.cpp) 的 `xEncodeInterResidual` 在 4127 行仅根据 `mtsIdx` 生成 `SBT_OFF_DCT/SBT_OFF_MTS`，4271 行起将结果写入 SBT/MTS 历史。GTT 的 DCT2 占位会被归为旧 DCT。随后 `UnitTools.cpp:10396` 的历史剪枝可能不再加入 inter MTS；控制宏 `APPLY_SBT_SL_ON_MTS` 当前为 1。

这条路径对 4×4 可达：`EncCu.cpp:4038` 的 `mtsAllowed` 只有尺寸上界，即使 `sbtAllowed=0`，`doPreAnalyzeResi` 仍可为真，并满足后续历史登记条件。影响幅度尚未通过执行测量。

**采用的最小设计：**

- 在当前帧间残差搜索的胜者记录中增加 encoder-only `currBestUsesGtt`，与 `currBestCost/currBestSbt/currBestTrs` 一起更新。
- 在 `xCheckBestMode` 交换临时与最佳 CodingStructure 之前捕获状态；不要在最终登记时读取已交换的临时 TU。
- 最终局部胜者含 GTT 时，跳过本次旧 SBT/MTS 历史登记。该标志随每次当前胜者更新，旧模式胜出时允许正常登记；不能沿用之前候选的标志。
- GTT 关闭时保持原历史行为；不全局关闭 SBT/MTS，也不扩展规范 `MtsType`。

因此原方案的文件清单需要补入 `EncoderLib/EncCu.cpp`。若未来希望复用 GTT 专属历史，再单独设计带类型的历史项；v1 无需扩展整个历史缓存格式。

### 3.2 帧内必须落实独立全零评价

原文 §8.2 要求“全零由外层单独评价”，但 §9.1 伪代码没有独立零候选步骤。帧间现有 `InterSearch.cpp:7655`、7821 附近已有零残差计价和比较；帧内 `IntraSearch.cpp:3745` 仅根据量化结果进入非零/零重建分支，4128 附近没有对每个非零结果另作真实失真下的全零比较。

**采用的补充设计：** 在运行开启且满足 GTT 适用条件的帧内通道，显式评价一次规范化零候选：

```text
gttFlag = false
mtsIdx[Y] = DCT2_DCT2
q[Y] = 0; absSum = 0; lastPos[Y] = -1; CBF[Y] = 0
```

复用相同预测、裁剪、适用的 BIF/LMCS 处理和完整语法估价；不得只用残差能量替代本入口的真实失真。调用重建段时跳过旧量化，避免覆盖已经安装的零等级。零候选不写 GTT flag。

固定顺序为“原旧候选顺序 → 规范零候选 → τ1 至 τ4 的 GTT 候选”，严格更低代价才替换。帧间复用其既有零候选机制，避免再引入一套失真或上下文口径。

这项补充落实原文承诺，也扩大了开启时帧内零候选的评价范围。后续收益分析需要承认这一搜索变化，不能把全部差异归于旋转核。关闭模式保留原流程。

## 4. 细化后的接口和状态责任

| 层 | 责任 | 不应承担的工作 |
| --- | --- | --- |
| `Gtt4x4` / `UnitTools` | 扫描、整数算子、最终等级选核、公共资格与模式互斥 | CABAC 估价、编码器预测信息 |
| `TrQuant` / `DepQuant` | 固定基础变换、整 TU 反量化、受约束量化 | 修改全局候选历史或决定最终模式 |
| `GttSearch` | 原始残差对应的基础系数与四路径缓存、11 类约束调度、返回候选等级 | 自行复制一套帧内/帧间失真公式 |
| `IntraSearch` / `InterSearch` | 安装候选、真实重建和完整计费、保存并恢复胜者 | 从代理代价直接选最终核 |
| `EncCu` | 正确标识 GTT 胜者对旧历史缓存的影响 | 把 GTT 当作旧 DCT 标签写入历史 |
| Writer / Reader | 对称的 SPS/TU 信号、公共符号链、非法状态拒绝 | 依赖编码端临时核标签 |

扩展通道按以下事务执行：

```text
captureStart(prediction, originalResidual, Ctx, CUCtx, QP, lambda)
evaluateLegacyCandidatesInOriginalOrder()
saveLegacyWinner()

if shouldTryGtt:
    evaluateCanonicalZeroAtTheSameScoringBoundary()
    computeFixedDctOnce()
    prepareUpToFourImmutablePathBuffers()
    for each of the 11 constraints in fixed order:
        result = quantGtt4x4(startCtx, pathCoefficients, constraints)
        if result is infeasible: continue
        restoreCandidateStart()
        installLevelsAndCanonicalMetadata(result)
        deriveAndValidatePathFromFinalLevels()
        reconstructAndScoreWithExistingEntry()
        saveCompleteWinnerIfStrictlyBetter()

restoreCompleteWinner()
```

其中零候选在帧间使用已有实现。帧内的最佳缓冲必须在“旧候选数大于一或将尝试新通道”时可用，旧最后候选也必须保存。候选安装接口需要区分“旧量化”和“已有等级”，后者不得再次调用 `quantNxN`。

保存内容包括 TU 的 Y 状态及系数、实际重建、原流程需要的预测/残差、dist/bits/cost、Ctx 和 CUCtx。复制色度时不能清亮度 `gttFlag`。同时覆盖 `BestEncInfoCache` 的存取：其实现通过 `copyComponentFrom` 复制 TU（[EncModeCtrl.cpp](../../source/Lib/EncoderLib/EncModeCtrl.cpp) 900、981 行附近），不能只验证当前循环中的 bestTU。

## 5. 数值与估价的边界

### 5.1 共同反量化必须完整复用

现 `DepQuant.cpp:939` 先从等级和物理状态形成 `qIdx`，944 行起还会对 `qAbs<64` 应用 `coeffShiftArray` 的小等级重建修正，再裁剪。它不等价于简单的 `level × step`。

`preQuantAbsTCQ` 的代理失真也不能直接代替这套实际重建失真。原文要求完整 dequant/inverse 后重排是正确的；每个路径/终组只返回一个代理最优候选，仍可能提前丢掉真实评分更优的其他等级序列。这是搜索近似，不是解码不同步。

### 5.2 补齐前向和量化中间量的证明

原文逆端 46336、55929 及保守 4 点累加界 18568428 已按整数公式复算一致。现 `TrQuant_EMT.cpp:256` 的 4 点逆蝶形使用 TCoeff 乘加，未先把 55929 窄化为 16 bit。

前向仍需把实际残差范围写入实现契约。以每样本 `|r|≤1023` 为前提，对二维整数 DCT 及 724/Q10 旋转累计舍入误差的有理数界估计，四路径系数可统一保守界为 32737；这不替代对实际 LMCS/预测入口的范围核对。P1/P3 还须检查 `preQuantAbsTCQ` 的 scaledOrg、deltaDist 中间乘积与合法 QP/lambda 范围，不能仅证明旋转不溢出。

### 5.3 区分最终完整计费与原预筛

完整 RDO 中，eligible 旧非 TS 候选必须支付 `gtt_flag=0`，GTT 支付 1，二者只由同一个 Writer/Estimator helper 计费一次。

但现有帧内 `PreCostLuma::estTransBits`（`IntraSearch.cpp:1564`）及帧间对应预估（`InterSearch.cpp:7490`）直接调用旧 TS/NST/MTS helper，不经过新 flag 的插入点。v1 保留原预筛时，这些预估不会自动计入新 EP 位。文档应只承诺完整 RDO 的计费正确；不能宣称所有预筛代价也已同步。重做预筛另记搜索 revision。

I/I 的 GTT 与旧 DCT 可以共享基础系数，但不能无条件去重：二者的 flag、NST/MTS 语法和符号预测行为不同，完整代价可能不同。

### 5.4 不作为当前 v1 错误的边界

- 扩展 Rice 可能超出所复用 DQ 率表的范围，但合法 v1 为内部 10 bit，原配置检查和 SPS Writer/Reader 已拒绝对应 REXT。不要将绕过参数检查、手工构造扩展 TU 的问题描述为默认 nx2High 缺陷；合成入口也应执行支持范围检查。
- 实际 `DecLib::xDecodeSPS` 每次新建 SPS（`DecLib.cpp:4204`）。原文的对象复用测试应明确夹具初态：只清 GTT 字段不能代表旧解析器已支持任意 REXT 状态的跨流复用。无需为本实验顺带重构整个旧 SPS 解析器。

## 6. 优化顺序

| 顺序 | 建议 | 等价性与范围 |
| --- | --- | --- |
| v1 实现时 | 同一预测仅算一次固定 DCT，保留最多四份 16 系数路径缓冲 | 与原设计一致；缓存根输入不可被旋转覆盖 |
| v1 验证后 | 在同 TU/QP/lambda/初始 Ctx 下复用量化常数、RateEstimator 和路径 PQ 数据 | 目标是候选、tie 和码流等价；各次 State/trace/active 仍独立初始化，工作区按搜索实例/线程隔离 |
| 单独搜索修订 | 每个路径/终组保留更多有效终态，真实重建后重排 | 可能改善代理失真偏差；位置 0 奇偶重置后最多四个物理终态，最多可扩大到 44 个候选，须计复杂度 |
| 更后续修订 | 固定 last 分别求解、扩大预测覆盖、路径符号预测 | 改搜索或解码协议，分别登记，不并入初始 v1 |

初始版本保留 11 类非零搜索，不先加能量剪枝或更多角度。仅内层扫描循环的上限就为 `1×4+2×8+4×12+4×16=132` 个位置，每个位置再处理联合状态；总编码时间还包括完整重建、BIF、估价和候选复制，不能只按旋转次数估计成本。

推荐性能用的 `gtt4x4_on.cfg` 将 `GTT4x4Stats` 设为 0；诊断时单独覆盖为 1。原方案示例覆盖文件设为 1，应避免直接拿其时间与无统计 anchor 比较。此处是配置设计建议，本轮没有新增或修改 cfg。

统计增加最终实际写出的 `flag=0` 与 `flag=1` 数量，才能看到所有适用旧 TU 的信号成本。搜索尝试次数、最终写入次数和计时分开；统计开关不能改变候选、排序或状态提交。

## 7. 实施与验收安排

沿用原 P0–P7，增加以下落实点：

| 阶段 | 本轮设计补充 | 通过条件 |
| --- | --- | --- |
| P1 共同算法 | 前向残差与中间量范围；真实 TU 复制入口 | 规定算子、宽系数、零后缀与模式字段正确 |
| P2 语法 | 连续 GTT→旧 DCT→TS→CBF0；亮度 GTT 与 JointCbCr、dual-tree 色度 | flag 出现条件、符号链和状态清理两端一致 |
| P3 DQ | 原小等级反量化修正、单 CG 约束、非法人工输入拒绝 | 回溯合法；真实反量化一致；不宣称完整历史全局最优 |
| P4 帧内 | 独立零候选；旧列表只有一个候选时仍保存最优 | 旧胜、零胜、GTT胜及新候选全不可行均可正确恢复 |
| P5 帧间 | GTT 胜者的历史登记隔离；真实缓存往返；flag 0/1 统计 | GTT 不冒充旧 DCT 历史；关闭时历史与缓存行为恢复 |
| P6 非序列验证 | 宏0/1构建、合成读写、ASan/UBSan、现脚本 dry-run | 逐项保存实际证据；各构建独立输出，避免顶层 bin 覆盖 |
| P7 获授权后 | 关闭恢复、启用解码 hash、统计等价，再做性能 | 明确序列和任务范围；正式数据可追溯 |

需要新增的针对性合成用例：

- H01：构造 4×4 inter GTT 胜者，经过真实历史登记入口后，不能因 DCT2 占位错误删除后续 MTS；并覆盖结构交换与胜者标志重置。该尺寸不允许 SBT，不要求同块发生 SBT 与 GTT 的直接竞争。
- Z01：旧量化返回非零但规范零候选真实代价更优；零候选获胜后 CBF、last、等级、gttFlag、reco 和上下文一致。
- C01：GTT TU 写入并从 `BestEncInfoCache` 恢复，再重建与计费；复制色度保持亮度标志。
- R01：同为 I/I 几何核的 GTT 与旧 DCT 分别完整计费，验证未被错误合并。
- D01：构造 `qAbs<64` 的等级，核对完整原反量化而非简化步长乘法。

## 8. 实验解释与本轮证据

研究假设是：受等级奇偶约束的跨组旋转能够改善部分亮度 4×4 的稀疏性/率失真，并抵偿控制约束、额外 flag、局部符号预测关闭以及搜索耗时。没有依据预先保证 BD-rate 改善。

唯一正式 Current anchor 拟采用本项目已冻结基线、原 nx2High 配置和原工具开关；复用任何既有结果前仍须确认可比性。宏0、宏1/运行关闭属于恢复验证，不能替换 anchor 身份。机制分析如需分离新语法、零候选和旋转效果，应另登记诊断对照；不从普通尝试次数推断纯旋转收益。

先以编解码一致性和关闭恢复为必要门槛，再观察逐序列/配置/QP 的码率、Y/U/V PSNR、分量及 6:1:1 加权 BD-rate、编码/解码时间。可接受的时间预算和收益阈值尚未约定；若正确性不通过，应停止扩大实验；若正式结果不能抵偿信号和复杂度成本，应保留否定结果并再决定是否扩展搜索。

本轮实际完成：

- 阅读完整原方案并核对相关源码、四套 nx2High 配置、当前分支与提交。
- baseline manifest 检查通过，2486 个文件无差异。
- 用独立 Python 算术复算 8 组整数黄金向量、6 个半格舍入值、逆端界，结果与原文一致。
- 复算附录 A 的四个实数参考 SSE：`0.240100000000`、`2.612683002030`、`0.377849006091`、`1.034703255584`，与原文一致。

上述公式复算没有调用参考编码器，也不是未来 C++ 实现的单元测试。算法实现、构建、合成 CABAC/DQ 测试、任务 dry-run、序列编解码、bit-exact 和 BD-rate 均未执行。本轮交付为设计记录，不将 P1–P7 标为完成。
