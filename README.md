# RETICOLO MCP · RETICOLO V10 RCWA 求解器 MCP 接口

> **Verified against real RETICOLO V10 + MATLAB R2025b. TE dielectric slab R=0.147929 vs analytical 0.1479 (0.03% error).**
> **已通过 RETICOLO V10 + MATLAB R2025b 真机验证。TE 介质平板 R=0.147929 vs 理论值 0.1479（误差 0.03%）。**

MCP server for the [RETICOLO V10](https://zenodo.org/records/14631951) rigorous
coupled-wave analysis (RCWA) solver. Wraps MATLAB R2025b via the Engine API.

基于 MATLAB Engine API 封装 [RETICOLO V10](https://zenodo.org/records/14631951) 严格耦合波分析（RCWA）求解器，提供 MCP 协议接口。

## Quick start · 快速开始

```powershell
# 1. Create conda env · 创建 conda 环境
conda create --name reticolo-mcp python=3.11 -y
conda activate reticolo-mcp

# 2. Install dependencies · 安装依赖
pip install mcp numpy pydantic

# 3. Install MATLAB Engine API (one-time · 一次性)
cd "D:\Program Files\MATLAB\R2025b\extern\engines\python"
pip install .

# 4. Install this package · 安装本包
pip install .

# 5. Set RETICOLO path and run · 设置路径后运行
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server
```

### opencode / Codex MCP config · MCP 配置

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

## Configuration · 配置

The server requires an external RETICOLO V10 installation. The bundled
`reticolo_v10/` directory in the repository is for development only and
is **not included in the PyPI wheel** (CC-BY 4.0 — separate from the MIT wrapper).

服务器需要外部 RETICOLO V10 安装。仓库中的 `reticolo_v10/` 仅供开发使用，**不包含在 PyPI wheel 中**（CC-BY 4.0 协议与 MIT wrapper 分离）。

Set path via env var or CLI · 通过环境变量或命令行设置路径：
```powershell
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server --reticolo-dir "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
```

## Tools · 工具列表

| Tool | Status | Description · 说明 |
|---|---|---|
| `solver_status` | ✓ | Lease state + COMSOL collision check (read-only, no MATLAB) |
| `reticolo_start` | ✓ | Start MATLAB engine, acquire lease, M0 disk-safety · 启动引擎 |
| `reticolo_stop` | ✓ | Stop engine, release lease, clean scratch · 停止引擎 |
| `reticolo_status` | ✓ | Engine state + lease status · 引擎状态 |
| `reticolo_solve_point` | ✓ | One wavelength → R, T, A_balance, passive · 单波长求解 |
| `reticolo_sweep` | ✓ | Resumable sweep, flush+fsync CSV, config_hash resume · 可恢复扫描 |
| `reticolo_convergence` | ✓ | Progressive nn scan, peak tracking, FWHM, Q · 阶数收敛 |
| `reticolo_field_export` | ✓ | Field export via retchamp, slice-plane, NPZ output · 场数据导出 |
| `job_submit` | ✓ | Submit durable staged-sweep job, returns job_id · 提交持久作业 |
| `job_status` | ✓ | Read job state + progress · 作业状态 |
| `job_tail` | ✓ | Last N events from job journal · 作业事件 |
| `job_cancel` | ✓ | Cooperative cancel request (between solve points) · 取消作业 |
| `job_resume` | ✓ | Resume failed/interrupted job · 恢复作业 |

✓ = verified against real RETICOLO V10 + MATLAB R2025b (2026-07-13).
✓ = 已通过 RETICOLO V10 + MATLAB R2025b 真机验证（2026-07-13）。

## Verification · 验证证据

| Gate | Evidence |
|---|---|
| G0 — Engine lifecycle | Start → health → stop, no MATLAB leak, no orphans |
| G1 — M0 resource | nn=9×2 + nn=15×1, C: Δ=0 GB, no `retXXXX` orphans, memory mode |
| G2 — Numerical baseline | TE slab n=1.5: R=0.147929 vs analytical 0.1479 (0.03% err); lossy slab passive ✓ |
| G3 — Durable jobs | Worker → results match G2; resume skips completed rows |
| Unit tests | 61 passed (import safety, config, schema, engine, lease, sweep, jobs, hash) |

## ⚠ Disk safety · 磁盘安全

RETICOLO's internal `retio` system spills large matrices to `retXXXX/` scratch
directories during high-order scans. This server defaults to **memory mode**
(`vmax=inf`, no disk spill). MATLAB temp files are redirected.

RETICOLO 内置的 `retio` 系统在高阶扫描时会将大矩阵写出到 `retXXXX/` 临时目录。
本服务器默认使用**内存模式**（`vmax=inf`，不写磁盘），MATLAB 临时文件重定向到 D 盘。

Startup applies · 启动时自动执行：
- `retio([], inf*1i)` — disable scratch writes · 禁用磁盘写入
- `TMP/TEMP/TMPDIR` → `D:\matlab_temp`
- Working directory → `D:\reticolo_scratch`
- Lease with COMSOL MCP collision detection · 与 COMSOL MCP 的碰撞检测

## Architecture · 架构

```
reticolo-mcp/
├── src/reticolo_mcp/
│   ├── server.py        # FastMCP server, all tools · MCP 服务端
│   ├── engine.py        # MATLAB Engine lifecycle + solve · MATLAB 引擎
│   ├── lease.py         # Atomic solver lease (named mutex) · 原子租约
│   ├── sweep.py         # Resumable sweep + peak analysis · 可恢复扫描
│   ├── jobs.py          # Durable job store (spec/state/events) · 持久作业
│   ├── worker.py        # Detached worker process · 独立工作进程
│   ├── convergence.py   # Progressive harmonic convergence · 阶数收敛
│   ├── field_export.py  # retchamp field export · 场数据导出
│   ├── schema.py        # Pydantic models for materials/geometry · 数据模型
│   ├── config_hash.py   # Canonical SHA-256 config identity · 规范哈希
│   └── config.py        # Paths, limits, env vars · 配置
├── reticolo_v10/        # Bundled RETICOLO V10 (CC-BY 4.0, dev only)
├── tests/               # Unit + integration tests · 测试
├── pyproject.toml
├── LICENSE              # MIT (wrapper code)
├── NOTICE               # CC-BY 4.0 attribution (bundled RETICOLO)
└── README.md
```

## License · 许可证

- **MCP wrapper code** (all Python files · 所有 Python 文件): MIT License.
- **Bundled RETICOLO V10** (`reticolo_v10/` · 内嵌 RETICOLO): CC-BY 4.0,
  © Jean Paul Hugonin & Philippe Lalanne.
  DOI: [10.5281/zenodo.14631951](https://doi.org/10.5281/zenodo.14631951).

See LICENSE and NOTICE for details.
