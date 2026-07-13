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
    "environment": { "RETICOLO_MCP_DIR": "D:\\RETICOLO V10\\V10_2025\\reticolo_allege_v10" },
    "enabled": true,
    "timeout": 120000
  }
}
```

## 工具

| 工具 | 状态 | 说明 |
|---|---|---|
| `solver_status` | ✓ | 只读租约状态 + COMSOL 碰撞检测（无需 MATLAB） |
| `reticolo_start` | ✓ | 启动 MATLAB 引擎，获取租约，应用磁盘安全策略 |
| `reticolo_stop` | ✓ | 停止引擎，释放租约，清理临时文件 |
| `reticolo_status` | ✓ | 引擎状态 + 租约状态 |
| `reticolo_solve_point` | ✓ | 单波长求解，返回 R / T / A_balance / 被动性 |
| `reticolo_sweep` | ✓ | 可恢复波长扫描，flus+fyncs CSV，config_hash 恢复 |
| `reticolo_convergence` | ✓ | 渐进阶数扫描，峰值跟踪，FWHM，Q 值 |
| `reticolo_field_export` | ✓ | 场数据导出（retchamp），切片平面，NPZ 输出 |
| `job_submit` | ✓ | 提交持久化分阶段扫描作业 |
| `job_status` | ✓ | 作业状态与进度 |
| `job_tail` | ✓ | 作业事件日志最近 N 条 |
| `job_cancel` | ✓ | 协同取消请求（在两点之间检查，不中断 `res1`） |
| `job_resume` | ✓ | 恢复失败/中断的作业 |

✓ = 已通过 RETICOLO V10 + MATLAB R2025b 真机验证（2026-07-13）。

## 验证

| 阶段 | 证据 |
|---|---|
| G0 — 引擎生命周期 | 启动 → 健康检查 → 停止，无 MATLAB 进程泄漏，无临时文件残留 |
| G1 — M0 资源控制 | nn=9×2 + nn=15×1 点，C 盘 Δ=0 GB，无 `retXXXX` 残留 |
| G2 — 数值基准 | TE 介质平板 n=1.5：R=0.147929 vs 理论值 0.1479（误差 0.03%）；损耗材料被动性验证通过 |
| G3 — 持久作业 | Worker 产出与 G2 完全一致；恢复跳过已完成行 |
| 单元测试 | 133 passed（导入安全、配置、schema、引擎、租约、扫描、作业、哈希、收敛、场导出、worker、server） |
| G0 — 引擎生命周期 | 启动 → 健康检查 → 停止，无 MATLAB 进程泄漏，无临时文件残留（MCP 验证 2026-07-13） |
| M3 — 高阶冒烟 | nn=21（32s）+ nn=31（261s），内存模式稳定，无 OOM |
| M4 — Scratch 模式 | 求解正确，结果与内存模式一致 |

## 已知限制

- **TM 正入射/偏入射：** `pol=-1` 对对称结构 R=T=0，由 RETICOLO V10 场分解退简并
  引起。可使用 `delta0≠0` 或研究 `ef.TMinc_top_*` 通道。
- **场导出（`retchamp`）：** RETICOLO V10 `retapod`/`retchamp` 在均匀结构上因
  `imag(apod)` 类型错误崩溃。此为 V10 上游 bug，在找到变通方案或 V10 修复前场导出
  未经验证。

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
│   ├── field_export.py  # retchamp 场数据导出
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
