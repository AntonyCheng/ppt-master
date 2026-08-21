# PPT 生成智能体 Docker 部署手册

本目录是 PPT 生成智能体的独立 Docker 部署入口。编排会启动：

| 服务 | 作用 | 对外端口 |
| --- | --- | --- |
| `pptmaster-caddy` | HTTPS/HTTP 反向代理和静态入口 | `8080`、`8443`（可配置） |
| `pptmaster-api` | Web API、账号、系统设置和前端静态文件 | 无 |
| `pptmaster-runner` | Celery 任务调度器；按任务创建 OpenCode Worker | 无 |
| `pptmaster-postgres` | 账户、项目、任务和加密的 Provider 配置 | 无 |
| `pptmaster-redis` | 任务队列和队列持久化 | 无 |
| `pptmaster-worker-image` | 仅构建，不常驻；每项任务临时启动的 OpenCode Worker 镜像 | 无 |

所有容器使用专属网络 `pptmaster-network`，容器名称均以 `pptmaster-` 开头。所有持久化数据均为宿主机 bind mount，位于 `docker/data/`；本项目不使用 Docker volume。因此，`docker compose down -v` 不会删除业务数据。

## 1. 部署前的结论

将整个项目复制到一台 Linux 服务器后可以启动，但不能跳过环境配置和 Worker 镜像构建。推荐流程是：

1. 安装 Docker Engine 与 Docker Compose Plugin。
2. 恢复项目目录，以及安全保存的 `docker/.env` 和 `docker/opencode/opencode.jsonc`。
3. 保留 `.env` 中的空路径配置；使用 `bash docker/bin/compose.sh` 时会自动按复制后的目录解析 Worker 项目和 OpenCode 配置路径。
4. 构建所有镜像，包含 Worker 构建 profile。
5. 启动编排，等待 API 健康检查通过。

已实现的工作目录权限策略会随镜像代码一同部署：Runner 创建新任务和复制续改版本后，都会把对应任务树交给配置的 Worker UID/GID。Worker 以非 root 用户运行；不需要、也不应将 OpenCode 整体提权为 root。

## 2. Linux 主机前置条件

以部署账号执行以下检查：

```bash
docker --version
docker compose version
docker info >/dev/null
```

部署账号必须有 Docker socket 权限。通常将该账号加入 `docker` 组后重新登录：

```bash
sudo usermod -aG docker "$USER"
```

`docker` 组等效于取得 Docker daemon 的高权限，只应授予受信任的部署管理员。不要将 PostgreSQL、Redis、API 或 Runner 的端口直接暴露到公网；Compose 只发布 Caddy 的 HTTP/HTTPS 端口。

首次构建需要访问以下来源：Docker 镜像仓库、配置的 Debian APT 镜像和 PyPI 镜像。默认使用阿里云 APT/PyPI 镜像，可在 `docker/.env` 修改。

## 3. 目录与文件

从项目根目录操作。核心目录如下：

```text
ppt-master/
├── docker/
│   ├── compose.yml
│   ├── .env                 # 本机私密配置，不应提交 Git
│   ├── .env.example         # 配置模板
│   ├── opencode/            # 本机 OpenCode Provider 配置（可能含 API Key）
│   │   └── opencode.jsonc
│   ├── caddy/Caddyfile
│   └── data/                # 所有持久化数据和 bind mount 根目录
│       ├── postgres/        # PostgreSQL 数据
│       ├── redis/           # Redis AOF 数据
│       ├── projects/        # 用户隔离的项目、SVG、PPTX 和任务记录
│       ├── caddy/           # HTTPS 证书等 Caddy 数据
│       └── caddy-config/    # Caddy 运行配置
└── webapp/                  # API、Runner、Worker 和前端源码
```

`docker/bin/compose.sh` 是 Linux 迁移后的推荐入口。它会根据脚本所在位置为 Runner 计算 Docker daemon 可见的绝对路径，因此不需要在 `.env` 中写死旧服务器或 Windows 路径。以下文档中的 `docker compose --env-file docker/.env -f docker/compose.yml` 都可替换为 `bash docker/bin/compose.sh`。

不要删除、移动或对整个 `docker/data/` 递归 `chown`。其中 PostgreSQL、Redis、Caddy 和项目工作区分别由不同容器用户管理。备份或迁移时保持目录结构和权限元数据。

### 3.1 必须保留的运行配置与清理边界

以下三类目录的作用不同，不能混为一谈：

| 路径 | 内容 | 清理规则 |
| --- | --- | --- |
| `docker/.env` | 数据库密码、管理员初始化信息、模型目录、Provider 环境变量与 `PPTMASTER_CONFIG_ENCRYPTION_KEY` | 不能删除；迁移时通过安全渠道复制。 |
| `docker/opencode/opencode.jsonc` | Provider 地址、API Key、默认模型与 OpenCode 工具权限 | 不能删除；迁移时通过安全渠道复制。该文件不会进入 Git 或 Docker 镜像。 |
| `docker/data/` | PostgreSQL、Redis、项目、导出文件及 Caddy 运行状态 | 仅在确认不保留账号、项目、任务和证书时才能清空。 |

若需要从无密钥模板重新创建 OpenCode 配置，复制 `docker/opencode/opencode.jsonc.example` 为 `docker/opencode/opencode.jsonc`，填入两个 Provider 的 API Key，再检查 `.env` 中的 `PPTMASTER_DEFAULT_MODEL`、`PPTMASTER_ALLOWED_MODELS` 与其一致。模板保留 AIHub 和 DeepSeek 的模型定义及全部权限规则，不包含任何密钥。

OpenCode Worker 以非 root 用户运行。为避免工作区被 OpenCode 误判为外部目录，`opencode.jsonc` 必须保留以下授权：

```json
"external_directory": "allow"
```

实际配置应写作 `"external_directory": "allow"`。该规则对整个 Worker 沙箱内的目录访问生效，Worker 仅能看到自己的 `/workspace/project` bind mount、只读的应用文件、临时 home 和 `/tmp`，不会获得宿主机目录访问权。若日志出现 `permission requested: external_directory (/workspace/project/...)`，说明该规则丢失或挂载的配置文件不是预期文件；恢复规则后重新提交该任务即可。

## 4. 新服务器首次部署

### 4.1 准备项目目录

以下以 `/srv/ppt-master` 为例：

```bash
sudo mkdir -p /srv/ppt-master
sudo chown "$USER":"$USER" /srv/ppt-master
cd /srv/ppt-master
# 将项目源码复制或克隆到此处
```

创建本机配置文件：

```bash
cp docker/.env.example docker/.env
chmod 600 docker/.env
```

### 4.2 配置 `docker/.env`

编辑 `docker/.env`。下面是 Linux 必须调整的字段示例：

```ini
POSTGRES_PASSWORD=<高强度随机密码>

# 仅首次初始化数据库时创建管理员；已存在的管理员不会因改动此处而重置密码。
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<管理员初始密码>

APP_ENV=production
PUBLIC_ORIGIN=https://ppt.example.com

# 使用 bash docker/bin/compose.sh 时留空，会自动使用当前 docker/ 目录下的
# data/projects 和 opencode。仅在把这两个目录放到项目外部时填写绝对 Linux 路径。
PPTMASTER_HOST_PROJECTS_ROOT=
PPTMASTER_HOST_OPENCODE_CONFIG_ROOT=
PPTMASTER_DOCKER_SOCKET=/var/run/docker.sock

# 使用部署账号的实际 Linux UID/GID，而非示例中的 Windows 值。
PPTMASTER_WORKER_UID=<执行 id -u 的输出>
PPTMASTER_WORKER_GID=<执行 id -g 的输出>

# 单次 OpenCode 调用连续无输出达到此秒数时，任务会失败而不是无限生成中。
PPTMASTER_OPENCODE_IDLE_TIMEOUT_SECONDS=600

# 模型名称必须与 OpenCode 配置或系统设置中的 Provider/模型一致。
PPTMASTER_DEFAULT_MODEL=<provider/model>
PPTMASTER_ALLOWED_MODELS=<provider/model>

# 网页系统设置新增 Provider 前必须设置，且必须长期保存不变。
PPTMASTER_CONFIG_ENCRYPTION_KEY=<Fernet 主密钥>

# 公网域名。DNS A/AAAA 记录需在启动 HTTPS 前指向本服务器。
PPTMASTER_SITE_ADDRESS=ppt.example.com
PPTMASTER_HTTP_PORT=80
PPTMASTER_HTTPS_PORT=443
```

获取当前部署账号的 UID/GID：

```bash
id -u
id -g
```

生成 `PPTMASTER_CONFIG_ENCRYPTION_KEY`（服务器安装了 OpenSSL 时无需额外安装 Python 包）：

```bash
openssl rand -base64 32 | tr '+/' '-_'
```

输出内容应保持完整（包括末尾的 `=`），复制到 `.env` 后长期保存。

该密钥用于加密数据库中经网页添加的 Provider API Key。迁移旧数据时必须使用旧服务器的原密钥；丢失或更换密钥会导致既有密文无法解密。

Provider 既可由管理员在网页“系统设置”中维护，也可通过 `docker/opencode/opencode.jsonc` 配置。该文件可能含 API Key，已被 Git 和 Docker 构建上下文排除；复制项目时需要通过安全渠道一并复制，不能放在 `docker/data/` 中。

### 4.3 构建镜像

`pptmaster-worker-image` 位于 `build` profile，普通 `up` 不会构建它。首次启动和源码升级后均需完整构建：

```bash
bash docker/bin/compose.sh --profile build build
```

此操作会构建 Web/API、Runner 和 Worker 镜像。Worker 基于固定 digest 的官方 OpenCode `1.18.18` 镜像，并安装 PPT 工作流所需的系统工具和字体。

### 4.4 启动并验证

```bash
bash docker/bin/compose.sh up -d
bash docker/bin/compose.sh ps
bash docker/bin/compose.sh logs -f pptmaster-api pptmaster-runner
```

等待 `pptmaster-api` 状态显示为 `healthy`。本机部署访问 `http://服务器地址:8080/`；公网部署在 DNS 和防火墙配置正确时访问 `https://ppt.example.com/`。

若使用域名，Caddy 会自动申请并续期 HTTPS 证书。启动前必须确保域名的 A/AAAA 记录已指向服务器，且防火墙/云安全组对公网开放 TCP `80` 和 `443`。

## 5. 从旧机器迁移

迁移应在旧服务停止后完成，以取得 PostgreSQL 和 Redis 的一致副本。

### 5.1 旧服务器

```bash
cd /srv/ppt-master
docker compose --env-file docker/.env -f docker/compose.yml down
```

通过安全渠道复制以下内容：

1. 完整项目源码。
2. 完整 `docker/data/` 目录。
3. 原 `docker/.env` 和 `docker/opencode/opencode.jsonc` 文件。

Linux 到 Linux 建议使用保留权限和数字 UID/GID 的复制方式：

```bash
rsync -aHAX --numeric-ids /srv/ppt-master/ <new-host>:/srv/ppt-master/
```

`.env` 含数据库密码、Provider 密钥和加密主密钥，不能提交 Git、贴入聊天记录或存入未加密的公共备份。

### 5.2 新服务器

1. 恢复文件到目标路径。
2. 保留旧 `.env` 中的数据库密码、Provider 密钥和 `PPTMASTER_CONFIG_ENCRYPTION_KEY`，以及旧 `opencode.jsonc`。
3. 保持 `PPTMASTER_HOST_PROJECTS_ROOT` 和 `PPTMASTER_HOST_OPENCODE_CONFIG_ROOT` 为空；仅按新服务器调整 Worker UID/GID、公网域名和端口。
4. 执行完整构建和启动：

```bash
cd /srv/ppt-master
bash docker/bin/compose.sh --profile build build
bash docker/bin/compose.sh up -d
```

所有账户、项目、历史任务、PPTX/SVG、网页维护的 Provider 配置和 Caddy 数据均会随 `docker/data/` 恢复。管理员环境变量仅在数据库不存在该管理员时生效，不会覆盖已迁移的账号密码。

## 6. 权限模型和常见权限问题

任务实际由临时的 OpenCode Worker 执行。Runner 通过 Docker socket 创建该 Worker，将当前任务的独立目录绑定到容器内 `/workspace/project`，并以 `PPTMASTER_WORKER_UID:PPTMASTER_WORKER_GID` 身份运行 Worker。

| 场景 | 处理方式 |
| --- | --- |
| 新建生成任务 | Runner 创建任务目录后，将其归属设置为 Worker UID/GID。 |
| 继续修改演示文稿 | Runner 复制上一版工作区后，递归将复制树归属设置为 Worker UID/GID。 |
| Linux bind mount | 使用 `chown` 精确修正任务树属主。 |
| Docker Desktop 不支持 `chown` | 仅对单个任务树使用可写权限回退，不会放宽整个项目目录。 |
| OpenCode 工具授权 | 控制模型能否调用工具，不能绕过 Linux 文件系统的属主和权限检查。 |

因此，`PermissionError` 不应通过“让 OpenCode 以 root 运行”处理。先确认 `.env` 的 `PPTMASTER_WORKER_UID`、`PPTMASTER_WORKER_GID` 是新服务器部署账号的 `id -u`、`id -g`，然后重新构建并重启 Runner：

```bash
docker compose --env-file docker/.env -f docker/compose.yml --profile build build pptmaster-runner pptmaster-worker-image
docker compose --env-file docker/.env -f docker/compose.yml up -d --no-deps --force-recreate pptmaster-runner
docker logs --tail 100 pptmaster-runner
```

若 Runner 无法创建 Worker，检查 Docker socket、网络和绝对路径：

```bash
bash docker/bin/compose.sh ps
docker logs --tail 150 pptmaster-runner
docker network inspect pptmaster-network
test -d "$(cd docker && pwd)/data/projects" && echo "projects path exists"
test -d "$(cd docker && pwd)/opencode" && echo "OpenCode config path exists"
```

`compose.sh` 会把这两个目录转换为 Docker daemon 可见的绝对路径。若绕过该脚本直接使用 `docker compose`，必须显式设置 `PPTMASTER_HOST_PROJECTS_ROOT` 和 `PPTMASTER_HOST_OPENCODE_CONFIG_ROOT`。

## 7. 日常运维

查看状态：

```bash
docker compose --env-file docker/.env -f docker/compose.yml ps
```

查看日志：

```bash
docker compose --env-file docker/.env -f docker/compose.yml logs -f
docker compose --env-file docker/.env -f docker/compose.yml logs -f pptmaster-api pptmaster-runner
```

停止服务但保留数据：

```bash
docker compose --env-file docker/.env -f docker/compose.yml down
```

升级项目代码或 `skills/ppt-master/` 后，必须重建 Worker 镜像，否则新任务仍会执行旧 Skill：

```bash
git pull
docker compose --env-file docker/.env -f docker/compose.yml --profile build build
docker compose --env-file docker/.env -f docker/compose.yml up -d --force-recreate
```

## 8. 备份与恢复

定期备份整个 `docker/data/`，并将 `docker/.env` 放入单独、加密且受访问控制的备份。

建议备份前先停止服务：

```bash
docker compose --env-file docker/.env -f docker/compose.yml down
tar -C docker -czf pptmaster-data-$(date +%F).tar.gz data
```

恢复时先停止目标服务器服务，再恢复 `docker/data/` 和与其匹配的 `.env`，然后按“从旧机器迁移”的构建与启动步骤执行。恢复后不要修改 `PPTMASTER_CONFIG_ENCRYPTION_KEY`。

## 9. 公网部署检查清单

- 使用域名和有效的 DNS A/AAAA 记录，不使用裸 IP 申请 HTTPS 证书。
- 对公网仅开放 TCP `80` 和 `443`；不要暴露数据库、Redis、API、Runner 和 Docker socket。
- 替换示例数据库密码和管理员初始密码。
- 保护 `docker/.env`，权限建议为 `600`。
- 定期备份 `docker/data/` 和 `.env`，并定期演练恢复。
- 限制服务器登录权限和 Docker socket 访问权限。
- 管理员账号使用强密码；系统设置页面仅对管理员开放。
- 在公网入口增加合适的 WAF、访问控制、监控与日志保留策略。

## 10. 快速故障定位

| 现象 | 优先检查 |
| --- | --- |
| `pptmaster-worker:local` 不存在 | 执行 `docker compose ... --profile build build pptmaster-worker-image`。 |
| Worker 无法启动 | `/var/run/docker.sock`、部署账号 Docker 权限，以及是否通过 `bash docker/bin/compose.sh` 启动；若直接运行 Docker Compose，必须显式设置两个 `PPTMASTER_HOST_*_ROOT` 绝对路径。 |
| Worker 写入 `PermissionError` | `.env` 中 Worker UID/GID 是否为 `id -u`/`id -g`，然后重建并重启 Runner。 |
| 日志出现 `permission requested: external_directory (/workspace/project/...)` | 检查 `docker/opencode/opencode.jsonc` 的 `permission.external_directory` 为 `"allow"`；修复后重新提交任务。 |
| 登录或页面不可访问 | `docker compose ps`、`docker logs pptmaster-api`、Caddy 日志和防火墙规则。 |
| 模型鉴权失败 | 系统设置中的 Provider/模型、环境变量 API Key、允许转发的变量和模型标识是否一致。 |
| 网页新增 Provider 失败 | 检查 `PPTMASTER_CONFIG_ENCRYPTION_KEY` 已设置且未更换。 |
| 迁移后无法读取既有 Provider | 确认迁移了旧 `.env` 中原来的 `PPTMASTER_CONFIG_ENCRYPTION_KEY`。 |
