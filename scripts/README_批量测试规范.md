# 批量测试运行脚本规范

本目录用于沉淀项目正式批量测试脚本。`scripts_参考/` 仅作为历史参考目录；后续新增批量运行需求，默认复用 `batch_test.py` 的参数、任务清单和输出目录规范，不再把正式脚本放入 `scripts_参考/`。

## 编码与风格

- Python 脚本统一使用 Python 3.10 或更新，文件按 UTF-8 保存；仅依赖标准库。
- 入口使用 `argparse`，路径处理使用 `pathlib.Path`。
- 文件读写显式使用 `encoding="utf-8"`；CSV 写入使用 `newline=""`。
- 外部命令使用 `subprocess.run([...], check=False)` 的列表参数形式，避免拼接 shell 字符串。
- 批量任务使用 `ThreadPoolExecutor` 控制外部进程并行数，默认 `--jobs 1`。

## 推荐入口

```bash
python3 scripts/batch_test.py --dry-run --allow-missing-input
python3 scripts/batch_test.py --preset RAeu --class D --qps 32 --dry-run --allow-missing-input
python3 scripts/batch_test.py --local-preset AI --sequences BasketballPass --qps 32 --frames 8 --dry-run
python3 scripts/batch_test.py --manifest scripts/batch_test_jobs.example.csv --dry-run
```

如果当前机器还没有测试 YUV，可以先加 `--allow-missing-input` 只检查批量规划：

```bash
python3 scripts/batch_test.py --manifest scripts/batch_test_jobs.example.csv --dry-run --allow-missing-input
```

实际运行前需要保证编码器/解码器可执行：

```bash
chmod +x bin/EncoderAppStatic bin/DecoderAppStatic
```

## 三种任务来源

1. 默认 HHI Config*.ini 模式：

不传普通模式参数时，脚本默认使用 `scripts/HHI测试cfg` 下的标准 CTC/HHI 配置，默认 preset 为 `AIeu`：

```bash
python3 scripts/batch_test.py --dry-run
python3 scripts/batch_test.py --preset AIeu --class C,E --dry-run
python3 scripts/batch_test.py --preset RAeu --class D --qps 32 --decode-md5
python3 scripts/batch_test.py --run AIeu:C --run LBeu:E --qps 32 --dry-run
```

HHI 模式默认路径：

```text
scripts/HHI测试cfg/AIeu/ConfigAI.ini
scripts/HHI测试cfg/RAeu/ConfigRA.ini
scripts/HHI测试cfg/LBeu/ConfigLB.ini
```

HHI 模式会读取 `Config*.ini` 中启用的序列、`#Class` 标记、`[Global] qps`、每个序列的 `cfg`、`framecount` 和 `segLen`。如果没有显式传 `--qps`，使用 ini 里的 `[Global] qps`；如果没有显式传 `--frames`，优先使用序列的 `framecount`，否则使用 `segLen`。

LBeu 的 framecount 已是半帧，不再次除二。INI 中的旧服务器 `Cmds`/sourcedir 不被直接执行；实际二进制用 CLI 路径，视频从 `--input-dir` 查找。

2. 普通自动展开模式：

```bash
python3 scripts/batch_test.py \
  --local-preset AI \
  --sequences BasketballPass,BQMall \
  --qps 22,27,32,37 \
  --frames 16 \
  --decode-md5 \
  --jobs 2
```

`--local-preset` 映射：

- `AI` -> `cfg/encoder_intra_nx2.cfg`
- `RA` -> `cfg/encoder_randomaccess_nx2.cfg`
- `LB` -> `cfg/encoder_lowdelay_nx2.cfg`
- `LDP` -> `cfg/encoder_lowdelay_P_nx2.cfg`
- 带 `_HIGH` 的 preset 对应 `*nx2High.cfg`

3. CSV 清单模式：

```bash
python3 scripts/batch_test.py --manifest scripts/batch_test_jobs.example.csv --jobs 2
```

CSV 字段规范：

- `enabled`: `1/0`，是否启用该行。
- `experiment`: 报告分组及隔离输出目录，默认 baseline。仅使用字母、数字、下划线、点、加减号。
- `encoder` / `decoder`: 每组独立程序路径；留空使用 CLI 默认。不得运行途中重新编译覆盖该路径。
- `name`: 任务名，会作为输出子目录名的一部分。
- `cfgs`: 公共 cfg 列表，多个 cfg 用英文分号 `;` 分隔，传参顺序保持不变。
- `sequence_cfg`: 单序列 cfg，例如 `cfg/per-sequence/BasketballPass.cfg`。
- `input`: 输入视频路径；可填绝对路径、项目相对路径，或只填文件名让脚本在 `--input-dir` 下递归查找。
- `qps`: QP 列表，例如 `22,27,32,37`。
- `frames`: 可选，传给 EncoderApp 的 `-f`；留空则不覆盖 cfg。
- `extra_args`: 可选，覆盖默认 EncoderApp 附加参数。
- `decode_md5`: `1/0`，是否编码后解码校验。
- `decoder_args`: 可选，覆盖默认 DecoderApp 附加参数。
- `xlsm_tag`: 可选，写入 `JVET-hhi.xlsm` 时匹配 column A 的条件后缀，例如 `ai/ra/lb`。

`extra_args` 不得重写 `-c/-i/-q/-f/-b/-o` 等受管字段，使用专用字段。外置模型等额外文件通过 `--identity-file` 纳入指纹。同一实验组内同配置/序列/QP 必须唯一。

所有启用行进入同一线程池，不按实验组顺序等待整组结束。默认关闭重建输出（`-o ''`），仅明确传入 `--write-recon` 才生成 `.yuv`。`--decode-md5` 仍可校验，不需要保存解码视频。

## Excel 报表

如果 `scripts/JVET-hhi.xlsm` 存在，每组任务全部返回（含失败）后立即复制模板并回填 `Test` sheet，不等其它组：

```text
runs/batch_test/<run-id>/reports/<experiment>/JVET-hhi.xlsm
```

也可以显式控制：

```bash
python3 scripts/batch_test.py --sequences BasketballPass --qps 32 --xlsm-report
python3 scripts/batch_test.py --sequences BasketballPass --qps 32 --no-xlsm-report
python3 scripts/batch_test.py --sequences BasketballPass --qps 32 --xlsm-template scripts/JVET-hhi.xlsm
```

HHI 模式以 preset 分组并推导 `xlsm_tag`：`AIeu -> ai`，`RAeu -> ra`，`LBeu -> lb`；普通模式为 baseline 组；CSV 按 experiment 分组。同组多个 tag 写入同一工作簿，不同组各自一份。重试改变状态后更新该组报告。`completion.json` 区分组已结束与全部成功。

模板只读，输出不可与模板同路径。首先清空 Test 中旧测量值，只填本轮实测成功数值；缺失和失败不继承模板数据，不以 anchor 补点。Reference 与 VBA 保留。工作簿生成不代表公式/VBA 已重新计算，亦不证明旧 Reference 与新基线可比。原始工作簿不上传 Git。

## 输出规范

默认输出根目录：

```text
runs/batch_test/YYYYMMDD_HHMMSS/
```

每个任务一个子目录：

```text
runs/batch_test/<run-id>/<task-name>/
  <task-name>_QP<qp>.bin
  <task-name>_QP<qp>.yuv
  <task-name>_QP<qp>.encode.log
  <task-name>_QP<qp>.decode.log
```

`.yuv` 仅在 `--write-recon` 时存在，decode.log 仅在要求校验时存在。CSV 清单多一层 experiment 目录。逐点另有 `.task.json`（指纹和命令）及成功后才写入的 `.success.json`。

每个任务完成时原子更新批次汇总：

```text
runs/batch_test/<run-id>/summary.csv
runs/batch_test/<run-id>/failures.csv
runs/batch_test/<run-id>/task_plan.json
```

`summary.csv` 包含分组、指纹、任务名、cfg、输入、QP、输出码流、编码/解码状态、耗时、分量 PSNR、总码率、日志路径和 error_info。失败点不等于测量成功。

## 续跑和边界

续跑只有在成功标记、二进制/配置/命令/显式依赖、视频元数据及输出证据一致时复用。有码流但无匹配成功记录会拒绝覆盖。重试失败点用仅包含失败点的 manifest 配合 `--overwrite`，或者新输出目录；整组 `--overwrite` 会重跑整组。`--retry-failed N` 有限次重试本次启动后失败的任务，不越过已有身份冲突。

一个输出目录只能由一个调度器管理。中断会取消未开始任务，当前子进程可能仍需结束；不承诺强制杀死运行进程。视频默认按路径、size、mtime 标识，不逐字节扫描；运行环境和动态库另行记录，额外外置文件要声明。

## 状态与退出码

- `0`: 全部任务成功或经验证续跑复用。
- `2`: 参数、路径或任务规划错误。
- `3`: 至少一个编码或解码任务失败。
- `130`: 用户中断。

状态字段：

- `ok`: 成功。
- `resumed`: 指纹及输出校验匹配的成功任务。
- `incomplete`: 程序正常退出但缺少完整测量/码流或帧数不符。
- `failed`: 外部命令返回非 0 或 MD5/校验失败。
- `exception`: 脚本运行中出现未捕获到任务层的异常。

## 后续脚本约定

- 新实验优先写 CSV 清单，然后调用 `batch_test.py`。
- 如确需新增脚本，新脚本应只负责生成 manifest 或封装参数，不重新实现编码、解码、并行、日志和 summary 逻辑。
- 所有批量输出继续放在 `runs/batch_test/` 下，避免散落在项目根目录。
- 长任务先使用 `--dry-run` 检查任务数量、输入路径和 cfg 顺序。
