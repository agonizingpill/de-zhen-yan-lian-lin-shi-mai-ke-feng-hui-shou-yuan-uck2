# 安澜演练临时音控

给安澜社区演练的临时音控组记录各麦位和配线路由，比较同期声检摘要。音控复核员可保护多人同频线路，对独立异常麦位建隔离单并做备用路复测。

## 场景与人员

模拟客户是一个五人社区活动音控组。原来用配线纸、共享表格和短时监听记录。三处常见困扰是同频共用麦位被误判为单点故障、临时改线影响其他路由、测试片段没有跟随新版线路。输入为演练声检查收表与归一化统计；输出交给音控值守和演练记录员核对。

| 身份 | 账号 | 操作 |
|---|---|---|
| 作业员 | `operator@demo.test` | 录入摘要，提交 LOF 邻域诊断和备用路复测 |
| 复核员 | `reviewer@demo.test` | 作业员操作，签改单麦位、签复位 |
| 只读访客 | `viewer@demo.test` | 查看麦位、发现、隔离单和复线履历 |

密码统一为 `DemoC0de!2026`。

## 起栈

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

网页在 `http://localhost:18321`，API 文档在 `http://localhost:19321/docs`，MySQL 端口 17821。数据库和用户为 `desk`。结束使用 `docker compose down`。

| 服务 | 实现 |
|---|---|
| frontend | Nginx、HTML/CSS/ES modules |
| backend | Python 3.11、FastAPI、SQLAlchemy |
| worker | Redis 计算队列、scikit-learn RobustScaler 和 LOF |
| db | MySQL 8，配线、声检窗、诊断、隔离和复线证据 |
| redis | 消息和计算凭证 |

`.env.example` 定义容器名前缀、三个端口、`DB_PASSWORD`、`JWT_SECRET`、`SEED_DEMO`。本地调试在 `backend/` 安装依赖，设置 `DATABASE_URL`、`REDIS_URL` 和 `JWT_SECRET`，分别运行 `uvicorn core.app:app --port 8000` 和 `python -m core.worker`。前端通过 Nginx 的 `/api/` 同源路由调用后端。

## 音控班剧本

1. 09:00，核对 `BUS-A` 的三个协作麦位和 `BUS-B` 上的室内总音控。
2. 09:10，第 80 个声检刻度录到 1320、1324 和 1311 Hz 的齐发窄峰，签同频记录 `FD-COORD`。
3. 09:12，第 82 刻度室内总音控 `WIN-L-01` 出现 2210 Hz 回响，诊断为单点异常。
4. 09:14，复核员登记 `ISO-API`。忙线 BUS-A 不可选，独立空置的 BUS-C 可以分配给这一个麦位。
5. 09:18，BUS-C 在第 89 刻度仍有高自回响，复位拒绝。
6. 09:20，第 90 刻度记录新样 `WIN-GOOD-RELINE`，验证同麦位、同备用路、新版本。
7. 09:22，复核员签复位，现场总音控保持 BUS-C；BUS-A 的三个协作点留存原配置。

## 算法与源文件

`backend/domain/learning.py` 从固定种子 217 的 32 个正常基线窗拟合 RobustScaler，再用 8 邻居的 novelty LOF 评估四项特征。读数为峰值频率占比、持续回响指数、整体声级、自身回路关联，范围均为 0 到 1。LOF 分数小于 −1.55 时尝试寻找同总线同刻度的相邻频率峰。在主峰偏差 25 Hz 内、峰占比至少 0.62 且回响至少 0.52 的麦位达到三个，即标记协作频带并保留。复线签发另外要求回响指数不高于 0.36。模型与阈值用于模拟声检样例。

`soundboard.py` 与 `interventions.py` 定义领域表。`soundcheck.py` 记录声检和配线版本，`diagnose.py` 固定快照并检查迟到的同频证据，`channel.py` 保护共享路、隔离独立点并复位。前端位于 `frontend/src/pages/`。状态枚举在 `backend/domain/states.py` 与 `frontend/src/audio-state.js`，权限枚举在 `backend/core/roles.py`、`frontend/src/store.js`，使用于数据库约束、强制业务路径和显示。

## 短时静音试验

几支麦同时啸叫、同频峰分不出由谁带起时，诊断页（邻域诊断）可选择一支麦发起短时静音试验（`mute_trials` 表，逻辑在 `backend/domain/muting.py`，接口挂在 `/feedback/mute-trials`）。试验按 **开麦 → 静音 → 重新开麦** 顺序录入三段读数，每段都带同路对照麦声级；对照麦缺省自动取同总线另一支麦，也可显式指定。

- 同一路（`channel`）只允许一场进行中的试验：线路串行锁加 `active_slot` 唯一约束，并发开试只有一场成功，其余返回 409；终局或作废后释放该路。
- 开试时快照该路接线（各麦位 `code/revision/patch`）。判定前接线发生任何变更，访问或补录时试验自动置为 `void`（作废），不再判定。
- 三段齐了才判定：受试麦啸叫读数在静音段明显回落、重新开麦后回升至开麦段的一定比例，且对照麦三段声级极差平稳，才判 `source`（啸叫源头）；任一不满足记 `inconclusive`（证据不足）并给出未通过的检查项。读数按 0–1 归一化填写，百分数量级也可识别。
- 诊断页可展开每场试验查看三段读数、判定与逐项检查；作业员/复核员可操作，只读访客可查询。

源码以 MIT 许可提供，见 `LICENSE`。
