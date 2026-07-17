# RETICOLO MCP — 基于 MATLAB Engine API 的 RCWA 求解器 MCP 接口

[English](README.md) | 中文

基于 [RETICOLO V10](https://zenodo.org/records/14631951) 严格耦合波分析（RCWA）
求解器的 MCP Server。通过 MATLAB R2025b Engine API 封装，agent 可直接计算 RCWA
光谱、收敛扫描和场分布，无需启动 MATLAB 桌面。

## 快速开始

```powershell
# 1. 创建 conda 环境
conda create --name reticolo-mcp python=3.11 -y
conda activate reticolo-mcp

# 2. 安装依赖
pip install mcp numpy pydantic

# 3. 安装 MATLAB Engine API（一次性）
cd "D:\Program Files\MATLAB\R2025b\extern\engines\python"
pip install .

# 4. 安装本包
pip install .

# 5. 设置 RETICOLO 路径并运行
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server
```

### opencode / Codex 配置

```json
{
  "reticolo": {
    "type": "local",
    "command": ["D:\\condaenvs\\reticolo-mcp\\python.exe", "-m", "reticolo_mcp.server"],
    "cwd": "D:\\reticolo_runtime",
    "environment": { "RETICOLO_MCP_DIR": "D:\\RETICOLO V10\\V10_2025\\reticolo_allege_v10" },
    "enabled": true,
    "timeout": 120000
  }
}
```

`cwd` 必须是源码仓库外的纯 ASCII 路径。若从源码树启动，源码会遮蔽 non-editable
安装，部署 receipt 将报告 `source_tree`，不能作为 installed-package 验收。

## 工具

| 工具 | 状态 | 说明 |
|---|---|---|
| `reticolo_capabilities` | 已验证（无需求解器） | 工具成熟度、schema 与构建身份 |
| `reticolo_resource_preflight` | 已验证 solver-free + staged real | 哈希 admission 与 nn=9/15/21/31 真实门禁 |
| `solver_status` | 已验证（只读） | 租约状态 + COMSOL 碰撞检测 |
| `reticolo_status` | 已验证（只读） | 引擎句柄与租约状态 |
| `reticolo_start` / `reticolo_stop` | 已通过真实生命周期验收 | 三轮清理、启动回滚与 >90 s heartbeat ownership 均通过 |
| `reticolo_solve_point` | 已验证 TE/TM 单点 translation | 正入射、signed-angle 解析/direct fixtures 与 patterned TE |
| `reticolo_sweep` | 实验性、默认禁用 | 旧同步扫描；优先使用持久化 job |
| `job_submit/status/tail/cancel/resume` | 实验性 | 真实 restart/resume 与安全边界 cancel 收据已通过；接口仍为实验性 |
| `reticolo_convergence` | 实验性 | MCP 执行尚未通过发布验收；外部归档证据不能提升其成熟度 |
| `reticolo_field_export` | 实验性；已验证均匀 TE artifact | 有界 `res3` 导出通过；paired mode comparison 尚未验收 |
| `reticolo_field_pair` | 实验性；真实 artifact pair 已验证 | 两个带哈希的均匀 TE artifact 已通过调用方有界坐标匹配和共享色限组装；不做模式分类 |

实时成熟度和部署身份以 `reticolo_capabilities` 返回值为准。以下真机结果是
历史基准证据，不会自动把当前所有工具版本提升为“已验证”。

同步扫描、收敛和场导出默认禁用。仅开发用途可设置
`RETICOLO_MCP_ENABLE_EXPERIMENTAL=1` 后重启 MCP host；该开关不会把工具提升为
“已验证”。

启用实验性场导出后，artifact 只能写入 `RETICOLO_ARTIFACT_DIR`（默认
`<runtime>/artifacts`）及其子目录。

单点入射使用 signed `theta_deg` 与以度为单位的 `azimuth_deg`。传给 RETICOLO 的
参数为 `ro=n_superstrate*sin(theta)`；非零角要求正实数、均匀的入射介质。持久化 job
当前仍仅支持正入射。

持久化 `job_submit` 必须携带显式资源策略。warning 结果需要使用返回的
`decision_hash` 再次确认；refuse 结果不会启动 worker。
科学验收策略也由调用方负责：单点、扫描、持久化 job 与收敛工具都要求显式
`passivity_tolerance`；收敛工具还要求 center、absorption、FWHM 与 branch-match
容差。Field export 要求 `slice_tol`，field pairing 要求有安全上限的
`coordinate_tolerance_um`。这些值进入 artifact 或持久化身份，不再由服务端默认。

## 验证

non-editable 安装后，从源码仓库外的纯 ASCII 目录验证真实 stdio transport。以下
身份值应来自已审查的构建 receipt：

```powershell
python scripts\verify_installed_transport.py `
  --python "D:\condaenvs\reticolo-mcp\python.exe" `
  --cwd "D:\reticolo_runtime" `
  --reticolo-dir "D:\RETICOLO V10\V10_2025\reticolo_allege_v10" `
  --expected-version "<version>" `
  --expected-tool-count <count> `
  --expected-build-id "<build-sha256>" `
  --expected-schema-id "<schema-sha256>" `
  --output "D:\reticolo_runtime\installed_stdio_receipt.json"
```

该门禁会在全新子进程中完成 MCP 初始化、工具发现和 capability 调用。若 installed
身份/profile 不一致、发现阶段导入 MATLAB，或 MATLAB PID 集合发生变化，门禁会失败。
`--experimental` 仅用于单独声明的 restart-bound profile 检查；随后必须不带该参数
再次重启并验证默认 profile。

可使用 `scripts\audit_external_evidence.py` 在不启动 MATLAB 的情况下审计归档收敛
数据。审计先用 SHA-256 与精确配置身份绑定 manifest、脚本、逐点 CSV 和 summary CSV；
使用必传 `--balance-tolerance` 检查 R/T/A 派生一致性；提供
`--convergence-group-column` 以及 center、absorption、FWHM、数值一致性和最大阶差
策略参数后，再从 raw rows 重建每个峰、
双侧 half-prominence FWHM、Q 和相邻阶 center/A/width 门槛。Provenance 可以通过而科学
验收以退出码 `2` 结束；这种 receipt 是有界 residual，不是执行崩溃，也不能提升工具
capability。

| 阶段 | 证据 |
|---|---|
| G0 — 引擎生命周期 | 启动 → 健康检查 → 停止，无 MATLAB 进程泄漏，无临时文件残留 |
| G1 — M0 资源控制 | nn=9×2 + nn=15×1 点，C 盘 Δ=0 GB，无 `retXXXX` 残留 |
| G2 — 数值基准 | TE 介质平板 n=1.5：R=0.147929 vs 理论值 0.1479（误差 0.03%）；损耗材料被动性验证通过 |
| G3 — 持久作业 | Worker 产出与 G2 完全一致；恢复跳过已完成行 |
| 历史单元测试基准 | v0.2 开发修改前 133 passed |
| M3 — 高阶冒烟 | nn=21（32s）+ nn=31（261s），内存模式稳定，无 OOM |
| M4 — Scratch 模式 | 求解正确，结果与内存模式一致 |
| V2 真实生命周期 | 3/3 循环与启动后 rollback 通过；无 MATLAB/lease/scratch 残留 |
| V2 长 heartbeat | 阻塞 100.016 s；约 30/60/90 s 持续更新；95 s contender 被拒绝 |
| V2 TE 解析平板 | raw R/T/A_balance = 0.1479289941 / 0.8520710059 / 2.22e-16；解析误差 < 2e-16 |
| V2 损耗平板 | raw R/T/A_balance = 0.0030686604 / 0.8847234795 / 0.1122078601；解析误差 < 3e-16 |
| V2 patterned translation | 三组 direct/wrapper ledger 全部 exact；Sun M5 raw R/T/A_balance = 0.8439529179 / 2.2009066e-6 / 0.1560448812 |
| V2 staged resources | nn=9/15/21/31 全部 green/passive；耗时 0.936/5.616/28.800/227.452 s；lease 全程保持 |
| V2 durable restart/resume | host 退出后 worker 保持运行；精确首点恢复为无重复的两行 passive 结果 |
| V2 安全边界 cancel | 飞行中的 nn=21 点先持久化；下一点 admission 前停止并证明清理完成 |
| 外部 Xu 收敛审计 | 1346/1346 行完成绑定；7/7 组重建 center/A/FWHM 收敛；不属于 MCP 执行 |
| 外部 Sun 收敛审计 | 170/170 行 provenance 通过；summary 缺少 FWHM 证据，科学契约被拒绝 |

## 已知限制

- **收敛：** MCP convergence 路径仍为实验性且默认禁用。归档 Xu 证据通过了独立的
  三指标重建；归档 Sun summary 缺少 width 契约。两者都不能提升 MCP 执行成熟度。
- **场导出：** 旧 `imag(apod)` 失败来自把 `ef` 传给 RETICOLO 单参数
  `retchamp` apodization helper。修正后的有界 `res3` 路径已有均匀 TE artifact
  通过真实验收，并嵌入 source/config/point/request 身份。一次 solver-free recovery
  使用调用方给定的 `1e-12 um` 坐标容差配对了两个真实 artifact，实测最大差为
  `5.55e-17 um`。TM、共振模式分类、视觉模式结论和 publication claim 均未验收。

## ⚠ 磁盘安全

RETICOLO 内置的 `retio` 系统在高阶扫描时会将大矩阵写出到 `retXXXX/` 临时
目录。本服务器默认使用**内存模式**（`vmax=inf`，不写磁盘），MATLAB 临时文件
重定向到 `D:\matlab_temp`。

启动时自动执行：
- `retio([], inf*1i)` — 禁用磁盘写入
- `TMP/TEMP/TMPDIR` → `D:\matlab_temp`
- 工作目录 → `D:\reticolo_scratch`
- 与 COMSOL MCP 的租约碰撞检测

## 配置

服务器需要外部的 RETICOLO V10 安装目录。仓库中的 `reticolo_v10/` 仅供开发使用，
**不包含在 PyPI wheel 中**（CC-BY 4.0 协议与 MIT wrapper 分离）。

```powershell
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server --reticolo-dir "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
```

## 架构

```
reticolo-mcp/
├── src/reticolo_mcp/
│   ├── server.py        # FastMCP 服务端，全部工具
│   ├── engine.py        # MATLAB 引擎生命周期 + 求解
│   ├── lease.py         # 原子求解器租约（命名互斥锁）
│   ├── sweep.py         # 可恢复扫描 + 峰值分析
│   ├── jobs.py          # 持久作业存储（spec/state/events）
│   ├── worker.py        # 独立工作进程
│   ├── convergence.py   # 渐进谐波阶数收敛
│   ├── evidence_audit.py # 无需求解器的归档证据与 claim 重建
│   ├── field_export.py  # 有界 res3 场数据导出
│   ├── schema.py        # Pydantic 材料/几何数据模型
│   ├── config_hash.py   # 规范 SHA-256 配置标识
│   └── config.py        # 路径、限制、环境变量
├── reticolo_v10/        # 内置 RETICOLO V10（CC-BY 4.0，仅开发用）
├── tests/               # 单元 + 集成测试
├── pyproject.toml
├── LICENSE              # MIT（wrapper 代码）
├── NOTICE               # CC-BY 4.0 署名
└── README.md
```

## 许可证

- **MCP wrapper 代码**（所有 Python 文件）：MIT License。
- **内置 RETICOLO V10**（`reticolo_v10/`）：CC-BY 4.0，
  © Jean Paul Hugonin & Philippe Lalanne。
  DOI: [10.5281/zenodo.14631951](https://doi.org/10.5281/zenodo.14631951)。

详见 LICENSE 和 NOTICE 文件。
