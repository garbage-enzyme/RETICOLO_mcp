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
| `reticolo_capabilities` | 已验证（无需求解器） | 工具成熟度、schema 与构建身份 |
| `solver_status` | 已验证（只读） | 租约状态 + COMSOL 碰撞检测 |
| `reticolo_status` | 已验证（只读） | 引擎句柄与租约状态 |
| `reticolo_start` / `reticolo_stop` | 需要重新实测 | v0.2 开发中生命周期已修改 |
| `reticolo_solve_point` | 仅 TE 基准 | 单波长原始 R/T 与派生 A_balance |
| `reticolo_sweep` | 实验性 | 带完整文件身份校验的可恢复扫描 |
| `job_submit/status/tail/cancel/resume` | 实验性 | 真实重启验收尚未执行 |
| `reticolo_convergence` | 实验性 | 尚不能作为支路收敛证据 |
| `reticolo_field_export` | 当前 V10 路径不可用 | 当前 retchamp 基准会失败 |

实时成熟度和部署身份以 `reticolo_capabilities` 返回值为准。以下真机结果是
历史基准证据，不会自动把当前所有工具版本提升为“已验证”。

## 验证

| 阶段 | 证据 |
|---|---|
| G0 — 引擎生命周期 | 启动 → 健康检查 → 停止，无 MATLAB 进程泄漏，无临时文件残留 |
| G1 — M0 资源控制 | nn=9×2 + nn=15×1 点，C 盘 Δ=0 GB，无 `retXXXX` 残留 |
| G2 — 数值基准 | TE 介质平板 n=1.5：R=0.147929 vs 理论值 0.1479（误差 0.03%）；损耗材料被动性验证通过 |
| G3 — 持久作业 | Worker 产出与 G2 完全一致；恢复跳过已完成行 |
| 历史单元测试基准 | v0.2 开发修改前 133 passed |
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
