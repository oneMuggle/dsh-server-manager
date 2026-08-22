# DSH Server Manager（DSM）

DSH Server Manager（DSM）使用一个 Docker 容器从官方 DeepSeek Harness 源码构建多用户服务器管理平台。Python 网关提供注册、登录和集中管理，并把每个已认证用户转发到独立的 DSH 进程。

> [!WARNING]
> 本项目仍处于开发和验证阶段，只适合可信局域网内的学习、测试与二次开发，**不应该用于生产环境**。项目目前不提供生产级安全保证、稳定性承诺或兼容性承诺，请勿直接暴露到公网或用于承载重要数据。

## 架构

```text
浏览器 -> http://<局域网 IP>:3081 -> FastAPI :8080 -> 用户专属 DSH 127.0.0.1:31xx
```

DSH 保持官方默认回环监听。FastAPI 接收请求，aiohttp 负责 HTTP/WebSocket 转发，并把 Host 改写为对应的回环地址。不修改 Harness 源码，也不使用 `--host` 或 `--trusted-host`。

每个注册账号拥有：

- 独立 Linux 用户和 DSH 进程；
- 独立的 Harness home、模型设置、插件配置和 workspace；
- 可通过签名 HttpOnly Cookie 或 Bearer Token 使用的网关会话。

容器内的 OUTPUT 防火墙阻止租户访问其他租户的 DSH 端口或网关端口。代理继续对返回流量做两处局域网兼容处理：

- 为普通 HTTP 页面补充官方前端依赖的 `crypto.randomUUID()`；
- 将官方连接模块的浏览器回环判定改为真，使模型设置和插件配置使用宿主持久化设置。

## 获取源码

```sh
git clone --recurse-submodules <本仓库地址>
cd dsh-server-manager
```

已有 checkout 若缺少 submodule：

```sh
git submodule update --init --recursive
```

`deepseek-harness/` 固定到官方 `dsh-v0.1.0-rc.8` 对应提交。Docker 构建直接使用该目录中的源码，不安装 npm 发布版 Harness。

## 启动

构建并启动：

```sh
cp .env.example .env
# 编辑 .env，为 DSM_ADMIN_PASSWORD 设置至少 8 个字符的强密码
docker compose -f docker/compose.yml up -d --build
docker compose -f docker/compose.yml logs -f
```

容器启动时会检查固定管理员账号 `admin`。如果该账号不存在，容器使用 `DSM_ADMIN_PASSWORD` 自动创建；变量缺失或长度不在 8–256 个字符之间时，容器拒绝启动。账号创建后修改环境变量不会自动重置已有密码。`DSM_REGISTRATION_ENABLED` 默认为 `false`，设置为 `true` 才开放公开注册。

查看服务器局域网地址：

```sh
hostname -I
```

在同一局域网设备的浏览器打开：

```text
http://<服务器局域网 IP>:3081/
```

### GitHub 镜像

每次向 `main` 推送提交时，GitHub Actions 会递归检出固定版本的 Harness 子模块，构建 Docker 镜像并发布到 GitHub Container Registry：

```text
ghcr.io/mill413/dsh-server-manager:<提交哈希前7位>
```

例如提交 `abcdef123...` 对应镜像标签 `abcdef1`。项目不发布浮动的 `latest` 标签，部署时应显式选择一个提交标签，以便复现和回滚：

```sh
docker pull ghcr.io/mill413/dsh-server-manager:<7位提交哈希>
```

没有有效登录信息时，主入口会自动跳转登录页；可以从登录页的“注册新账号”按钮进入注册页。注册或登录成功后会进入该用户的 DSH 主界面。模型提供商和 API Key 在 DSH 的“设置 → 模型”中由每个用户分别配置。

`admin` 是唯一固定管理员，其他注册账号均为普通用户。管理员的 DSH 主界面会显示“管理后台”入口。

宿主机只挂载 `docker/data/` 一个目录，内部布局如下：

```text
docker/data/
├── gateway/
│   ├── users.db          # SQLite 用户数据库
│   └── session-secret    # Cookie/Bearer Token 签名密钥
├── plugins/
│   ├── artifacts/        # 去重后的离线原始包
│   ├── releases/         # 只读的共享插件版本
│   └── pnpm-store/       # 联网/离线安装共用的 pnpm store
└── users/<username>/
    ├── home/             # 用户在界面中看到的 HOME
    │   └── workspace/    # 默认工作区
    └── state/            # DSH_HOME：设置、凭据、profiles、sessions
```

## 环境变量

Compose 从仓库根目录的 `.env` 读取配置；可从 `.env.example` 复制。当前对外配置如下：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DSM_ADMIN_PASSWORD` | 无 | 首次启动时创建固定管理员 `admin`，必须为 8–256 个字符；管理员已存在时不会自动改密 |
| `DSM_REGISTRATION_ENABLED` | `false` | 是否开放注册页面和注册 API，只接受 `true` 或 `false` |
| `DSM_COOKIE_SECURE` | `false` | 使用 HTTPS 时设为 `true`，使会话 Cookie 仅通过安全连接发送 |
| `DSM_PLUGIN_NPM_REGISTRY` | pnpm 默认 registry | 共享插件联网导入所使用的 HTTP(S) registry |
| `DSM_PLUGIN_NPM_TOKEN` | 无 | 私有 registry 的只读 Token，只在插件安装临时目录中使用 |
| `DSM_LOG_LEVEL` | `INFO` | Supervisor 日志级别 |

旧服务兼容接口继续使用其原有变量名 `DSH_REGISTER_API_KEY`、`DSH_LOGIN_API_KEY` 和 `LOGIN_TICKET_TTL`，避免旧调用方修改部署配置；它们只控制下文的三个兼容接口。`DSM_STATE_ROOT` 和 `DSM_CONTROL_SOCKET` 是容器内部进程间配置，标准 Compose 部署无需设置。

## 注册和登录 API

面向 API 调用和二次开发时应使用 Bearer Token；Cookie 只用于浏览器管理界面和 DSH 页面会话。

开放注册后，新用户可以通过 JSON 接口注册。响应中的 `accessToken` 可以直接用于后续 API 请求：

```sh
curl -sS \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"change-me-123"}' \
  http://127.0.0.1:3081/api/v1/auth/registrations
```

已有用户推荐通过 OAuth2 Password 接口登录并获取 Bearer Token：

```sh
curl -sS -X POST \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=alice' \
  --data-urlencode 'password=change-me-123' \
  http://127.0.0.1:3081/api/v1/auth/token
```

响应格式：

```json
{
  "access_token": "<signed-token>",
  "token_type": "bearer",
  "expires_in": 604800
}
```

将返回的 `access_token` 放入 `Authorization` 请求头：

```sh
TOKEN='<signed-token>'
curl \
  -H "Authorization: Bearer ${TOKEN}" \
  http://127.0.0.1:3081/api/v1/auth/sessions/current
```

用户名长度为 3–24，只能使用小写字母、数字、下划线和连字符，且必须以字母开头；密码至少 8 个字符。

浏览器登录页使用 `POST /api/v1/auth/sessions`。该接口会设置 HttpOnly Cookie，同时在响应中返回同一份 Bearer Token：

```json
{
  "username": "alice",
  "role": "user",
  "accessToken": "<signed-token>",
  "tokenType": "Bearer",
  "expiresIn": 604800
}
```

Bearer Token 与浏览器 Cookie 使用同一套签名、七天有效期和失效检查。用户被停用或密码被重置后，两者都会失效。`DELETE /api/v1/auth/sessions/current` 只负责清除浏览器 Cookie；当前没有单独的 Bearer Token 撤销列表，API 客户端需要自行删除本地保存的 Token。

用户数据使用 SQLite 存储，用户名、UID 和租户端口均有唯一索引，数据库文件位于 `docker/data/gateway/users.db`。

### 旧服务兼容 API

DSM 保留了作者旧服务的三个机器对机器接口，旧调用方可以保持路径、请求体和响应处理不变：

- `POST /api/register`：添加用户；
- `POST /api/users/{username}/provider`：添加或替换同名自定义提供商，未传模型时请求 `<baseURL>/models` 自动发现；
- `POST /api/login-ticket`：为已有用户返回 10–300 秒内有效的一次性登录链接。

在 `.env` 配置下列变量后才会启用；留空时对应接口统一返回 401：

```dotenv
DSH_REGISTER_API_KEY=<注册与提供商接口密钥>
DSH_LOGIN_API_KEY=<独立的登录换票密钥>
LOGIN_TICKET_TTL=60
```

`DSH_LOGIN_API_KEY` 可以以任意已有账号登录，权限高于普通用户 Token，必须与 `DSH_REGISTER_API_KEY` 分开保管，且只允许可信后端使用。登录 ticket 只在网关内存中保留 SHA-256 摘要，成功或失败消费后都不能重放。

## 管理后台与 Swagger

固定账号 `admin` 访问 `/admin` 可以查看用户的用户名、角色、账号状态、租户端口、进程运行状态、常驻内存和注册时间，并可创建用户、重置密码以及停用或启用普通用户。用户列表接口为 `GET /api/v1/users`，未登录请求返回 401，普通用户请求返回 403；接口不会返回密码记录、Linux UID 或用户配置。`DELETE /api/v1/users/{username}` 目前仅保留定义并返回 501，尚未实现用户删除。

管理员还可以调用 `POST /api/v1/users/{username}/providers`，为指定用户添加一个自定义模型提供方。请求字段与 DSH 的“设置 → 模型 → 自定义提供方”一致：

```json
{
  "providerId": "my-gateway",
  "displayName": "My Gateway",
  "baseURL": "https://gateway.example/v1",
  "api": "openai-completions",
  "apiKey": "replace-with-the-user-key",
  "models": [
    {
      "id": "chat-model",
      "name": "Chat Model",
      "contextWindow": 128000,
      "maxTokens": 8192
    }
  ]
}
```

`displayName`、模型的 `name`、`contextWindow` 和 `maxTokens` 可省略。`models` 整个字段也可省略；此时必须传入 `apiKey`，网关会请求 `<baseURL>/models` 自动发现最多 100 个模型。显式传入 `models` 时 `apiKey` 仍可省略。`api` 仅接受当前 Harness 源码公开给自定义提供方的 `openai-completions`、`openai-responses` 和 `anthropic-messages`。接口只执行创建操作，提供方 ID 已存在时返回 409，不会覆盖用户现有配置。

配置通过目标用户 Harness 自身的设置 RPC 写入。`apiKey` 不会进入网关注册表、响应或设置文件，而是单独写入该用户的 Harness 凭据存储；不需要密钥的提供方可以省略此字段。若提供方配置已写入、但凭据存储随后失败，接口返回 502，并在错误详情中明确标记 `providerCreated: true`，避免调用方误以为整个操作都已回滚。

Swagger UI 位于 `/docs`，OpenAPI 文档位于 `/openapi.json`。Swagger UI 的 JavaScript、CSS 和图标均随网关镜像提供，不依赖公网 CDN，可在完全离线的局域网中使用。在同一浏览器登录后打开 Swagger，可以使用当前登录 Cookie；也可以在 `Authorize` 中通过账号和密码获取 Bearer Token 后调试受保护接口。管理后台中的 Swagger 链接会在新标签页打开。

## 插件管理

管理员访问 `/admin/plugins` 可以管理共享插件：

- 联网环境只需输入 npm 包名（例如 `@scope/plugin`），系统会从 npm Registry 自动发现全部版本并默认选中 `latest`，管理员确认具体版本后再导入；
- 离线环境上传 npm `.tgz` 包；离线包必须包含运行所需内容，且依赖已经内置或存在于离线包中；
- 点击安装、卸载、启用或停用后，在弹窗中搜索并选择一个或多个用户批量执行；
- 可把当前策略设为新用户默认策略，并在任务列表中查看每个操作的执行状态。

管理页也会自动列出当前 DSH 镜像提供的官方 Cordis 插件。官方插件直接使用镜像内文件，不进入共享 release 目录，也不能执行安装或卸载；管理员只能按用户或批量启用、停用，并可将状态应用到后续新用户。DSM 把这些差异写入每个用户独立的 `dsm-builtins.patch.yml` 覆盖层，不修改用户自己的 `cordis.patch.yml`。批量接口为 `POST /api/v1/plugins/builtins/jobs`，其 `action` 只接受 `enable` 或 `disable`。

插件的每个版本只在 `docker/data/plugins/releases/` 保存一份只读实体。用户目录中不复制插件文件，SQLite 只记录用户与共享版本的关联和启用状态；启用时由用户 profile 引用容器内的版本化解析别名，并在 `profiles/node_modules` 创建指向共享 release 的包名软链接，供插件 patch 解析运行时代码。因此同一版本不会随用户数量增加而重复占用磁盘，不同版本也可以同时服务不同用户。

版本发现接口为 `GET /api/v1/plugins/registry/versions?packageName=@scope/plugin`，返回全部可用版本及 `latest` 当前指向的精确版本。选择版本后调用 `POST /api/v1/plugins/imports/registry`：

```json
{
  "packageName": "@scope/plugin",
  "version": "1.2.3"
}
```

版本发现只读取 Registry 元数据，不下载或保存插件，也不会创建任务；导入接口收到选择的精确版本后才下载插件并创建异步任务。

离线导入接口为 `POST /api/v1/plugins/imports/archive`，请求体直接使用 `application/octet-stream` 传输 `.tgz`。批量策略接口为 `POST /api/v1/plugins/jobs`，导入和策略操作均异步返回任务 ID，可通过 `GET /api/v1/plugins/jobs/{job_id}` 查询结果。所有接口仅允许管理员 Cookie 或 Bearer Token 调用。

默认使用 pnpm/npm 的公共 registry。私有源可以在 `.env` 中设置 `DSM_PLUGIN_NPM_REGISTRY` 和只读的 `DSM_PLUGIN_NPM_TOKEN`。安装过程使用独立低权限账号，只允许 `node-pty` 执行原生构建脚本，其他依赖的 lifecycle scripts 不会执行；发布后保留必要的可执行位，并把共享版本改为 root 所有的只读目录。插件自身仍会进入被分配用户的 DSH 进程执行，所以只能导入可信插件。

## 上传文件

登录后的 DSH 主界面会在左侧栏“设置”按钮上方注入“上传文件”按钮；侧栏折叠时只显示与官方按钮一致的图标样式。弹窗提供工作区目录浏览：点击文件夹进入子目录，通过面包屑或“返回上一级”切换目录，然后将本机文件上传到当前目录。支持一次选择多个文件，单文件最大 100 MB，同名文件会被原子替换。

目录选择器和上传目标均固定在当前登录用户的 `home/workspace/` 内。接口不会列出隐藏目录或软链接目录，并拒绝绝对路径、`..`、隐藏目录、隐藏文件和软链接路径，不能访问或写入用户的 `state/`、profiles、sessions 或其他用户目录。

## 停止

```sh
docker compose -f docker/compose.yml down
```

## 安全限制

当前版本面向可信局域网：已有账号之间相互隔离，注册接口默认关闭，但普通 HTTP 没有 TLS。不要把端口 `3081` 直接暴露到公网；公网部署前还需要 HTTPS、注册策略、CSRF/限流强化和管理接口。

## 致谢

感谢以下开源项目及其贡献者：

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：本项目构建、运行和管理的核心 Agent Harness；
- [dsh-server-deployment](https://github.com/AnkoCD/dsh-server-deployment)：为多用户门户、基于 OS 账号的数据隔离、回环网关代理和文件访问边界提供了重要参考。

DSM 是独立的社区部署与管理项目，不代表上述项目的官方生产部署方案。

## 许可证

本项目基于 [MIT License](LICENSE) 发布。
