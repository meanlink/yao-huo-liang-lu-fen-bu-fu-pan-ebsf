# 窑火良率分布复盘

描述统计与稳健分布的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

### 釉料追溯与召回（trace 子系统）
解决“釉料混配后只留窑次良率、问题原料漏查下游”的问题，新增「釉料登记」与「召回追查」两个页面及 `/api/trace` 路由。

- **关系登记**：原料批号、多级混配（可多级拆合）、窑次领用（含缺来源待查）、复盘、简报统一落库；每次余量变动写 `StockLedger` 守恒流水。
- **守恒与拒绝规则**：混配要求 投入 = 产出 + 损耗；投料/领用走行锁校验余量，循环配料与并发超领在服务端直接拒绝，不产生负库存。
- **缺来源不豁免**：无来源批号的领用只登记物料名并标记 `pending`；召回按同名物料命中为待查候选，禁止直接排除，不能当作无影响。
- **按批号召回**：沿配料链 BFS 追查受影响窑次、复盘、简报与待查领用，候选保留完整链路径；分步追查先落断点再提交，进程中断后重试不重复封锁、不遗漏新增去向。
- **版本失效与重新核对**：任何关系写入都推进 `GraphState` 版本；追溯期间仍可登记，版本不一致时旧追溯置为 `rechecking`，重新核对保留人工结论并补全新增去向。
- **门禁与整单发布**：发布前命中结果一律暂停批准与下载（幂等封锁）；排除必须填写依据、待查项不可排除；全部核对后才能整单发布，发布单列明受影响项与排除依据，受影响项持续封锁、排除项解封。
- 后端纯服务端校验与计算，接口幂等可重试；演示数据见 `python -m scripts.seed_trace_demo`（随 `seed_demo` 自动幂等执行）。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18200；后端文档 http://localhost:19200/docs；数据库宿主端口 17700。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=kiln-yield；FRONTEND_PORT=18200；BACKEND_PORT=19200；DB_PORT=17700；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1001/，源码不含日志/截图/测试报告。
釉料追溯子系统的端到端验收（守恒、防循环、并发超领、待查领用、断点恢复、版本失效重核、封锁门禁、整单发布）：`cd backend && python -m tests.test_trace`（SQLite 内存文件库，无需 Postgres/Redis）。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
