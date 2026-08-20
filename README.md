# DSH LAN Deployment

使用一个 Docker 容器从官方 DeepSeek Harness 源码构建多用户 Web 服务。Python 网关提供注册和登录，并把每个已认证用户转发到独立的 DSH 进程。

## 架构

```text
浏览器 -> http://<局域网 IP>:3080 -> FastAPI :8080 -> 用户专属 DSH 127.0.0.1:31xx
```

DSH 保持官方默认回环监听。FastAPI 接收请求，aiohttp 负责 HTTP/WebSocket 转发，并把 Host 改写为对应的回环地址。不修改 Harness 源码，也不使用 `--host` 或 `--trusted-host`。

每个注册账号拥有：

- 独立 Linux 用户和 DSH 进程；
- 独立的 Harness home、模型设置、插件配置和 workspace；
- 由签名 HttpOnly Cookie 绑定的网关会话。

容器内的 OUTPUT 防火墙阻止租户访问其他租户的 DSH 端口或网关端口。代理继续对返回流量做两处局域网兼容处理：

- 为普通 HTTP 页面补充官方前端依赖的 `crypto.randomUUID()`；
- 将官方连接模块的浏览器回环判定改为真，使模型设置和插件配置使用宿主持久化设置。

## 获取源码

```sh
git clone --recurse-submodules <本仓库地址>
cd dsh-lan-deployment
```

已有 checkout 若缺少 submodule：

```sh
git submodule update --init --recursive
```

`deepseek-harness/` 固定到官方 `dsh-v0.1.0-rc.8` 对应提交。Docker 构建直接使用该目录中的源码，不安装 npm 发布版 Harness。

## 启动

构建并启动：

```sh
docker compose -f compose.yml up -d --build
docker compose -f compose.yml logs -f
```

查看服务器局域网地址：

```sh
hostname -I
```

在同一局域网设备的浏览器打开：

```text
http://<服务器局域网 IP>:3080/
```

没有有效登录信息时，主入口会自动跳转登录页；可以从登录页的“注册新账号”按钮进入注册页。注册或登录成功后会进入该用户的 DSH 主界面。模型提供商和 API Key 在 DSH 的“设置 → 模型”中由每个用户分别配置。

宿主机只挂载 `data/` 一个目录，内部布局如下：

```text
data/
├── gateway/              # 用户注册表与会话签名密钥
└── users/<username>/
    ├── home/             # 用户在界面中看到的 HOME
    │   └── workspace/    # 默认工作区
    └── state/            # DSH_HOME：设置、凭据、profiles、sessions
```

## 注册和登录 API

注册：

```sh
curl -i -c cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"change-me-123"}' \
  http://127.0.0.1:3080/api/gateway/v1/auth/register
```

登录、查询当前账号和退出：

```sh
curl -i -c cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"change-me-123"}' \
  http://127.0.0.1:3080/api/gateway/v1/auth/login

curl -b cookies.txt http://127.0.0.1:3080/api/gateway/v1/auth/me
curl -X POST -b cookies.txt http://127.0.0.1:3080/api/gateway/v1/auth/logout
```

用户名长度为 3–24，只能使用小写字母、数字、下划线和连字符，且必须以字母开头；密码至少 8 个字符。

## 上传文件

登录后的 DSH 主界面右下角提供“上传文件”按钮。目标目录使用相对于用户 `workspace` 的路径，例如 `.`、`documents` 或 `reports/2026`；不存在的普通目录会自动创建。支持一次选择多个文件，单文件最大 100 MB，同名文件会被原子替换。

上传目标固定在当前登录用户的 `home/workspace/` 内。接口拒绝绝对路径、`..`、隐藏目录、隐藏文件和软链接路径，不能写入用户的 `state/`、profiles、sessions 或其他用户目录。

## 停止

```sh
docker compose -f compose.yml down
```

## 安全限制

当前版本面向可信局域网：已有账号之间相互隔离，但注册接口默认开放，且普通 HTTP 没有 TLS。不要把端口 `3080` 直接暴露到公网；公网部署前还需要 HTTPS、注册策略、CSRF/限流强化和管理接口。
