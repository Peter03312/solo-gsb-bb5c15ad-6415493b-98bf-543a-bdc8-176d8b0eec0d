# 剧场无线话筒频点排演系统

在同一剧场内，多个房间可能存在无线信号互扰。本系统根据用户录入的
**房间互扰关系**、每台话筒的**可用频点列表**、**保护间隔**与**启用时段**，
自动为话筒分配频点，避免互扰设备在时段重叠时频点过近。

- 后端：Python 3.11+ / Flask / OR-Tools CP-SAT 约束求解器
- 存储：SQLite（单文件，挂载于 `./data/theater.db`）
- 前端：原生 HTML / CSS / JavaScript，无外部 CDN 依赖
- 部署：Dockerfile + Docker Compose 一键启动并自动初始化

## 编排规则

1. 两台话筒位于**同一房间或互扰房间**，且启用时段在 `[开始, 结束)` 上重叠时，
   所配频点之差不得小于 **双方保护间隔的较大值**（整数 kHz）。
2. **锁定**话筒必须使用其锁定频点，编排时无条件保留。
3. 可用频点不足时不会报错，而是给出**最大可行方案**，并在页面列出未获配设备及原因。
4. 优化目标按字典序：
   1. 先最大化**获配的高优先级话筒数**；
   2. 再最大化**获配话筒总数**。
5. 以下情况**拒绝编排并指出问题设备**：
   - 启用时段非法（开始 ≥ 结束、超出 0:00–24:00，不支持跨日时段）；
   - 保护间隔为负、可用频点为空或非整数；
   - 锁定频点不在该设备的可用频点列表中；
   - 两台**锁定话筒**彼此互扰、时段重叠且频点差小于保护间隔。
6. 任意输入（房间、互扰关系、话筒）被修改后，旧方案自动标记为
   **「已过期」**，页面给出醒目提示，需要重新编排。
7. **现场执行核验**以某次最新且未过期的方案为冻结基线；上报、修订号与
   报告规则见文末「现场核验接口调用示例」一节。

## 一键启动（推荐）

```bash
docker compose up --build -d
```

首次启动会自动：

1. 构建镜像并安装依赖；
2. 在 `./data/theater.db` 初始化 SQLite 表结构；
3. 写入一套演示数据（3 个房间、2 组互扰关系、7 台话筒，含 1 台锁定话筒）。

启动后浏览器访问：

> **http://localhost:8000**

停止服务：

```bash
docker compose down
```

数据保存在宿主机 `./data` 目录，删除容器不丢数据。

## 不使用容器（本地运行）

```bash
pip install -r requirements.txt
python -m app
```

## 配置（docker-compose.yml / 环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 宿主机映射端口（仅 Compose 中生效，写在 `ports` 左侧，如 `PORT=9000 docker compose up`） |
| `SEED_DEMO` | `1` | 数据库为空时是否自动写入演示数据，`1` 写入，`0` 空白启动 |
| `FLASK_DEBUG` | 未设置 | 设为 `1` 时使用 Flask 开发服务器与热重载（默认用 waitress 生产服务器） |

页面上也提供「载入演示数据」「清空全部」按钮；在已有数据时载入演示会被拒绝，
以防误覆盖。

## 使用说明

1. **房间与互扰**：添加房间（主舞台、侧舞台、休息室……），再录入两两互扰房间对；
   同一房间内的话筒天然互扰，无需录入。
2. **话筒设备**：填写名称、所在房间、保护间隔(kHz)、启用时段、高/普通优先级、
   可用频点（逗号分隔的整数 kHz）。勾选「锁定频点」后从可用频点中选择锁定值。
3. 点击右上角 **▶ 开始编排**：
   - 成功：分两栏显示**获配设备**（频点、优先级、是否锁定保留）与
     **未获配设备**（每个可用频点与哪些已分配设备冲突）；
   - 失败：红色提示条给出非法设备名称与原因（锁定互冲 / 时段非法等），不产生方案。
4. 修改任意录入后，结果区出现「已过期」黄条；再次点击「开始编排」得到新方案。
5. **现场执行核验**：得到最新且未过期的方案后，在页面底部「现场执行核验」
   面板点击「从最新方案开启核验」（或调用 `POST /api/verifications`），系统
   会冻结该方案的**分配频点、话筒属性、房间互扰关系与可用频点列表**；此后再
   修改录入、删除或改名话筒，均不影响这次核验。
   - 按话筒 **ID** 逐台录入实际开机区间 `[开始,结束)` 与实际频点（分钟为
     0–1440 的整数；区间可首尾相接，同一话筒不得重叠），提交**完整替代**
     区间集——本次未出现的话筒视为全程未开机；
   - 每次提交须携带当前**核验修订号**，旧号一律拒绝（409）且不做任何部分
     写入；成功后修订号 +1；
   - 报告按时间列出：实际开机话筒之间**同房间/互扰房间且频差小于双方较大
     保护间隔**的冲突区间；偏离计划频点/锁定频点/可用列表，以及计划未获配
     却开机的设备区间；计划启用时段中没有上报覆盖的部分标记为**未知**
     （不推定正常或冲突）；
   - 已删除或改名的话筒仍按冻结时的身份（原 ID、原名称）参与核验。

## 现场核验接口调用示例

以下示例基于演示数据（`SEED_DEMO=1`）。时间一律为 **0–1440 的整数分钟**
（如 1260 = 21:00）。

```bash
# 1) 先编排，得到一份未过期方案
curl -s -X POST http://localhost:8000/api/solve

# 2) 从最新且未过期的方案开启核验（冻结方案、话筒属性、互扰、可用频点）
curl -s -X POST http://localhost:8000/api/verifications \
  -H 'Content-Type: application/json' \
  -d '{"note":"9月30日首演场"}'
# -> {"id":1,"solution_id":1,"revision":1,"ok":true}

# 3) 携修订号提交完整替代区间集（每台话筒一行，区间 [开始,结束,频点]）
curl -s -X PUT http://localhost:8000/api/verifications/1 \
  -H 'Content-Type: application/json' \
  -d '{
    "revision": 1,
    "report": [
      {"mic_id": 1, "intervals": [[1140,1260,600]]},
      {"mic_id": 2, "intervals": [[1140,1200,625],[1200,1290,610]]},
      {"mic_id": 6, "intervals": [[1155,1305,570]]}
    ]
  }'
# -> {"ok":true,"id":1,"revision":2,"counts":{"conflict_segments":1,...}}
# 区间也支持对象写法：{"start_min":1140,"end_min":1200,"freq":625}

# 4) 查看核验与报告（也可在页面底部直接查看）
curl -s http://localhost:8000/api/verifications/1

# 5) 下一次上报仍为“完整替代”，且必须用返回的最新修订号（2）
curl -s -X PUT http://localhost:8000/api/verifications/1 \
  -H 'Content-Type: application/json' \
  -d '{"revision":2,"report":[{"mic_id":1,"intervals":[[1140,1290,600]]}]}'
```

拒绝场景（整份提交不生效，HTTP 400/409，响应体 `error` 说明原因）：

- `409` 最新方案不存在或已过期时开启核验；提交携带的修订号不是当前号；
- `400` 上报了冻结方案之外的未知话筒 ID；区间端点不在 0–1440 或
  开始 ≥ 结束；频点不是不小于 0 的整数；同一话筒区间互相重叠；
  JSON 结构不符合约定。

报告响应结构（`GET /api/verifications/{id}` 中的 `report` 字段；首次成功
提交前为 `null`）：

```json
{
  "conflicts": [
    {"start_min":1200,"end_min":1260,
     "mic_a_id":1,"mic_a":"主持1号","freq_a":600,
     "mic_b_id":2,"mic_b":"主持2号","freq_b":610,
     "guard":25,"freq_diff":10,"room_a":"主舞台","room_b":"主舞台"}
  ],
  "deviations": [
    {"start_min":1200,"end_min":1290,"mic_id":2,"mic":"主持2号",
     "freq":610,"planned_freq":625,
     "type":"freq_not_planned","reason":"实际频点偏离计划分配频点"}
  ],
  "unknown": [
    {"start_min":1170,"end_min":1200,"mic_id":5,"mic":"休息5号",
     "planned_freq":600}
  ],
  "counts": {"reported_mics":3,"conflict_segments":1,
             "deviation_segments":1,"unknown_segments":1}
}
```

`deviations[].type` 取值：`freq_not_planned`（偏离计划频点）、
`freq_not_locked`（锁定话筒偏离锁定频点）、`freq_not_allowed`（不在可用
列表内）、`unplanned_power`（计划未获配却开机）。同一区间可能同时命中
多条（如既偏离计划又不在可用列表）。

## 目录结构

```
.
├── app/
│   ├── __init__.py      # Flask 路由：录入、校验、编排、方案与核验持久化
│   ├── __main__.py      # python -m app 入口
│   ├── db.py            # SQLite 连接、建表、演示数据
│   ├── scheduler.py     # CP-SAT 模型：约束、字典序目标、未获配原因分析
│   └── verification.py  # 核验：快照冻结、修订号校验、冲突/偏离/未知报告
├── templates/index.html # 单页界面
├── static/              # style.css / app.js（原生前端）
├── data/                # SQLite 数据卷（theater.db）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## HTTP 接口（供脚本调用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 全部房间、互扰、话筒、最新方案与核验列表（核验含快照与报告） |
| POST | `/api/rooms` | 新增房间 `{name}` |
| DELETE | `/api/rooms/{id}` | 删除空房间（下有话筒时拒绝） |
| POST | `/api/interference` | 新增互扰对 `{room_a_id, room_b_id}` |
| DELETE | `/api/interference/{id}` | 删除互扰对 |
| POST | `/api/mics` | 新增/编辑话筒（带 `id` 为编辑） |
| DELETE | `/api/mics/{id}` | 删除话筒 |
| POST | `/api/solve` | 执行编排（非法/锁定互冲返回 409 与设备明细） |
| POST | `/api/verifications` | 从最新且未过期的方案开启一次核验，冻结方案与输入 |
| GET | `/api/verifications` | 全部核验记录（含冻结快照、已上报区间与报告） |
| GET | `/api/verifications/{id}` | 单次核验详情与报告（不存在返回 404） |
| PUT | `/api/verifications/{id}` | 携 `revision` 提交完整替代区间集（旧号 409，非法内容 400） |
| POST | `/api/demo` | 空库时写入演示数据 |
| POST | `/api/reset` | 清空全部数据（含核验记录） |

时段以**分钟**（0–1440）提交，频点与保护间隔均为整数 kHz。
既有录入、编排与方案查询接口的请求/响应格式保持不变。
