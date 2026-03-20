# Agent 统一平台

## 项目简介

统一 Agent 管理平台，聚合多个独立 Agent 到一个 Web 入口。支持权限管理（guest/root）、Agent 沙箱隔离、开机自动恢复。

## 架构

```
浏览器 → Nginx → ┬─ /        React 前端（Vite + TailwindCSS v4）
                  ├─ /api/*   FastAPI 网关（:8000）
                  └─ /agent/* Agent 子进程（各自端口，iframe 嵌入）
```

- 网关启动时扫描 `agents/*/agent.json` 自动发现 Agent
- 每个 Agent 独立子进程 + 独立 venv + 独立端口
- 状态检查：子进程 PID + 端口探活兜底

## 技术栈

后端 FastAPI (Python 3.9+) | 前端 React + Vite + TailwindCSS v4 | 图标 lucide-react

## 当前 Agent

| Agent | 端口 | 权限 | 说明 |
|-------|------|------|------|
| feishu_notify | 8501 | public | 飞书群消息推送（纯文本+富文本） |
| daily_digest | 8502 | root_only | Libra 实验摘要（单实验+全量） |
| launch_report | 8503 | root_only | 实验报告生成（截图+爬取+飞书文档） |

## 交互注意

- 用户使用语音输入，英文/中文可能有误识别（如 "Render"→"染的"、"Claude"→"Cloud"），根据上下文理解即可

## 编码规范

### Python 3.9 兼容
- 不能用 `int | None`，必须用 `Optional[int]` 或 `from __future__ import annotations`

### 前端 TypeScript
- 类型导入必须用 `import type { X }` 而非 `import { X }`
- 用 `npm run build` 检查错误，`tsc --noEmit` 不够可靠

### Agent 沙箱自包含规范
每个 Agent 的代码和依赖必须在自己目录内闭环（`scripts/check_isolation.sh` 自动检查）：
1. 禁止 `sys.path.insert/append`
2. 禁止硬编码绝对路径
3. 所有 import 为标准库 / requirements.txt 声明的库 / 内部相对导入
4. 必须有 `requirements.txt`
5. 必须有 `test_e2e.py`

**添加新 Agent：** 代码放 `agents/<id>/`，复用模块复制进来而非外部 import，跑 `check_isolation.sh` 验证。

### 飞书 Webhook Bot
- post 富文本不支持 `style` 字段，发送时必须过滤掉
- 支持元素：`text`、`a`（链接）、`at`

### 新增 Agent 后本地看不到
- **根因**：网关只在启动时扫描一次 `agents/*/agent.json`
- **解决**：登录 root → 管理面板点"重新扫描"，或 `POST /api/agents/reload`，或重启网关
- Render 每次部署会自动重启，所以云端不受影响

### Agent 前端修改后不生效
- **现象**：修改了 `app.py` 中的 HTML/JS，浏览器强刷后仍是旧代码
- **根因**：uvicorn 没有用 `--reload` 启动，修改 Python 文件后进程仍跑旧代码
- **验证方法**：`curl -s http://localhost:<port>/ | grep <关键代码>` 确认服务端返回的是新代码
- **解决**：必须 `kill -9` 旧进程再重启，不能只靠浏览器刷新

### Libra confidence 字段含义
- `confidence=1` 表示"统计显著"，**不代表正向**
- `confidence=-1` 也表示"统计显著"，**不代表负向**
- 正负方向必须看 `rel_diff` 的符号
- 颜色规则：显著 + rel_diff≥0 → 绿色，显著 + rel_diff<0 → 红色，不显著 → 黑色

### 飞书文档表格/Grid 空行问题
- **根因**：飞书创建表格或 Grid 后，每个 cell/column 自带一个空文本段落 block
- **不能直接删再写**：cell 不能完全为空，飞书会自动补回空段落
- **正确方案**：先写入内容，再删除多余的默认段落
  - `table_pair`：`write_table_cell_image` 用 index=0 插图片，然后删 index=1 之后
  - `primary_grid` 容器：追加所有内容后删 index=0 到 _default_count
  - Grid 列：用 `write_table_cell_image`（已含清理逻辑）

### 日志
- 网关日志：`logs/gateway.log`
- Agent 日志：`agents/<id>/data/<id>.log`
- launchd 日志：`logs/launchd_stdout.log`、`logs/launchd_stderr.log`

## 自测

改完代码必须跑 `./scripts/check_all.sh`，全部通过才算完成：
1. 前端 Vite 构建
2. 后端 API 测试（23 用例）
3. Agent 配置校验
4. Agent 沙箱隔离检查
5. Agent 端到端测试（`--lite` 模式）

### test_e2e.py 规范
```
python3 test_e2e.py [--port PORT]          # 默认：接口格式校验（秒级）
python3 test_e2e.py [--port PORT] --lite   # + 标准样例（~10s，日常用）
python3 test_e2e.py [--port PORT] --live   # + 全量测试（分钟级，发布前）
```

### 标准测试样例
| Agent | 模式 | 输入 | 说明 |
|-------|------|------|------|
| daily_digest | lite | flight_id=71879109, start=2026-03-16, end=2026-03-19, region=ROW | 单实验数据拉取 |
| launch_report | lite | flight_id=71879109, 2 个指标组爬取 + 生成文档 | 快速端到端验证 |
| launch_report | live | flight_id=71879109, 全部指标组截图 + 生成文档 | 完整验证 |
| feishu_notify | live | `--send` 模式发送测试消息 | Webhook 连通性 |

## TODO

- [x] ~~Git 首次提交~~
- [ ] Render 部署配置
- [ ] 推特爬虫 + 工具 Agent
- [ ] 心跳守护 Agent（Watchdog）：横向管理所有 Agent，定时健康检查 + 通知 + 授权后自动修复
- [ ] 账号管理 + Agent 可见性控制（对外展示时实现）
- [ ] Nginx 配置模板
- [x] ~~Libra 实验报告 Agent~~：已接入，截图/爬取/飞书生成三步都可用
- [x] ~~launch_report 体验优化~~：按钮顺序、命名规范、缓存复用、截图进度展示、表格空行修复
- [ ] 更多 Agent 接入（小红书文案、Poker、股票告警等）

## 规划中的功能

### 心跳守护 Agent（Watchdog）
- 每小时检查所有 Agent 的 /health + test_e2e.py
- 异常 → 飞书通知 → 授权后 git stash → 修复 → 失败则回滚
- 默认跨沙箱只读，修复需 root 明确授权
- 开机自启（launchd/systemd）

### 多 Agent 并行对比（Arena）
- 同一 prompt 并行发给多个大模型 API，结果并排展示
- 两种模式：Chatbot 文字对比 + 图片生成风格对比
- 需要接入多个模型接口（Claude/GPT/Gemini/SD/MJ 等）
- 附带 Token 用量 + 费用统计面板：每次调用记录各模型的 token 消耗和价格，可查看累计花费

### 部署迁移注意事项
当前用 Render 免费层做 Demo，后续可能迁移到 VPS / Railway / Fly.io。以下部分与部署平台耦合，迁移时需要调整：

| 耦合点 | 当前方案 | 迁移时调整 |
|--------|---------|-----------|
| 进程启动方式 | Render 的 `render.yaml` + `start` 命令 | 改为 Docker Compose 或 systemd |
| 环境变量 | Render Dashboard 配置 | 改为 `.env` 文件或云平台的 secret manager |
| HTTPS / 域名 | Render 自动分配 `xxx.onrender.com` | 自行配置 Nginx + Let's Encrypt |
| 端口暴露 | Render 只暴露一个端口，需单进程模式 | VPS 可直接多端口，Nginx 反向代理 |
| 冷启动 | 免费层 15 分钟无请求后休眠 | VPS 常驻运行，无此问题 |
| 静态文件 | 前端 build 后由 FastAPI 托管 | 可分离到 Vercel/CDN |

**设计原则**：部署逻辑集中在 `render.yaml` + `scripts/` 中，业务代码不含平台特定逻辑，确保一键切换。

### 推特信息聚合 Agent
- 基础：链接搜索、拉取用户帖子
- 进阶：关注列表 → 自动抓取 → AI 筛选 → 推送
