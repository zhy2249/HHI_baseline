# 基线构建与运行准备

以下命令在仓库根目录执行。正式编码命令仅供用户服务器使用；本次准备工作未执行视频编解码。

## 源码检查和构建

要求 Python 3.10 或更新、CMake 3.17 或更新、支持 C++17 的编译器；使用 presets 时要求 CMake 3.21。批量脚本和 Excel 操作只依赖 Python 标准库。

```bash
python3 scripts/baseline_manifest.py
python3 -m unittest discover -s scripts/tests -v
cmake -S . -B build/baseline -DCMAKE_BUILD_TYPE=Release
cmake --build build/baseline --target EncoderApp DecoderApp -j 4
```

Linux 默认生成 `bin/EncoderAppStatic`、`bin/DecoderAppStatic`。名字带 Static 不证明全部依赖静态链接；构建参数另行记录。已有 `build/` 不代表源码当前可用，使用新的子目录并检查构建结果。不要同时构建不同算法到相同顶层 bin 路径；多个实验使用独立 checkout/二进制路径。

本次编译核验额外使用 `-DNX2_ENABLE_LINK_TIME_OPT=OFF`；正式性能比较统一采用所约定构建参数，不混用计时结果。不要设置 `NX2_TOPLEVEL_OUTPUT_DIRS=OFF`：当前上游 Linux POST_BUILD 拷贝依赖顶层分配置路径，本次没有为了此选项修改原始 CMake。

## LB CE 任务预览

```bash
python3 scripts/batch_test.py --preset LBeu --class C,E \
  --input-dir /path/to/videos --qps 22,27,32,37 \
  --out-dir runs/baseline_lb_ce --jobs 10 \
  --decode-md5 --dry-run --allow-missing-input --no-xlsm-report
```

共 28 任务。LBeu INI 已是半帧，不能再次除二；不指定 `--frames` 才沿用每序列自己的 framecount。dry-run 不启动程序，不创建结果。

## 用户服务器正式运行和续跑

先提供可信且范围匹配的 `scripts/JVET-hhi.xlsm`，不要把别的版本的旧结果默认当作 Reference。

```bash
python3 scripts/batch_test.py --preset LBeu --class C,E \
  --input-dir /path/to/videos --qps 22,27,32,37 \
  --out-dir runs/baseline_lb_ce --jobs 10 --decode-md5 \
  --xlsm-report --xlsm-template scripts/JVET-hhi.xlsm
```

默认不保存重建。完成后查看 `summary.csv`、`failures.csv` 和 `reports/LBeu/JVET-hhi.xlsm`。脚本保留公式/VBA，不替 Excel 执行宏或保证公式缓存已重算；在可信模板前提下由用户按原流程计算。

原命令再次执行，只复用指纹和输出都一致的成功点。被中断/失败/身份改变且已有文件的点会拒绝覆盖：新建 CSV 仅列这些点并显式 `--overwrite`，或使用全新输出目录；不要对整组加 `--overwrite` 后以为会只重跑失败点。`--retry-failed N` 只有限次重试本次确实启动后返回失败的任务，不自动覆写启动前检测到的身份冲突。

## 多实验共用任务池

编辑 `scripts/batch_test_jobs.example.csv` 的副本，启用各实验行并填写各自二进制路径。示例第二行默认关闭，避免意外扩大任务。

```bash
python3 scripts/batch_test.py --manifest /path/to/jobs.csv \
  --input-dir /path/to/videos --out-dir runs/round01 --jobs 10 \
  --dry-run --allow-missing-input --no-xlsm-report
```

核对后去掉 dry-run 和 allow-missing-input，按需启用模板。任务按 CSV 顺序进入同一线程池，某组最后一个任务尚未结束时，后续组已可填补空闲名额；每组独立写 `reports/<experiment>/`。组内不同模式使用不同 xlsm_tag，组内相同序列/配置/QP 的重复点拒绝规划。

## GitHub 上传

远端使用用户创建的 `zhy2249/HHI_baseline`，本地目录仍为 `HHI_baseline_fixed_2`。只上传源码、依赖、配置、工具和小体积文档，不上传原始工作簿或视频。默认分支 main；仓库可见性以 GitHub 设置为准。
