# 水准网成果平台

面向省级十万级往返高差测段的水准网平差平台。系统把**当天可发现的分区质检问题**与**最终整体最小二乘成果**分开：连通分量可以并行预检，但任何分量的分区结果都不会被简单拼接成最终高程；最终求解始终从同一不可变观测/基准/权重快照重建整体设计矩阵。

## 关键原则

- React + Cytoscape.js：测点拓扑、任务代次/阶段进度、残差与闭合环追踪。
- FastAPI + SciPy sparse：加权最小二乘，默认稀疏正规方程 + `splu`。
- Celery + Redis：连通分量、分区质检、全局求解、审计阶段任务，任务幂等且可从确认阶段恢复。
- PostgreSQL/PostGIS：项目、测点、原始观测、基准、权重规则、不可变快照、任务结果、发布成果。
- 乐观锁：观测、基准、权重规则都带 `lock_version`；项目草稿带 `draft_version`。
- 快照不可变：任务绑定 `input_sha256`、规范化输入、算法参数；旧任务可继续完成审计，但新草稿发布时不会被覆盖。
- 禁止随意正则化：病态/秩亏时切换到显式 QR 诊断或 SVD 诊断并拒绝伪造唯一高程；不添加 ridge、最小范数伪解或任意基准平移。
- 原始高差永不被覆盖：`raw_forward/raw_backward` 保留在观测表和快照，平差高差、改正数、残差只写结果表。

## 目录

```text
backend/
  app/
    core/          # 拓扑、权重、分区 QC、稀疏 WLS/QR 诊断
    services/      # 快照、导入、修订、任务状态机、发布审计
    workers/       # Celery app 与幂等任务
    api/           # FastAPI 路由
    models.py      # PostgreSQL/PostGIS SQLAlchemy 模型
  tests/
frontend/         # Vite React + Cytoscape.js
scripts/          # 10 万数据生成、压测、样例 CSV
docker-compose.yml
```

## 启动

```bash
docker compose up --build
```

- 前端：http://localhost:5173
- API 文档：http://localhost:8000/docs
- PostgreSQL：PostGIS 16
- Redis：Celery broker/backend
- `migrate` 服务执行 `python -m app.manage initdb`，创建表、PostGIS extension 和空间索引。

本地开发：

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export LEVELING_DATABASE_URL=postgresql+psycopg2://leveling:leveling@localhost:5432/leveling
export LEVELING_REDIS_URL=redis://localhost:6379/0
python -m app.manage initdb
uvicorn app.main:app --reload
celery -A app.workers.celery_app.celery worker -Q leveling --loglevel=info
celery -A app.workers.celery_app.celery_app beat --loglevel=info
```

生产环境建议另外安装 SuiteSparse 并安装 `backend/requirements-sparseqr.txt` 中的 `sparseqr`。缺少该包时，程序会对大尺寸 QR 诊断明确报错并拒绝产出高程，不会用伪解替代。

## CSV 格式

```csv
line_code,sequence,from_code,to_code,raw_forward,raw_backward,distance_km,sigma_add_mm,sigma_per_km_mm
L1,1,BM-A,BM-P1,1.2503,-1.2500,1.0,1,2
```

- 有向高差取 `0.5 * (forward - backward)`。
- 往返回合差取 `forward + backward`，作为质检证据。
- 权重：`sigma_m = sqrt((sigma_add_mm/1000)^2 + factor*(sigma_per_km_mm/1000)^2*distance_km)`，`w=1/sigma_m^2`。
- 原始 forward/backward 不做覆盖。

## 任务阶段机

1. `accepted`：校验项目草稿乐观锁并生成/复用不可变快照。
2. `components`：计算连通分量，写入分区任务。
3. `partition_qc`：Celery worker 并行执行无基准、多固定基准、往返闭合差、独立环闭合等预检。
4. `global_solve`：从快照重建全分量稀疏设计矩阵；分区结果只作为证据，不拼接成果。
5. `audit`：闭合环、基准约束、改正数/残差统计、输入 hash、算法参数、原始值完整性。
6. `ready`：当前草稿可发布；旧草稿完成时标记 `superseded`，仅保留审计。

确认阶段写入 `job_checkpoints`。worker 重启后定时恢复 tick 只处理未确认工作；重复提交同一 `input_sha256` 只返回同一个任务，不产生新的任务代次。

## 算法选择

默认 `auto`：

- 先构造整体稀疏正规方程 `N = AᵀWA`、`u = AᵀWb`，用 SciPy `splu` 求解。
- 结构性无基准/秩亏：立即失败并给出 nullspace 诊断。
- LU 指示病态或超过条件数阈值：切换显式 QR 诊断。
- QR 发现秩亏：返回秩、nullspace、问题点/分量，不发布唯一高程。
- 可通过提交参数 `algorithm="qr"` 强制 QR，`algorithm="normal"` 强制正常方程路径。

算法参数、阈值、权重模型、禁止正则化声明都写入快照和发布成果。

## 乐观锁与旧任务审计

- 修订观测：`PATCH /api/projects/{pid}/observations/{oid}`，必须带 `expected_version`。
- 修订基准：`PATCH /api/projects/{pid}/datums/{did}`，必须带 `expected_version`。
- 修订权重：`PUT /api/projects/{pid}/weights`，必须带 `expected_version`。
- 提交任务与发布：必须带当前 `expected_draft_version`。
- 修订会增加项目 `draft_version`。运行中的旧任务仍可完成并保留结果/审计；若快照不再是当前草稿，则状态为 `superseded`，不能发布或覆盖新草稿。

## 发布前审计

发布接口只接受当前草稿、状态 `ready` 且审计通过的任务，核对：

- 分区错误（如不连通无基准分量）；
- 独立环闭合差；
- 基准约束/多基准矛盾与基准残差；
- 改正数、残差、RMS、σ0、加权 RSS、自由度、条件数；
- 快照 `input_sha256`、观测/基准/权重分片 hash、算法参数；
- 原始 forward/backward 与结果表的调整值分离；
- `regularization == forbidden`。

## API 摘要

```text
POST   /api/projects
POST   /api/projects/{project_id}/import
GET    /api/projects/{project_id}/topology
POST   /api/projects/{project_id}/datums
PATCH  /api/projects/{project_id}/datums/{datum_id}
GET/PUT /api/projects/{project_id}/weights
PATCH  /api/projects/{project_id}/observations/{observation_id}
POST   /api/projects/{project_id}/jobs
GET    /api/projects/{project_id}/jobs
GET    /api/jobs/{job_id}
GET    /api/jobs/{job_id}/components
GET    /api/jobs/{job_id}/result
POST   /api/projects/{project_id}/publish
GET    /api/projects/{project_id}/publications
```

无 Celery 的测试/单机环境可调用：

```text
POST /api/jobs/{job_id}/advance
POST /api/jobs/{job_id}/run-component/{component_id}
POST /api/jobs/{job_id}/solve
```

## 验收场景

生成 10 万测段数据（含冗余闭合、无基准孤岛、多基准矛盾点）：

```bash
python scripts/generate_pressure_data.py scripts/pressure_100k.csv 100000
python scripts/pressure_solve.py scripts/pressure_100k.csv
```

参考机器上核心求解结果约 1 秒级：

```text
ok True method sparse_normal_equations points 99993 obs 100000 ...
```

测试覆盖：

```bash
cd backend
LEVELING_DATABASE_URL=sqlite:////tmp/leveling-test.db python -m pytest -q
```

场景包括：

1. 100,000 往返测段压力求解；
2. 不连通子网导致分量 QC 错误和整体秩亏拒绝；
3. 多固定/多基准矛盾显式告警与残差审计；
4. 求解途中修改权重，旧任务不覆盖新草稿，新快照产生新代次；
5. worker 在分区后、全局求解中或审计前重启，通过 checkpoint 恢复；
6. 重复提交同一输入只得到一个任务；
7. 发布结果复现到输入摘要、算法参数和输入 SHA-256；
8. 原始高差不被平差值覆盖。
