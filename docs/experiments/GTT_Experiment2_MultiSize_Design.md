# HHI_baseline 实验 2：多尺寸 GTT 设计与 Codex 实施规格

- 初稿日期：2026-10-31；本轮设计修订：2026-11-01，`e2-design-r1`。
- 状态：设计规格；尚未实现实验 2、编译新实现或运行序列实验。
- 仓库：zhy2249/HHI_baseline。
- 设计起点：experiment/gtt-nx2high，提交 7586bbc3335e5bd7f3de7f2463f207ad3e134044。
- 本轮核对提交：2e370834547b7cc04b3e40bf9961036f0a917001；相对设计起点只增加设计文档和协作/索引说明，算法源码未变。
- 固定项目基线：5da139f22ffe9a281d8d9e4f0b7b4b22cba5326b。
- 文档位置：docs/experiments/GTT_Experiment2_MultiSize_Design.md。
- 本文不改写实验 1 的既有方案、结果、源码身份或 baseline manifest。
- 用户目标：设计更大尺寸 GTT，后续按尺寸独立开展实验 2，检验小块中 GTT 标志成本可能较高的假设。

## 0. 给 Codex 的执行摘要

主方案：对完整亮度 8×8、16×16、32×32 TU，使用对应尺寸固定整数 DCT-II，再按原 grouped-4x4 对角扫描分成四个逻辑组，沿用两级 I/R724 旋转、最终等级奇偶控制及受约束 DQ1。树深保持不变，每个 TU 最多 11 类非零候选和一个规范全零候选。

实验 2 主线采用四组等分。另提供“固定首 CG、后三组等分”的明确分组对照：8×8 与等分相同；16×16 为 16/80/80/80；32×32 为 16/336/336/336。两种分组不混在一个未标明身份的结果里。

必须先解决两项实质兼容问题：

1. 8×8 起跨多个 CG，不能照搬当前单 CG 的 GTT4x4 量化循环。必须恢复真实 CG 类型、CG 跳过、邻域历史和跨 CG 奇偶维护。
2. 逆旋转系数可以达到 55929。16/32 点部分旧 SIMD 变换内核会把输入饱和成 int16，不能直接调用。为 GTT 提供宽幅固定 DCT 入口，保留原矩阵、位移和阶段裁剪。

新增协议版本 3，在 SPS 中发送尺寸掩码与一种全序列分组方式。每个适用 TU 仍只有一个 GTT flag；核路径、终止组、尺寸索引均不额外写入 TU。版本 2 的原 4×4 路径继续可读，旧语义不得改变。

主比较逐项为：8-only、16-only、32-only；4-only 是迁移对照；最后根据独立结果评估尺寸组合。未开启的尺寸不得写 flag=0，也不得因 GTT 增加规范零候选或新搜索。

按用户要求，子实验依次用整数标记：**1=8×8等分，2=16×16等分，3=32×32等分，4=16×16固定首CG，5=32×32固定首CG，6=4×4等分迁移对照**。`0` 只表示关闭，不占用子实验编号。宏和命令行直接使用此编号；尺寸、mask、分组由同一张模式表派生，详见第10、13节。后续尺寸组合从7起另行登记，本修订不提前赋予其含义。

本次设计不等于授权自动运行任何视频编解码。后续实现时遵守仓库 AGENTS.md；合成测试、编译和 dry-run 可以验证实现准备，不能冒充真实码流验收或压缩收益。

## 1. 研究假设与当前证据

实验 1 的已登记完整曲线中，RA Class C 四序列的 Y BD-rate 为 +0.050745%，6:1:1 为 +0.028515%；LB 七序列 Y 为 −0.026286%，6:1:1 为 −0.037968%。这些只是实验 1 当前有限集合的结果，不作为实验 2 的预期收益或实现错误证据。[S01]

“4×4 的 flag 相对昂贵”是合理且可验证的研究假设。当前 flag 使用专用 CABAC context，不是固定一比特。适用的旧非 TS 块也发送 0，GTT 块发送 1。[S02][S03]

若相同覆盖面积 A 中所有 TU 都出现一个 flag，且平均每个 flag 的码长相同为 b，则：

\[
R_{\mathrm{flag}}/A=b/N^2.
\]

同样 1024 个亮度样本下，理想 flag 次数为：

| 尺寸 | flag 次数 | 相对 4×4 的密度 |
|---|---:|---:|
| 4×4 | 64 | 1 |
| 8×8 | 16 | 1/4 |
| 16×16 | 4 | 1/16 |
| 32×32 | 1 | 1/64 |

这不是实际编码块重新划分的命令，也不是预计总码率收益。真实结果还取决于尺寸分布、CBF、TS、SBT、选择率、概率适应及预测链。

一个仅用于理解机制的假设：若 GTT 选择率 p=10%，理想已适应二元模型下，flag=1 约 3.32 bit，flag=0 约 0.15 bit，平均约 0.47 bit。若选中块排除 flag 后的平均局部 RD 等价节省为 S bit，则在冻结候选、输入和上下文的近似模型中，需要：

\[
pS>\bar b,\qquad S>H_2(0.1)/0.1\approx4.69.
\]

这里的 10% 是假设，既不是实验 1 使用率，也不是对实验 2 的预测。该式说明未选中块的 flag=0 成本同样需要被覆盖。

## 2. 冻结的实验 2 范围

| 项目 | 规定 |
|---|---|
| 主试尺寸 | 8×8、16×16、32×32 |
| 迁移对照 | 4×4，使用冻结实验 1 算法核对 |
| 变换对象 | 完整亮度 TU；不拆成若干独立空间 4×4 TU |
| 预测类型 | 普通 intra/inter；保留原公共资格限制 |
| 基础变换 | 对应尺寸固定整数 DCT-II |
| 逻辑组 | 4 组；主线等分，对照固定首 CG |
| 条件核 | I 与 R724/Q10，仍只两级 |
| 控制信息 | 最终传输绝对等级的组奇偶 |
| 量化 | 原 DQ1 四物理状态加当前逻辑组奇偶 |
| 配套量化条件 | RDOQ、RDOQTS 启用；符号隐藏关闭，保留原配置检查 |
| 搜索类别 | 1+2+4+4=11 类规范非零候选 |
| TU 新语法 | 每个实际出现位置一个 context-coded GTT flag |
| SPS 协议 | 版本 3、尺寸掩码、分组方式 |
| context | 按尺寸独立，初始参数与实验 1 同源且相同 |
| 位深与动态范围 | 内部亮度 10 bit、变换动态范围 15 |
| 排除 | 矩形、IBC、PLT、BDPCM、SBT、noResidual、缩放列表、无损、非默认 REXT、DQ 非 1 |
| 符号处理 | 仅 GTT 亮度关闭延迟符号与符号预测；其他模式和色度保留原行为 |
| 64×64 | 数学上可推广；本版协议不启用，见附录 A |

大尺寸实验不引入新的角度、训练核、预测预筛或能量门限。这样主线的差异集中在尺寸和必要的多 CG 实现。分组方式对照独立登记，不悄悄改变主线。

## 3. 完整变换定义

### 3.1 基础系数与扫描

对残差块 X 做对应 N 的固定整数 DCT-II，得到完整系数矩阵 Z。使用工程原表：

```cpp
g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
           [gp_sizeIdxInfo->idxFrom(N)]
           [gp_sizeIdxInfo->idxFrom(N)]
```

得到扫描向量 z。生产代码使用表，不硬编码自制 zigzag。散回时仍写入原扫描对应的光栅位置，不能改变既有系数语法坐标。[S04][S05]

### 3.2 主线：四组等分

令 L=N²，M=L/4：

\[
z=[e^T,a^T,b^T,h^T]^T,\quad e,a,b,h\in\mathbb Z^M.
\]

逻辑组 G1/G2/G3/G4 分别对应 e/a/b/h。

| 尺寸 | L | 四组长度 | CG 总数 | 每逻辑组包含 CG |
|---|---:|---|---:|---|
| 4×4 | 16 | 4/4/4/4 | 1 | 一个 CG 的四分之一 |
| 8×8 | 64 | 16/16/16/16 | 4 | 1 |
| 16×16 | 256 | 64/64/64/64 | 16 | 4 |
| 32×32 | 1024 | 256/256/256/256 | 64 | 16 |

### 3.3 两级旋转

保持实验 1 的有符号舍入：

\[
\operatorname{rs10}(v)=
\begin{cases}
\lfloor(v+512)/1024\rfloor,&v\ge0,\\
-\lfloor(-v+512)/1024\rfloor,&v<0.
\end{cases}
\]

对成对坐标：

\[
R(a_j,b_j)=
\left(
\operatorname{rs10}(724(a_j+b_j)),
\operatorname{rs10}(724(b_j-a_j))
\right).
\]

第一层：

\[
(c_2,v)=
\begin{cases}
(a,b),&k_2=0,\\
R(a,b),&k_2=1.
\end{cases}
\]

第二层：

\[
(c_3,c_4)=
\begin{cases}
(v,h),&k_3=0,\\
R(v,h),&k_3=1.
\end{cases}
\qquad c_1=e.
\]

R 在向量的每对同序号坐标上执行。它是跨逻辑组的条件旋转；K2 会改变后续 K3 的输入 v。不得在改变 K2 后复用不对应的 K3 结果。

所有加法和乘法先提升到 int64。每次输出做 TCoeff 范围检查，不在旋转后额外裁剪到 15/16 bit。整数 R724 不是严格整数可逆变换，也不是精确正交矩阵；不得测试“任意整数正逆无损往返”。[S06]

规定 R 路径的整数合成算子为：

\[
S(u_j,v_j)=
\left(
\operatorname{rs10}(724(u_j-v_j)),
\operatorname{rs10}(724(u_j+v_j))
\right).
\]

本文的“逆 K2 / 逆 K3”指：I 使用恒等算子，R 使用 S。它不是浮点矩阵求逆，也不是再次执行前向 R。先对反量化后的 c3/c4 合成得到 v/h，再对 c2/v 合成得到 a/b；第一组 e 保持不变。

### 3.4 四条完整路径

下面用 R 的两输出 R_first、R_second 表示精确整数算子。

| 路径 | c1 | c2 | v | c3 / c4 |
|---|---|---|---|---|
| I/I | e | a | b | b / h |
| I/R | e | a | b | R_first(b,h) / R_second(b,h) |
| R/I | e | R_first(a,b) | R_second(a,b) | v / h |
| R/R | e | R_first(a,b) | R_second(a,b) | R_first(v,h) / R_second(v,h) |

一个预测候选只计算一次完整基础 DCT，最多缓存四条不可变路径系数。不同路径不得覆盖根系数或另一条路径。

### 3.5 从最终等级推导控制

q 是最终写入码流的量化等级，沿同一扫描分组。控制为：

\[
k_2=\left(\sum_{i\in G_1}|q_i|\right)\bmod2,
\quad
k_3=\left(\sum_{i\in G_2}|q_i|\right)\bmod2.
\]

不能使用预量化值、内部 qIdx、反量化幅度、原残差或暂存候选标签来代替。

解码流程：

1. 读取整 TU 的最终等级及原有 CG/last/幅度/符号语法。
2. 从最终等级计算 k2、k3。
3. 使用原整 TU DQ1 反量化，完整保留小等级重建修正和物理状态轨迹。
4. 逆 K3，再逆 K2。
5. 对整块执行对应尺寸固定逆 DCT-II。
6. 进入原预测相加、裁剪和后续环路流程。

DQ 反量化不能按 GTT 逻辑组分别初始化。逻辑组序号也不改变原熵解码的逆扫描顺序。

## 4. 8×8 的 64 系数对应关系

8×8 共有四个 4×4 熵编码 CG，原 CG 对角顺序为左上、左下、右上、右下。

| 频率矩阵区域 | 列 0–3 | 列 4–7 |
|---|---|---|
| 行 0–3 | G1 / CG0 / 扫描 0–15 | G3 / CG2 / 扫描 32–47 |
| 行 4–7 | G2 / CG1 / 扫描 16–31 | G4 / CG3 / 扫描 48–63 |

对 j=0…15：

- K2 配对扫描 16+j 与 32+j。
- K3 配对 K2 的后输出与扫描 48+j。
- q[0…15] 的奇偶控制 K2。
- q[16…31] 的奇偶控制 K3。

这仍是一块 8×8 残差的 64 维完整变换，不是四块 4×4 残差分别变换，也不是给四个 CG 分别发 GTT flag。

16×16 的 CG 顺序按光栅编号为：

```text
G1: 0,4,1,8
G2: 5,2,12,9
G3: 6,3,13,10
G4: 7,14,11,15
```

因此，16×16 及以上的等分逻辑组不是四个矩形频域象限。上述列表只能作为黄金映射检查，生产仍读取原扫描表。

## 5. 分组对照：固定首 CG，后三组等分

### 5.1 动机

等分时，N=16 的 G1 有 64 个系数，N=32 的 G1 有 256 个系数；它们完全不参与两级旋转。若主要能量已经集中在 G1，大尺寸虽然摊薄 flag，却可能让真正可适应的系数区域过晚。

这是需要测试的机制，不是已有结果。主线诊断必须统计原基础系数的 G1 能量比例、最终 τ=1 比例和有效前缀 R 选择率；后者不等于系数实际改变率，见第12节。

### 5.2 合法定义

对 N≥8：

\[
A=16,\qquad M=(N^2-16)/3,
\]
\[
|G_1|=A,\qquad |G_2|=|G_3|=|G_4|=M.
\]

| 尺寸 | 组长度 | 每组 CG 数 |
|---|---|---|
| 8×8 | 16/16/16/16 | 1/1/1/1 |
| 16×16 | 16/80/80/80 | 1/5/5/5 |
| 32×32 | 16/336/336/336 | 1/21/21/21 |

对于支持的 N，这些长度均为整数、16 的倍数。后三个向量仍等长，故原 K2/K3 算子和隐式控制完全成立。

分组方式只在 SPS 选择一次，不在每个 TU 增加一个分组 flag。子实验1/2/3/6派生groupMode=0，子实验4/5派生groupMode=1。版本 3 中 groupMode=1 不允许尺寸掩码包含 4×4，避免对首 CG 定义产生歧义。8×8 两种模式的变换完全相同，不重复作为独立算法实验。

实现必须通过 layout.groupBegin/groupEnd 查询边界。不能将组索引统一写成 i/M；groupMode=1 的第一组长度不同。

该对照也有代价：首组只保留 16 个等级作为 K2 的控制载体，满足奇偶的灵活性可能与等分方案不同；较远坐标的配对相关性也可能更弱。不能预先保证优于等分。

## 6. 固定 11 类候选与编码流程

设累计边界 b[0]=0，b[g+1]=b[g]+len[g]。τ 表示最终最后非零系数实际落入的逻辑组，取值 1…4。

| τ | last 范围 | 有效控制约束 | 候选类别数 |
|---|---|---|---:|
| 1 | b0…b1−1 | 无 | 1 |
| 2 | b1…b2−1 | G1 奇偶=k2 | 2 |
| 3 | b2…b3−1 | G1=k2，G2=k3 | 4 |
| 4 | b3…b4−1 | G1=k2，G2=k3 | 4 |

last-start 只允许在本行区间内产生，必须非零；更低位置只有路径延续。不能只限制最大 last 而不限制最小 last。

τ=1 时尾部全零，两级核均不影响重建；τ=2 时 G3/G4 全零，K3 不影响重建。只检查有效控制前缀，不因无效后缀的最终奇偶不同而错误拒绝候选。

算法顺序保持固定：

1. 原旧模式及其既定顺序。
2. 帧内扩展通道的一次规范零候选；帧间复用既有零候选。
3. τ 从 1 到 4，每层 I 在 R 前，固定词典序。
4. 严格更低完整 RD 代价才替换，平局保留先前胜者。
5. 统一恢复完整胜者状态。

主线累计扫描位置上限为 33M=8.25N²：

| 尺寸 | DQ 调用上限 | 扫描位置上限 |
|---|---:|---:|
| 4×4 | 11 | 132 |
| 8×8 | 11 | 528 |
| 16×16 | 11 | 2112 |
| 32×32 | 11 | 8448 |

固定首 CG 对照为 11A+22M：8×8 为 528，16×16 为 1936，32×32 为 7568。这些数字不是整个编码器倍率。

每个候选均从同一预测、原残差、QP、lambda、Ctx/必要的 CUCtx 起点评分。完整失真沿用当前入口：帧内使用原重建/裁剪/BIF/LMCS 口径；帧间沿用原帧间口径。不能只改变 GTT 的失真定义。

禁止：

- 事后 ±1 修改等级来补奇偶；
- 对四组分别无约束量化；
- 因原最优为 TS 或 CBF0 而跳过 GTT；
- 将原源系数尾部清零来模拟 τ；
- 使用代理代价与零比较后提前拒绝全部非零候选；
- 把 τ 或 kernel path 当成必须传输的 TU 状态。

## 7. 多 CG 受约束 DQ：实施核心

### 7.1 当前代码的限制

当前 quantGtt4x4 明确要求 m_numSbb=1、m_numCoeff=16、所有位置 SCAN_ISCSBB；未进入 CG 边界历史或 CG skip 分支。[S07]

实验 2 需要新增通用 quantGtt，不能只把上述断言删除、扩大数组后继续沿用单 CG 循环。

### 7.2 四物理状态、八联合槽、三组 State

物理 DQ1 状态 s∈{0,1,2,3}；当前逻辑组累积奇偶 p∈{0,1}：

\[
slot=2s+p.
\]

三个 State bank 各八槽：

- current；
- previous；
- 当前 CG 开始前的 skip snapshot。

每组 m_stateId 的固定排列为 0,0,1,1,2,2,3,3。它用于原物理转移、率表和 Rice 状态，不得换成 slot，也不切换到 DQ2。

State 含引用和 const 字段，使用原样指针交换管理 bank，不使用整个 State 赋值。有效标志若独立保存，应同步交换。

所有 init 使用实际 N,N，使 regular-bin 预算反映真实 TU 面积。

### 7.3 物理编号与历史槽编号分离

当前 CommonCtx 已有两代各八槽，数量足够。问题在 CommonCtx::update 使用 m_stateId 作为历史目标索引；同物理态的两种奇偶会相互覆盖。[S07]

建议内部 State 构造增加独立 historySlot：

```cpp
State(rateEstimator, commonCtx, physicalStateId, historySlot);
```

- 新 GTT：physicalStateId=slot/2，historySlot=slot。
- 旧路径：historySlot=physicalStateId，保持原行为。
- CommonCtx 写目标历史以及写入 m_refSbbCtxId 时使用 historySlot。
- 原率表、物理转移和 Rice 零位置继续使用 physicalStateId。
- m_refSbbCtxId 范围仍为 −1 或 0…7。

两条奇偶路径的历史包括 CG 标志、邻域等级、Rice 和预算；不能仅保存一个奇偶布尔量后共享全部历史。

### 7.4 普通系数路由

对每个合法等级 q：

\[
s'=T_{\mathrm{DQ1}}(s,|q|\bmod2).
\]

若当前逻辑组有约束，则 p'=p xor (|q|&1)，否则 p'=0。逆扫描到该逻辑组的最低扫描位置时检查目标奇偶，失败丢弃；成功只清新增奇偶，保留物理态及其他历史。

主线可用 i%M==0 判断逻辑组结束；通用生产代码应查询 layout，兼容不等长首组。16×16 中不能每16个系数清奇偶，32×32 同理。

原 checkRdCosts/checkRdCostsOdd1 返回的 prevId 是物理编号，调用者必须将有效决定改成 predecessorJointSlot 后再路由/更新。

沿用当前正确 PQ 映射：物理偶状态 even/odd 为 PQ[0]/PQ[2]；物理奇状态为 PQ[3]/PQ[1]；near-zero 分支按物理态选择对应 PQ，保留零与等级1的竞争。

### 7.5 恢复真实 CG 语义

每个位置传入 tuPars.m_scanInfo[i].spt，不可统一传 SCAN_ISCSBB。

- SCAN_SOCSBB：原 CG 起始语义与 sig CG=1 代价。
- SCAN_EOCSBB：原 CG 结束语义及整 CG skip 竞争。
- scanInfo.eosbb：是否需要向下一 CG 更新历史。

eosbb 与 spt==SCAN_EOCSBB 不是同一个判断。保留原代码对首 CG、末 CG、推断非零的特殊语义。

在 eosbb 且 i>0 时调用 updateStateEOS；在 i=0 时不能访问 CommonCtx 的 i−1 数据。

每次 eosbb 且 i>0，在该边界所有目标槽的 updateStateEOS 之前，只调用一次 CommonCtx::swap()。previous/skip 的历史引用读取旧代，八个目标槽写入新代；不能每槽 swap，也不能遗漏。各 State bank 的轮换和 SOCSBB 时的 skip 快照轮换，保持原多 CG DQ 的时序。

lastOffset 使用实际 N,N 及 slice.m_reverseLastSigCoeffFlag。每次调用的 skip 快照初始必须全部无效；τ<4 时起点位于物理 TU 中间 CG，不能把尚未开始的高位非零路径错误视为可 skip 的来源。

### 7.6 整 CG skip 独立路由

DQ1 一个零的转移为 0→0，1→2，2→1，3→3；完整16个零后为恒等：

\[
T_0^{16}(s)=s.
\]

整 CG skip 应从 CG-entry 快照出发：

- 物理态不变；
- 当前逻辑组奇偶不变；
- 若同时到达逻辑组边界，再检查目标奇偶并清零；
- 只支付原 skip 对应的 CG 率代价；
- 保留原 skip 的 regular-bin 预算行为。

不能把 skip 当作一次 level=0 的普通路由，否则状态1与2会错误互换。

若复用 updateStateEOS：

```cpp
skipOffset = 8;
decision.prevId = 8 + snapshotJointSlot;
```

checkRdCostSkipSbb 原来产生的 skipOffset+physicalStateId 同样需要改成上述联合来源。

一个要求奇数奇偶的整个逻辑组全零不合法；但该逻辑组的部分 CG 可以跳过，只要其他 CG 已贡献所需奇偶。不得全局禁止控制组内的零 CG。

### 7.7 推荐的回溯表示

使用显式回溯跳转，避免复制旧量化辅助 trace 常数时发生维度错误：

```cpp
struct GttTrace {
  int predecessorSlot;  // 0..7；start 使用独立标志
  int nextTraceScan;    // 普通 i+1；skip 为 i+16
  TCoeff absLevel;
  bool start;
  bool skipCg;
};
```

运行时 Decision.prevId 可继续用 8+slot 表示 skip 来源，但 Trace 保存真实联合槽，二者不要混用。

从 i=0 回溯：

- 普通节点写当前等级，再到 i+1。
- skip 节点写当前 CG 的16个零，再到 i+16 对应来源。
- start 节点必须非零，且位于本次 last 区间，然后结束。
- 回溯先整体清零输出，确认 last 以上为零。
- 从最终结果重新检查全部有效奇偶、absSum、last、CBF。
- 不可行返回不得污染调用者 TU。

这是有限幸存路径近似，并不保证真实 CABAC 历史与像素失真下的全局最优。

### 7.8 每次调用初始化与内存

每次调用独立初始化 Quantizer、RateEstimator、CommonCtx、3×8 State、skip 有效位和 trace，不继承上一个尺寸/路径/旧模式的状态。

当前 CommonCtx 的固定内存约1.06 MiB，不能在每次 GTT 函数栈上再构造一份。可复用 DepQuant 实例已有 m_commonCtx，并确保原、新调用均完整 reset；或使用量化实例私有 workspace。

实际两代八槽历史数据需求为：

| N | 16×(N²+N²/16) 字节 |
|---|---:|
| 8 | 1088 |
| 16 | 4352 |
| 32 | 17408 |

workspace 按编码线程/量化实例隔离。若复用 State，RateEstimator 和相关率表的生命周期必须覆盖 State 使用期。不能用共享全局静态可写缓冲。

### 7.9 尺寸类型和代价范围

当前 Gtt4x4Constraints 的 minLastScan/maxLastScan 是 uint8_t，32×32 必须扩大；建议通用接口使用 int，支持0…1023并便于未来扩展。

检查并替换所有固定16容量、4宽高、/4、&3、lastOffset(...,4,4)、16系数最大幅度扫描。物理 CG 内部 /16 或 &15 不能误改成逻辑组边界。

Quantizer::initQuantBlockGtt 继续根据实际整块最大源幅度设置代理精度；另证明整条 N² 路径的 rdCost 累加不溢出，不能只证明单系数乘积安全。不能对合法输入静默饱和代理代价来改变路径选择。

## 8. 固定 DCT 与整数范围合同

### 8.1 数值语义

使用原工程的 g_trCoreDCT2PN 对应前向/逆向矩阵，不使用浮点正交矩阵替代，不增加 QP 偏移。[S08]

10 bit、动态范围15、当前矩阵精度下：

| N | 前向第一遍 | 前向第二遍 | 逆向第一遍 | 逆向第二遍 |
|---|---:|---:|---:|---:|
| 4 | 3 | 8 | 7 | 10 |
| 8 | 4 | 9 | 7 | 10 |
| 16 | 5 | 10 | 7 | 10 |
| 32 | 6 | 11 | 7 | 10 |

生产使用原公式推导位移，不硬编码表格掩盖前提。

DCT 的原阶段舍入是加正偏置后算术右移，与旋转 rs10 的负数半格远离零不是同一种规则。参考核分别实现，不能统一替换。

前向保留完整 Pel 残差输入；混合预测可能超出±1023，不新增原入口没有的裁剪。中间量在宽类型中计算，输出做 TCoeff 范围检查。

按完整Pel绝对值界32768及各矩阵最大绝对元素A估算，首遍输出界为 `floor((N*A*32768 + 2^(shift1-1))/2^shift1)`：

| N | A | 首遍输出绝对界 | 第二遍移位前点积绝对界（不含舍入偏置） |
|---|---:|---:|---:|
| 8 | 89 | 1458176 | 1038221312 |
| 16 | 90 | 1474560 | 2123366400 |
| 32 | 90 | 1474560 | 4246732800 |

32×32的粗界超过int32。该上界不表示所有中间值可同时取极值，也不证明实际必然溢出；但32位正式实现必须给出更紧的结构界并计入舍入偏置，否则使用64位累加。不能用逆端范围证明前向累加安全。

逆向：完整 DQ 反量化后先逆旋转；第一遍逆 DCT 后执行原动态范围裁剪；第二遍执行原 Pel 范围裁剪。不得把阶段裁剪提前到旋转之后。

### 8.2 逆旋转幅度

原反量化系数绝对值保守界32768，两级逆旋转给出：

\[
B_3=\operatorname{rs10}(724(32768+32768))=46336,
\]
\[
B_2=\operatorname{rs10}(724(32768+46336))=55929.
\]

这个界不随逻辑组长度变化，因为每个输出只经过固定两级成对操作。

| N | 矩阵最大绝对系数 | 首遍逆 DCT 保守点积界 |
|---|---:|---:|
| 8 | 89 | 39821448 |
| 16 | 90 | 80537760 |
| 32 | 90 | 161075520 |

以上逆端点积在32位内，但输入55929超出int16。前向完整Pel的中间界另外验证；不要直接借用逆端界。

### 8.3 旧 SIMD 窄化：必须解决

当前 fastInverseDCT2_B16/B32 在 ENABLE_SIMD_TRAFO 下进入 inverseMatrixMult，再调用 fastInvCore；TrafoX86.h 多处用 _mm_packs_epi32 把源TCoeff饱和到int16。[S09][S10]

因此直接把 GTT 逆旋转输出送进这些入口不正确。前向矩阵 SIMD 的 pack 也可能在完整Pel、混合残差导致的宽中间量上提前饱和。

规定：

1. 增加 GTT 专用宽幅固定 DCT 入口。
2. 黄金参考采用原矩阵、int64 点积、原位移/舍入/阶段clip。
3. 正式实现采用专用宽幅蝶形或经过证明的32位SIMD，并逐点对齐黄金参考。
4. 逆第一遍已完成原15bit clip之后，第二遍可在输入范围满足的前提下复用原快核。
5. 不全局关闭原SIMD，不改旧模式算子。
6. 不在旋转后加int16 clip来绕过问题。
7. 稠密矩阵黄金参考为O(N³)，不得不加说明地作为正式计时版本，避免尺寸越大越被参考实现拖累。

## 9. SPS、TU 语法与 context

### 9.1 版本化 SPS payload

复用现有私有 SPS 扩展槽1。版本2原payload保持原样；版本3定义如下：

```text
sps_gtt_version_idc                    u(8)

if version == 2:
    // 不读取更多位
    sizeMask = 1
    groupMode = 0

if version == 3:
    sps_gtt_size_mask                 u(8)
    sps_gtt_group_mode_flag           u(1)
```

mask 位0/1/2/3对应4/8/16/32；位4…7本版必须为0。非零mask仅在工具开启时存在。groupMode=1时mask不能包含4。

工具关闭时原扩展行为保持，无新增GTT payload。Reader每次解析先清新字段，按版本读完整payload，最后统一验证公共配置；未知版本、空mask、保留位非零、非法group/size组合必须明确拒绝。

当前 v2 读完版本立即调用只允许2的验证；实现时必须改为版本化解析，不能无条件追加mask后让旧验证误解。[S11]

### 9.2 TU flag

原位置不变：ts_flag之后、last之前。

这里不是“先确认DCT-II再写flag”：NST/MTS索引语法更晚，资格检查只能使用此时两端均已知的信息。适用的旧DCT-II、NST、MTS候选均写0；GTT写1后排除其亮度NST/MTS。GTT自身使用DCT-II占位索引，不把占位值当成flag出现的条件。

flag出现条件：

- 公共 GTT 资格满足；
- SPS sizeMask包含当前尺寸；
- 当前分量Y；
- Y CBF=1；
- 非TS。

适用旧候选写0，GTT写1。未启用尺寸完全不写GTT flag；不新增每组flag、不写kernel index、不写τ、不写size id。

Cb/Cr helper立即返回且不修改luma gttFlag。仅Y推断不出现时清/断言false。色度CBF为0不能清亮度flag。

### 9.3 context

- 保留版本2的原Ctx::GttFlag及其偏移。
- 版本3在原context末尾追加四个Ctx::GttFlagBySize。
- sizeId由N推导，无额外语法。
- 四个context首轮使用相同初值35与原DWS/DWE/DWO，不同时训练不同初始化。
- Writer、Estimator、Reader从同一version/size分派。
- 新context随原Ctx复制、回滚、同步、初始化；不手工另建孤立概率对象。

相同尺寸单独测试和联合测试均保持同一种context定义。联合收益仍不能由各尺寸独立BD-rate相加得到。

### 9.4 兼容边界

- 未修改的baseline解码器不能解码启用GTT的码流。
- 原v2解码器拒绝v3，不能宣称向旧二进制兼容。
- 实验2解码器在编入GTT能力时支持旧v2与新v3。
- 工具关闭的恢复目标仍是原baseline行为；增加context定义本身不能改变旧context编号。
- v2与v3 SPS不同，4×4迁移对照不要求整条码流字节相同；应分别核对算法、TU等级、重建和预计的头部差异。

## 10. 宏、配置与身份

### 10.1 数值选择器与宏默认值

保留实验1的布尔入口，给实验2独立的整数选择器。以下均为待实现规格，当前源码仍只有实验1入口：

```cpp
#define HHI_GTT4X4        0
#define HHI_GTT4X4_STATS  0

#define HHI_GTT           0 // 0=off; 1..6=第13.1节登记的子实验
#define HHI_GTT_STATS     0 // 统计默认值；--GTTStats=0/1覆盖
```

`HHI_GTT` 默认0；非零既编入实验2编码能力，也默认启用同号子实验。例如宏为2时，无需命令行再次开启，默认就是16×16等分。任何非零值都须编入全部已登记的1…6模式，不得只编入宏所选的尺寸。统计宏紧随工具宏，只决定统计运行默认值，不能裁掉命令行开启统计所需的实现。

`EncCfg` 保存整数模式，例如 `int m_gttExperiment { HHI_GTT };`，不能用bool接收后把2…6归一成1。启用状态从最终模式 `!=0` 推导；统计默认值为 `HHI_GTT_STATS != 0`。编译期检查 `HHI_GTT` 只能为0…6、两项统计宏及旧工具宏只能为0/1；未来增加编号时同时扩展模式表与合法值检查。CMake不决定算法模式。

### 10.2 参数、覆盖与互斥

| 参数 | 意义 | 默认 |
|---|---|---|
| GTT | 整数；0关闭，1…6选择同号子实验 | 保留HHI_GTT原值 |
| GTTStats | 最终统计 | 跟随HHI_GTT_STATS |

本修订用编号替代初稿尚未实现的 `GTTSizes`、`GTTGroupMode` 自由组合入口；这两个名称不注册为可覆盖参数。内部仍保留规范sizeMask和groupMode供SPS、共同算法及日志使用。不得出现“编号2、实际运行8×8”的组合，也不允许自定义模式冒用已登记编号。

配置/CLI可覆盖宏默认值。当前解析器按读取顺序应用参数，覆盖参数放在相关 `-c` 之后。对任一 `HHI_GTT!=0` 的二进制，`--GTT=3` 切换到子实验3，`--GTT=0` 关闭。`HHI_GTT=0` 时实验2编码入口不可启用，最终生效GTT非零即报错；共享解码能力的存在不改变此限制。先读取GTT=1配置、再以CLI覆盖为0时应正常关闭，不能在读取前一项时过早拒绝。空值或非整数在解析时拒绝；负数、7及以上当前未登记值在所有覆盖完成后对最终模式检查并拒绝，不静默关闭或回退。

编码器在**所有配置和CLI覆盖完成后**检查 `GTT4x4 && GTT!=0`，两入口同时开启即报错。两个宏同时非零允许构建，但默认运行会因互斥失败；CLI关闭其中一个后可通过。不把旧参数GTT4x4改成多尺寸，也不由新入口暗中关闭旧入口。

最终GTT非零才检查所选尺寸是否超过SPS最大TU及DQ、位深、分组等能力约束。GTT关闭时不附加8×8默认尺寸之类的限制。旧GTT4x4开启时直接推导v2、mask=1、groupMode=0，不使用实验2模式表。

### 10.3 编码入口与共同解码能力

共享GTT算法、SPS读取及context定义按 `HHI_GTT4X4 || HHI_GTT != 0` 编入；新编码搜索入口另受 `HHI_GTT != 0` 控制，旧编码入口另受 `HHI_GTT4X4` 控制。本次扩展后的二进制能力为：

| HHI_GTT4X4 | HHI_GTT | 可用编码入口 | GTT解码能力 |
|---:|---:|---|---|
| 0 | 0 | 无 | 拒绝v2/v3 |
| 1 | 0 | 仅实验1 | v2与v3 |
| 0 | 1…6 | 实验2所有已登记子实验 | v2与v3 |
| 1 | 1…6 | 两入口可选，最终运行互斥 | v2与v3 |

此表不改变已发布实验1旧二进制只读v2的事实。解码按SPS选择版本/尺寸/分组，不按编译默认子实验筛选码流；关闭编码入口不等于关闭已编入的解码能力。共同context布局始终保留旧GttFlag，再追加四个GttFlagBySize，不能因旧编码入口关闭而移动索引。

保留encoder-only旧配置字段；SPS统一使用：

```cpp
bool    m_gttEnabled;
uint8_t m_gttVersion;
uint8_t m_gttSizeMask;
uint8_t m_gttGroupMode;
```

SPS只保留一份规范真值，不同时维护可能不一致的旧、新enabled/version字段。子实验编号仅用于编码配置和实验登记，不增加SPS字段或TU语法；解码器只需要现有v3的mask/groupMode合同。

### 10.4 配置和日志映射

例：子实验1覆盖配置，后续实现时保存为 `cfg/experiments/gtt_exp2_1.cfg`：

```ini
GTT4x4      : 0
GTT         : 1
GTTStats    : 1
```

子实验2…6仅将GTT改为对应整数，文件名同样使用编号；正式任务显式记录GTT/GTTStats覆盖值。性能测量用GTTStats=0，并与相同测时条件的anchor配对。

身份行至少包括：experiment=2、sub=<最终编号>、源码revision、compile（实验2编码能力）、default_sub（宏值）、enabled、version、sizes、mask、groupMode、DQ、kernel=I/R724、signPred=local-off、stats_default、stats、宽幅DCT实现标识。头部身份与最终统计均带experiment/sub，实际值不得从文件名猜测。

假设宏默认1、CLI选2且统计开启，启动身份字段示意（仅格式，不是运行证据）：

```text
EXPERIMENT: GTT experiment=2 sub=2 compile=1 default_sub=1 enabled=1 version=3 sizes=16 mask=0x04 groupMode=0 ... stats=1
```

编号2不是协议版本2，也不是第二个TU的索引。总实验编号固定为2，子实验编号独立记录；跨实验引用使用“实验2/子实验2”。新增组合必须先登记具体mask、组法和修订，再分配7、8…，禁止重新解释1…6。

## 11. 接口与代码落点

推荐增加通用类型，不修改TU为四个子TU：

```cpp
struct GttLayout {
  int width, height, numCoeff;
  int groupBegin[4], groupEnd[4]; // 半开区间
  int numCg, cgSize;
  uint8_t groupMode;
};

struct GttConstraints {
  int minLastScan { 0 }, maxLastScan { -1 };
  int8_t requiredParity[4] { -1, -1, -1, -1 }; // -1表示无约束
};

struct GttQuantResult {
  bool feasible { false };
  std::vector<TCoeff> qRaster; // 函数接口语义；实现可用实例workspace避免重复分配
  int lastScan { -1 };
  int64_t absSum { 0 };
  int64_t proxyCost { 0 };
};
```

具体宽度/容量可采用固定最大1024的实例私有缓冲，但必须避免每候选在栈上复制大对象；安装结果时检查absSum能安全写回工程字段。

任何feasible结果都必须有完整初始化的layout.numCoeff个等级，包含规范全零候选。默认vector为空，不能直接沿用原固定数组的默认零构造。规范零候选显式设置feasible=true并执行qRaster.assign(layout.numCoeff, TCoeff(0))；lastScan=-1、absSum=0。也可以提供同等语义的独立零安装接口。安装前检查尺寸与缓冲长度。

主要文件与责任：

| 文件范围 | 主要修改 |
|---|---|
| TypeDef.h | 新宏及相邻统计宏，保留既有修复宏 |
| Gtt4x4.* / 新Gtt.* | layout、扫描映射、通用两级算子、最终等级推导；保留旧入口包装 |
| Gtt4x4QuantRouting.h / 新路由helper | 参数化逻辑组边界，独立普通/CG skip路由 |
| DepQuant.h/.cpp | 多CG受约束求解器、historySlot、生命周期、回溯 |
| TrQuant.h/.cpp及专用核文件 | 宽幅固定DCT、整TU反量化/逆入口 |
| UnitTools.h/.cpp | 版本/尺寸资格、局部NST/MTS/SP互斥 |
| SequenceParameterSet.h/.cpp | 统一规范状态、版本payload验证 |
| Contexts.h/.inl | 追加按尺寸context |
| VLCWriter/Reader | v2/v3 SPS对称读写 |
| CABACWriter/Reader | size-context分派、同一flag时序、真实统计 |
| GttSearch.h/.cpp | 通用布局、四路径、11类别、规范安装 |
| IntraSearch/InterSearch | 复用现有完整评分事务，按启用尺寸进入 |
| EncCu.cpp | 保留已存在的多TU GTT胜者历史隔离 |
| EncCfg/EncAppCfg/EncLib | 参数、身份、SPS、统计归属 |
| tests | 宽幅数值、多CG、语法和搜索验证 |
| cfg/experiments及docs | 独立尺寸配置、实验2登记、任务预览 |

当前帧内最佳缓冲、最后旧候选保存、帧间独立第三轮以及GTT胜者历史隔离已存在；扩展时复用并覆盖新尺寸，不重新引入早退或DCT占位历史污染。[S12][S13][S14]

TU仍只需现有gttFlag。尺寸来自TU，kernel来自最终等级，τ来自last；不要增加第二套规范kernel/τ状态与等级竞争。

## 12. 统计：验证 flag 假设与旋转选择

### 12.1 最终语法统计

按实验/子实验、layer、尺寸、配置和QP分别输出：

- 实际flag数量total，以及zeros/ones数量、`percent=100*ones/total`；total=0时percent为N/A。
- 真实Writer编码该bin前，按当前概率模型得到的0/1累计分数比特估计。
- 实际编码图片的总Y样本数、该尺寸最终Y TU数、公共资格TU数及实际出现flag的TU数，分别记录面积。
- 最终GTT TU数及覆盖面积。
- 最终有效核路径和τ分布。
- 非零数、last分布；必要时分intra/inter。

统计必须来自最终写出，不把候选试算次数混入实际使用率。

分母固定如下：`eligible_area` 是满足公共资格和尺寸mask、尚未排除CBF=0/TS的最终Y TU面积；`flag_area` 是真正发出flag的面积，必须等于 `total*N*N`；`gtt_area=ones*N*N`。下列“适用面积”统一指 `flag_area`，另外报告eligible_area以观察CBF/TS筛选影响。图片总面积按实际编码层/图片累加，不用容器帧数或输入文件长度代替；若图片边缘存在填充，注明采用编码图片亮度网格的面积，TU面积也使用同一网格。

`gtt_flag`只负责实际flag统计，不能由此声称统计了全部Y TU：rootCbf=0、skip或PLT路径可能提前返回。全部TU和公共资格面积需在最终已提交结构上只读收集，每个亮度TU只计一次；不得混入RDO临时结构、双树色度或重复写出试算。统计按EncLib/layer归属并按最终计数周期清零，避免线程共享可写全局计数。

概率分数比特是估计码长，不能叫精确独立输出bit。CABAC串行缓冲会使单次调用的输出字节差无法直接归属于该flag。整条码流总bit另报。

真实Writer在 `encodeBin` **之前**只读 `getCtx().getFracBitsAcess().getFracBitsArray(ctxId).intBits[flag]`，按0/1分别累加uint64分数比特，输出时除以 `1<<SCALE_BITS`。只在 `isEncoding()` 且统计启用时观察。不能调用真实BinEncoder不支持的 `getEstFracBits()`，也不能用会更新模型的 `estFracBitsUpdate()` 再模拟一次该bin；上下文只能由原编码调用更新一次。

重点指标：

\[
R_{\mathrm{flag},N}/\text{全序列Y样本数},
\]
\[
R_{\mathrm{flag},N}/\text{该尺寸适用面积},
\]
\[
R_{\mathrm{flag},N}/R_{\mathrm{整码流}},
\]
\[
\text{有效前缀选择R的TU面积}/\text{适用面积}.
\]

上述面积的分子只计最终GTT TU：τ1不计；τ2要求k2=R；τ3/4要求k2或k3为R。该项称为“有效前缀R选择率”，避免无效后缀奇偶抬高计数。它仍不保证系数发生实际变化：例如τ4、k2=R、k3=I，而G2/G3全零时，K2实际作用于全零向量。

如果报告“实际旋转改变率”，须额外比较同一最终等级反量化后、逆旋转前后的整块系数，并明确分母；此诊断也不能直接证明重建改善或净RD收益。不通过修改等级或重跑候选来制造统计差异。所有分母为0的比率输出N/A。

### 12.2 搜索与机制统计

单独统计搜索调用数（含同一TU的不同预测/划分候选）、DQ调用数、可行候选数、最终胜出数、DQ/宽幅DCT/完整评分耗时。搜索调用数不能直接作为最终TU使用率分母。

在已有搜索数据可复用的范围，记录同一预测/初始状态下GTT与最佳旧候选的完整ΔJ、系数/符号/flag估计成本分项。

G1基础能量比例只作为候选残差的诊断统计，明确分母是被搜索候选还是最终TU；不能把搜索分布当码流分布。若需要最终winner的基础能量，应保存对应诊断或在单独诊断运行重算，不污染性能比较。

统计0/1必须产生相同码流。新增更重诊断可分独立诊断开关，正式计时关闭重诊断，并对anchor采用相同测时条件。

## 13. 实验 2 矩阵与登记

### 13.1 子实验编号与主矩阵

模式表是编号到配置的唯一映射，所有正编号均属于总实验2。编码配置中的GTT值与子实验编号相同，算法revision另列，不能替换编号含义。

| 标记/GTT值 | 名称 | 协议/尺寸 | sizeMask | groupMode/分组 | 用途 |
|---:|---|---|---|---|---|
| 0（关闭） | 关闭恢复 | GTT4x4=0且GTT=0 | 0，无payload | 无 | 对齐正式anchor，不计作子实验 |
| 1 | 8×8主试 | v3、8-only | 0x02 | 0，16/16/16/16 | 第一主试 |
| 2 | 16×16主试 | v3、16-only | 0x04 | 0，64/64/64/64 | 第二主试 |
| 3 | 32×32主试 | v3、32-only | 0x08 | 0，256/256/256/256 | 第三主试 |
| 4 | 16×16固定首CG对照 | v3、16-only | 0x04 | 1，16/80/80/80 | 与子实验2比较分组影响 |
| 5 | 32×32固定首CG对照 | v3、32-only | 0x08 | 1，16/336/336/336 | 与子实验3比较分组影响 |
| 6 | 4×4迁移对照 | v3、4-only | 0x01 | 0，4/4/4/4 | 核对通用实现与冻结实验1算法 |

编号不是实施先后顺序。实现阶段应先用子实验6做4×4差分核验，再逐步打通1→2→3；正式收益主比较是1/2/3。4/5是独立分组对照。8×8固定首CG与等分相同，不重复编号或重复跑，不把不同组法的结果合成一个“尺寸收益”。

优选尺寸组合从7起继续编号，具体组合待独立结果后登记。本修订未定义7对应哪个mask，因此当前请求7必须报错。组合不预设全部开启：可能只组合8/16，也可能包含32；须依据净RD、复杂度与色度稳定性，独立收益不能直接相加。

各子实验的正式BD-rate均对同一个已登记Current anchor计算；4对2、5对3、6对冻结实验1仅为机制/迁移对照，须另列比较对象。实验1工作簿Reference获准作为实验1 anchor，并不自动确认其可用于实验2；运行准备阶段先登记实验2的源码、二进制、配置、序列、QP、帧数和采样规则，核对可比性后方可复用或另行准备。当前实验2正式anchor尚未登记。

### 13.2 第一批建议集合

沿用当前已有可比较集合，优先：

- LB：Class C四序列和Class E三序列。
- RA：Class C四序列。
- QP：22、27、32、37。

子实验1/2/3共(7+4)×4×3=132个编码配置点。若同条件anchor和子实验6都需重新配对，各增加44点，共220点；子实验4/5各44点，加入两项分组对照后共308点。旧v2另行重跑如有需要须再单独计数。这些是任务数量估算，不是执行命令或实际已跑数量。

Class B随后补齐标准集合；D、AI、LDP按正式CTC计划扩展。不同尺寸的均值只能对相同完整序列集合比较；未到达点留空，不填0，也不借实验1点补实验2。

帧数、采样、输入指纹从实际实验登记继承；不把旧8帧dry-run示例当正式帧数。RA/LB的时间和BD-rate分开统计。

### 13.3 输出与归档

结果目录按子实验编号固定，尺寸等详细身份保存在各自记录中；未来7起沿用相同规则。以下为待收件目录约定，不创建空结果或伪造数据：

```text
experiments/experiment_002_gtt_sizes/
  anchor/     # 已核验的正式对照及其身份
  1/          # 8×8等分
  2/          # 16×16等分
  3/          # 32×32等分
  4/          # 16×16固定首CG
  5/          # 32×32固定首CG
  6/          # 4×4等分迁移对照
```

继续复用scripts/batch_test.py。任务CSV的 `experiment` 列使用 `exp2_1`…`exp2_6`（anchor为 `exp2_anchor`），利用现有分组、输出目录和续跑指纹；`xlsm_tag` 仍为ra/lb等配置标签，不能用子实验号替换。所有子实验共享任务池，分组完成后即时出表，不在运行期间修改二进制/配置/输入。原始日志和运行证据放 `runs/`，结果副本收进上述目录。

每个编号目录独立复制已核验Reference的 `results.xlsm`，将该子实验CSV按序列/QP/配置写入Test，保留Reference和VBA，缺点留空并清缓存。多个子实验不得竞争写同一个Test页，也不直接合并为重复序列/QP/配置键的单份导入CSV。沿用现有导入脚本与四点PCHIP实现；先分别计算Y/U/V BD-rate，再按(6Y+U+V)/8；单QP替换继续按完整anchor曲线替换一整组RD元组。

`Experiment_Results.md` 按“实验2/子实验N”记录身份、原始数据、整体及逐QP BD-rate；`Experiment_Analysis.md` 单独写分析。设计、结果和分析文档按已有授权上传Git，工作簿/CSV/码流/大日志留本地。

报告必须含：

- Y/U/V与6:1:1，序列均值与类别均值分开。
- 编码/解码时间比及明确聚合方法。
- 实际flag的估计成本、覆盖面积、有效前缀R选择率；如有实际系数改变率，单列定义。
- 负面序列和U/V异常点，不删除不利样本。
- 源码、配置、二进制、输入、帧数、采样、状态和hash证据。
- 实验1结果保留，实验2另开条目。

不能仅凭统计格式示例、编译宏或外部CSV pass认定实际启用/一致性已验证。不能把固定4×4与8×8拆块结果比较为一个相同工具成本分析。

## 14. 验证门槛

### 14.1 合成与单元验证

| 类别 | 必须覆盖 |
|---|---|
| 布局 | 各尺寸扫描双射、精确组边界、8×8 CG布局、16/32固定首CG布局 |
| 数值 | 所有路径、负数半格、超过±1023残差、反量化边界与逆后55929 |
| 变换 | 宽幅正式核与int64参考逐点一致；原SIMD窄化不得进入宽输入路径 |
| 前向范围 | 完整Pel极值、宽中间量、32点第二遍点积及舍入偏置；32位快核需更紧范围证明 |
| 不可逆性 | 使用已知整数黄金结果，不把任意正逆精确还原当断言 |
| DQ历史 | 同物理态不同奇偶跨CG保留不同历史 |
| CG skip | 物理1/2跳过整CG不互换；与16个零轨迹一致 |
| 跨组奇偶 | 16/32逻辑组内部CG不清奇偶；固定首CG不等长边界正确 |
| 零CG | 奇控制组部分CG可跳，整组全零不能满足奇约束 |
| last | CG与逻辑组首尾、0、N²−1及32×32的255/256边界 |
| 预算 | regular-bin预算跨界、Rice状态、原小等级反量化修正 |
| 回溯 | 普通节点与显式skip跳转；实际last/absSum/奇偶独立核对 |
| 失败隔离 | 不可行候选不修改TU，不回退普通量化 |
| 调用隔离 | 4/8/16/32、旧/新DQ、QP与路径交错调用 |
| 语法 | v2/v3、非法mask/version/groupMode、仅一个size开启 |
| context | 按size派生；旧context偏移不移动；Writer/Estimator/Reader一致 |
| 状态复制 | 色度复制不清Yflag、同CU多TU、混合尺寸、最佳缓存恢复 |
| 搜索 | 旧胜/零胜/GTT胜/最后候选胜/旧列表仅一项 |
| 4×4迁移 | 相同残差/QP/lambda/Ctx逐一比较旧、新11类候选的feasible、完整等级、last、absSum、proxyCost和稳定平局顺序；不只比较最终重建 |
| 工具组合 | TS、SBT、NST/MTS、符号预测局部互斥和非适用尺寸不受影响 |
| 模式编号 | 0…6唯一映射；非法值报错；整数不被转换为bool；编号与mask/group/log一致 |
| 参数默认与覆盖 | 宏默认0…6、统计0/1；CLI切模式/关闭；配置与CLI顺序；宏0请求开启失败；旧/新入口最终互斥；关闭不受新尺寸限制 |
| 统计 | Estimator不累计，最终Writer累计；编码前概率只读且不二次更新；total=zeros+ones、flag_area=total*N²、零分母N/A；合成语法统计0/1字节一致 |
| 代价范围 | N²路径累积与宽源/合法QP/lambda端点不溢出 |

可在冻结、无历史相关的合成率模型中用穷举验证联合路由；不能宣称证明真实CABAC历史下全局最优。

参数覆盖用仅构造EncCfg/EncAppCfg的真实解析路径验证，不创建EncLib或启动序列。构建至少覆盖两工具均关闭、仅旧入口、仅新入口、两入口均编入；新入口的不同宏默认值也须验证。合成统计等价与序列统计等价分别记录，后者留待14.2的运行授权。

### 14.2 获得对应运行授权后的序列验收

先关闭恢复，再各尺寸启用和解码hash，随后统计等价、配对时间与四QP RD。短帧只用于正确性；正式收益仍使用登记的完整条件。

合成TU往返、编译、ASan/UBSan和dry-run均不能替代上述验收。若宽幅快核尚未完成，标明参考核状态，不把其时间作为最终复杂度结论。

## 15. Codex 的实施阶段与完成定义

1. P0：读取当前AGENTS/Baseline_Audit，核对实际head、工作树和manifest；不导入其他仓库的实验宏或结论。
2. P1：新增通用layout/算子、两种分组及宽幅DCT参考；保留v2入口与旧测试。
3. P2：完成多CG受约束DQ、historySlot、skip路由、显式trace和代价范围检查。
4. P3：完成v3 SPS/mask/groupMode、按尺寸context、整数模式表、宏/CLI覆盖与编号身份。
5. P4：接入现有帧内/帧间完整评分、共同解码入口和历史隔离。
6. P5：完成宽幅正式快核、统计、按1…6编号的独立配置和任务manifest。
7. P6：宏关闭/开启构建、参数解析、合成验证、合成语法统计等价及dry-run，记录实际完成项和失败边界。
8. P7：仅在任务范围已授权后执行序列验收和正式实验，登记实验2数据与分析。

代码实现可按8→16→32逐步打通，但对外规格与协议按本文固定，不能实现完8后临时更改另两尺寸的分组/核/符号策略而继续用同一实验身份。

### 可直接交给 Codex 的任务指令

以下是后续获得实现授权时的交接文本，不将当前“设计实验2”请求自动扩大为修改算法：

```text
在 zhy2249/HHI_baseline 的当前 GTT 实验分支上实现实验2，先读取并遵守仓库AGENTS和本设计规格。

保留实验1协议2与4×4行为，增加协议3的4/8/16/32尺寸掩码及SPS级分组方式。主实验每次只开启一个尺寸，未开启尺寸不写GTT flag，也不执行新增零候选或GTT搜索。

以HHI_GTT/GTT整数值标记子实验：0关闭，1=8等分，2=16等分，3=32等分，4=16固定首CG，5=32固定首CG，6=4等分迁移。尺寸和组法只能由模式表派生。宏非零编入全部已登记模式且默认选择同号模式；CLI可切换或关闭，宏0显式开启报错。编号带入启动/统计日志、配置文件、任务分组和结果目录，7起待新设计登记后才能使用。

实现完整N×N固定整数DCT-II、原扫描上的两级I/R724旋转、最终等级奇偶控制和规范11类last-group搜索。主分组四等分；明确实现16/32固定首CG的分组对照，8不重复，4禁止该对照模式。

多CG DQ保持物理DQ1，用4×2联合槽、三组State bank和两代8槽独立历史；分离physicalStateId与historySlot；使用真实CG语义；整CG skip保持物理态和累计奇偶，逻辑组边界才检查/重置奇偶。回溯显式处理16系数跳转，扫描位置不能用uint8_t。

新增GTT专用宽幅DCT入口，避免旧SIMD把逆旋转55929或前向宽中间量提前pack为int16。黄金参考与正式快核的矩阵、位移、舍入、阶段clip必须逐点一致，不全局关闭旧SIMD，不在旋转后额外裁剪。

复用当前帧内/帧间完整评分和恢复事务，保留GTT胜者历史隔离与仅GTT亮度的符号预测关闭。宏与统计宏相邻，运行默认分别跟随宏，旧/新编码工具最终配置互斥。

输出每尺寸真实flag0/1计数及1占比、编码前概率估计码长、分别定义的覆盖面积、有效前缀R选择/τ、搜索次数和耗时。实际系数改变率另作诊断，不与R选择率混同。保持统计开关码流等价。

完成代码、构建、必要合成测试和独立尺寸任务dry-run，按实际证据更新文档。不要自动更新baseline manifest，不声称获得压缩收益。序列编解码另按明确运行授权执行；设计文档按AGENTS既有授权提交并推送，其他上传按用户已明确授权的范围执行。
```

## 16. 结果应如何解释

实验2可以支持以下判断，但每项需要对应数据：

- flag/适用像素下降、净RD改善：支持尺寸摊薄成本的解释。
- flag密度下降但有效前缀R选择率很低：检查G1冻结区域和实际尺寸覆盖。
- R选择率较高但局部净RD弱：先检查系数是否实际改变，再检查配对相关性、奇偶约束、符号成本和proxy候选。
- 局部RD改善但整序列/色度波动：结合逐帧、模式与预测链分析，不仅凭一项指标判bug。
- 净RD改善但搜索时间高：优化等价缓存、量化预计算和宽幅快核，再单独研究搜索剪枝。
- 某一尺寸失败：只说明该尺寸、核集合、分组和搜索修订下的结果，不能直接否定一般树状变换。

保持实验1与实验2身份独立，不能用小规模闭环结果预先保证收益，也不因实验1收益接近零而取消合理的大尺寸验证。

## 17. 本轮设计审查记录

2026-11-01的 `e2-design-r1` 完成了子实验1…6映射、宏/CLI整数选择语义、身份与归档规则的设计；补充前向宽幅界、4×4逐候选迁移检查，以及flag概率观察和旋转选择统计的明确口径。核对了当前源码的DQ历史/CG skip、变换入口、SPS、CABAC时序、context、配置和批处理分组，未修改算法源码。

静态核验：基线manifest字节未变；检查器报告的33项相对fixed baseline差异全部对应已提交的实验1改动，不是实验2实现。实验源码/配置与7586bbc一致。六项编号的尺寸、mask、组长和前向范围算术核对通过，文档diff格式检查通过。

本轮未执行新实现编译、算法合成测试、任务dry-run或序列编解码。第14、15节仍是后续实现与验收要求；当前无实验2运行结果或收益结论。

## 附录 A：64×64 的预留设计

数学上继续等分时，L=4096、M=1024、CG总数256，每逻辑组64个CG；仍4条路径、11类候选，累计扫描上限33792。固定首CG对照则为16/1360/1360/1360，CG数1/85/85/85。

此工程的MAX_NONZERO_TU_SIZE=256，不存在可直接套用的“64×64必然只保留32×32非零区域”前提。[S15]

本版不启用64的理由是控制首轮范围，并先验证多CG求解、宽幅快核和资源开销。后续启用时检查SPS实际最大TU、4096位置/累积代价、workspace、真实覆盖率及64点快核。必须以新的明确协议定义登记保留位的用途，不静默重新解释本版拒绝的mask位。

不能以64的理论flag密度更低直接推断收益更高；首组1024系数冻结尤其需要覆盖和能量诊断。

## 附录 B：主要源码与证据

所有链接均固定到本次审查提交；后续实施若head变化，先检查差异，不直接假定行号和行为仍相同。

- [S01 实验1结果与证据范围](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/docs/experiments/Experiment_Results.md)
- [S02 实现身份、统计与验证记录](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/docs/experiments/GTT4x4_Implementation.md)
- [S03 CABACWriter](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/EncoderLib/CABACWriter.cpp)
- [S04 扫描与ROM](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/Rom.cpp)
- [S05 资格和公共工具互斥](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/UnitTools.cpp)
- [S06 GTT4x4整数算子](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/Gtt4x4.cpp)
- [S07 DepQuant](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/DepQuant.cpp)
- [S08 DCT矩阵](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/RomTr.cpp)
- [S09 变换内核](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/TrQuant_EMT.cpp)
- [S10 SIMD内核](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/x86/TrafoX86.h)
- [S11 SPS读取](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/DecoderLib/VLCReader.cpp)
- [S12 IntraSearch](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/EncoderLib/IntraSearch.cpp)
- [S13 InterSearch](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/EncoderLib/InterSearch.cpp)
- [S14 EncCu历史](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/EncoderLib/EncCu.cpp)
- [S15 CommonDef尺寸定义](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/CommonDef.h)
- [S16 当前候选生成](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/EncoderLib/GttSearch.cpp)
- [S17 当前共同变换入口](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/TrQuant.cpp)
- [S18 当前context参数](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/source/Lib/CommonLib/Contexts.inl)
- [S19 协作规则](https://github.com/zhy2249/HHI_baseline/blob/7586bbc3335e5bd7f3de7f2463f207ad3e134044/AGENTS.md)

本文件提供算法与实施合同。文中的实验规模、预留配置和验收项目是计划，不是已经完成的实验结果。

