# GTT4x4 专用上下文实现与验证记录

## 实现身份

- 基线：`5da139f22ffe9a281d8d9e4f0b7b4b22cba5326b`，NextSoftware2 fixed baseline。
- 分支：`experiment/gtt-nx2high`，实现修订 `context-v2-r3`（功能和统计分别由宏设置运行默认值）。分支名预留其他尺寸的 GTT 扩展；本文记录的已实现范围仍为 4×4。
- 协议版本：**2**。原外部方案的版本 1 是旁路 flag 草案，本实现拒绝它；未同时实现旁路实验模式。
- 本轮按用户明确要求实现专用 CABAC context，并统计最终实际写出 flag 中 1 的占比。
- `TypeDef.h` 中 `HHI_GTT4X4`、紧随其后的 `HHI_GTT4X4_STATS` 默认均为 **0**。`GTT4x4` 和 `GTT4x4Stats` 分别跟随对应宏设置运行默认值，配置和命令行可覆盖。
- 原外部方案、既有修复宏和 baseline manifest 保留。

## 实际算法与接线

实现整块亮度 4×4 固定 DCT-II、四组扫描坐标上的 724/Q10 旋转、最终等级奇偶选核，以及专用单 CG 受约束 DQ。编码端按 τ=1..4 枚举最多 11 类非零候选；解码端从最终等级派生核，执行原整 TU DQ 反量化、逆旋转和固定逆 DCT。

公共量化仍保留本基线的小等级反量化修正，不简化成 `level×step`。混合预测可能超出普通 10 bit 样本差值范围，前向不强制裁剪到 ±1023；固定 4 点变换覆盖完整 Pel 输入。新 DQ 分支按实际源系数幅度设置代理失真的定点精度，避免宽源乘积溢出，原量化路径不变。

帧内在原变换循环之外增加规范零候选和 GTT 候选，共用原预测、重建、BIF/LMCS、失真及语法估价；新增通道完整保存并恢复旧胜者。帧间第三轮独立于旧两轮的 CBF0/TS 提前退出，复用原评分与分量恢复。GTT 胜者不会以 DCT2 占位值写入旧 SBT/MTS 胜者历史。

每个旧/GTT 候选从相同的完整 CABAC Ctx 开始；新 context 随原 Ctx 初始化、更新、同步和回滚。既有预测/变换预筛仍为原近似模型，不承诺穷举全部预测和等级历史。

关键文件：

| 范围 | 文件 |
| --- | --- |
| 共同整数算法及数据契约 | `CommonLib/Gtt4x4.h/.cpp` |
| 模式生命周期与资格、局部工具互斥 | `Unit`、`UnitTools` |
| 固定 DCT、受约束 DQ、公共逆入口 | `TrQuant`、`DepQuant` |
| 候选调度、安装与真实评分 | `EncoderLib/GttSearch`、`IntraSearch`、`InterSearch` |
| 历史隔离 | `EncoderLib/EncCu.cpp` |
| 专用 context 与语法 | `Contexts.h/.inl`、CABAC/VLC Writer、Reader、SPS |
| 参数、身份与最终统计 | `EncCfg`、`EncAppCfg`、`EncLib` |

Lib 文件均位于 `source/Lib/`。

## 专用上下文与出现条件

新增一个共享的 `Ctx::GttFlag`，在所有旧 context 定义末尾追加，旧编号和初值不移动。B/P/I/L 四类初始化使用初值 35；更新窗口、权重和速率偏移使用当前工程的 DWS/DWE/DWO，完整 20 行参数已冻结。它是未训练的起始参数，不代表实测最优。

写出条件仍为：SPS 开启、合法 4×4 亮度 TU、CBF=1、非 TS，并满足 DQ1、内部 10 bit、动态范围 15 等共同条件。适用的旧 DCT/NST/MTS 写 0，GTT 写 1；其他 TU 不产生该 flag。两端在 TS 之后、last 之前分别调用 `encodeBin/decodeBin`，估价器使用相同 context；不再固定计一比特。

宏关闭构建仍识别参数并拒绝开启请求，识别 GTT SPS 后明确拒绝；宏开启但运行关闭不写新 SPS payload/TU flag。

## 最终占比日志

统计由每个 EncLib 实例持有，仅在真实 Writer 的 flag 出现位置累计。CABACEstimator 和候选试算不累计。完成汇总时每层输出一行，例如下列**格式示例**：

```text
GTT4x4_FLAG_STATS layer=0 total=200 ones=10 percent=5.000000%
```

- `total`：实际写出的 GTT flag 总数，即 0 和 1 的总和。
- `ones`：最终写出的 GTT flag=1 数量。
- `percent`：`100 × ones / total`。不是全部 TU 中 GTT 的占比。
- 无出现位置时输出 `total=0 ones=0 percent=N/A`。

身份行包含 `compile`、`enabled`、`flag=context-1`、`stats_default`（统计宏默认值）和 `stats`（覆盖后的运行值），开启时 `version=2`，关闭时 `version=0`。计数只观察写出结果，不改变概率更新、模式选择或码流。

## 构建和开启方式

在独立实验 checkout/副本中，将 `source/Lib/CommonLib/TypeDef.h` 的 `HHI_GTT4X4` 显式改为 1，再构建，即可默认启用 GTT，无需另加 `--GTT4x4=1`。主工作区保持默认 0。CMake 选项只控制构建，不决定算法默认值。

```bash
cmake -S . -B build/gtt4x4-release \
  -DCMAKE_BUILD_TYPE=Release \
  -DNX2_ENABLE_LINK_TIME_OPT=OFF \
  -DGTT4X4_BUILD_TESTS=ON
cmake --build build/gtt4x4-release \
  --target EncoderApp DecoderApp Gtt4x4UnitTests -j 4
ctest --test-dir build/gtt4x4-release --output-on-failure -R Gtt4x4
```

宏 0 构建仍可运行 SPS/拒绝行为的语法测试；需算法开启的测试明确跳过，不冒充算法验证。

宏 1 的二进制可在原 nx2High 配置之后使用 `--GTT4x4=0` 临时关闭，使用 `--GTT4x4=1` 显式开启。配置文件和命令行按读取顺序覆盖默认值，因此覆盖参数应放在 `-c` 配置之后。宏 0 仍不编译 GTT，显式请求开启会报错。

统计开关独立设置：`HHI_GTT4X4_STATS=1` 默认输出最终占比，`=0` 默认不输出；命令行 `--GTT4x4Stats=0/1` 可双向覆盖。统计宏只决定默认值，不裁剪统计实现；统计实现仍随 `HHI_GTT4X4=1` 编译。工具宏为 0 时没有 GTT 统计实现或结束统计行。

例如在源文件中设置如下即可默认启用工具及统计，统计宏紧跟开启宏：

```cpp
#define HHI_GTT4X4       1
#define HHI_GTT4X4_STATS 1
```

也可叠加 [gtt4x4_on.cfg](../../cfg/experiments/gtt4x4_on.cfg)，显式开启工具与统计：

```ini
GTT4x4       : 1
GTT4x4Stats  : 1
```

性能计时时可单独覆盖 `--GTT4x4Stats=0`。配套解码器必须使用宏 1、版本 2 实现。不要让不同宏或 Debug/Release 构建覆盖同一 checkout 的顶层 bin/lib。

## 任务预览

[预览 CSV](GTT4x4_jobs_preview.csv) 使用四套根目录 nx2High 配置，每套 BasketballPass、QP22/27/32/37、8 帧，共 16 任务；按配置分组，共享任务池。它不是正式 CTC 登记或运行授权。

```bash
python3 scripts/batch_test.py \
  --manifest docs/experiments/GTT4x4_jobs_preview.csv \
  --input-dir /path/to/videos \
  --encoder /absolute/path/to/macro1/bin/EncoderAppStatic \
  --decoder /absolute/path/to/macro1/bin/DecoderAppStatic \
  --out-dir runs/gtt4x4_preview --jobs 1 \
  --dry-run --allow-missing-input --no-xlsm-report
```

已执行该 dry-run，任务数、配置、分组及无重建输出符合预期。脚本的 `EXPERIMENT: none` 是因为 dry-run 不执行二进制探测；不能据此判断宏状态。实际运行需输入和相应授权，不直接移除 dry-run 开跑。

## 验证证据与限制

修改前 baseline manifest 为 PASS：2486 文件，0 差异。修改后核验为 2492 文件、33 项预期差异，均对应本次源码、新配置和构建文件；manifest 本身未修改，未重新定义基线。

已通过的 Release 检查：

- GNU 11.4、C++17、LTO 关闭，宏 0 和宏 1 的 EncoderApp、DecoderApp 均构建成功。
- 宏 0 语法测试：旧 SPS/REXT 往返字节一致、版本拒绝；未与未经修改的 baseline 码流比较。
- 宏 1 四组测试：共同整数与生命周期、受约束 DQ、SPS/专用 CABAC context、候选生成至系数读写和独立逆变换。
- 合成 flag 序列统计开/关的输出字节相同，Estimator 不增加计数。
- 受约束 DQ 覆盖16个精确 last、11类约束、QP端点、零源奇控制、正负等级、宽源、stride、失败隔离及独立反量化状态轨迹。
- Common、Quant、Search 分别通过 152、17278、656 项断言；Syntax 单独覆盖宏 0/1 的协议行为。Quant 还将 288 个受控、无历史相关代价模型下的生产状态路由与独立全向量穷举比较，包含必须保留较贵奇偶路径的反例；这不证明真实 CABAC 历史相关代价的全局最优。

Debug 的 ASan/UBSan 四组测试也已通过，但采用了独立验证副本，并有以下边界：

- 默认 SIMD 的初次执行在进入测试主体前遇到基线 `AdaptiveLoopFilterEcmX86.h:2348` 的负数左移，随后在验证副本设置 `NX2_ENABLE_X86_SIMD=OFF`。
- 标量构建暴露基线 `TrQuant_EMT.cpp:345` 的 `rnd_factor` 拼写错误，以及 `Rom.cpp` 几何模板初始化的三处负数左移。仅在验证副本修正拼写并将这些左移改成等价乘法，主工作区的两个文件保持原样。完整临时差异在 `runs/gtt4x4_validation/asan-validation-only.patch`。
- 本环境的 LeakSanitizer 因跟踪环境限制而终止，最终以 `ASAN_OPTIONS=detect_leaks=0`、`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1` 执行。内存泄漏检查未完成；上述结果不能表述为未经调整的 SIMD 基线通过全部消毒器。

Debug 配置使用 `-DCMAKE_BUILD_TYPE=Debug -DNX2_USE_ADDRESS_SANITIZER=ON -DNX2_ENABLE_X86_SIMD=OFF -DNX2_ENABLE_LINK_TIME_OPT=OFF -DGTT4X4_BUILD_TESTS=ON`。初始失败及最终结果均保存在本地验证日志目录。

本地构建、测试和预览日志在 `runs/gtt4x4_validation/`。测试均使用合成对象，没有调用 EncoderApp/DecoderApp 编解码视频。未执行序列级关闭恢复、真实帧级 hash、真实素材下最终使用率、BD-rate 或性能测量；完整 CU 决策、缓存及工具组合仍需后续序列验收。合成 TU 往返不能替代这些验收。

### 2026-10-30：宏控制运行默认值

`context-v2-r2` 仅调整运行默认值与使用说明，协议仍为版本 2。宏 0/1 的 Release 编解码器重建成功；宏 1 四组测试通过，宏 0 语法测试通过、其余三组按约定跳过。

另外使用临时独立程序调用真实 `EncAppCfg::parseCfg`，宏 0/1 各检查 10 种默认、显式开关、配置/命令行先后覆盖及独立统计设置，共 20/20 通过。16 个接受配置的实际身份行与最终值一致；宏 0 最终请求开启的 4 个案例明确拒绝。程序只构造配置对象和解析器，没有构造 EncApp/EncLib 或运行视频编解码。日志、案例参数和二进制指纹在 `runs/gtt4x4_macro_default_validation/`。

### 2026-10-31：统计宏控制统计默认值

`context-v2-r3` 新增紧接工具宏的 `HHI_GTT4X4_STATS`，仅控制统计运行默认值，命令行和配置均可覆盖为 0 或 1；计数位置、分母、码流语法及协议版本保持不变。身份行新增 `stats_default`，用于区分宏默认值和最终 `stats` 值。

工具/统计宏组合 0/0、1/0、1/1 的 Release EncoderApp、DecoderApp 均构建成功。两种工具开启配置的四组测试均通过，工具关闭配置的语法测试通过、其余三组跳过。真实参数解析器的 27 个案例全部通过：26 个接受配置的身份日志符合预期，1 个工具未编译却请求启用的案例按预期拒绝；覆盖统计默认值、双向覆盖、配置读取顺序及工具运行关闭时统计参数的独立性。

本次未运行视频编解码，参数验证不代表已测结束统计输出或真实使用率。日志、案例参数、源码副本及二进制指纹在 `runs/gtt4x4_stats_default_validation/`。
