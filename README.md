# DSH LAN Deployment

使用一个 Docker 容器从官方 DeepSeek Harness 源码构建 Web 服务，并通过同容器内的 Nginx 暴露局域网访问地址。

## 架构

```text
浏览器 -> http://<局域网 IP>:3080 -> Nginx :8080 -> DSH 127.0.0.1:3080
```

DSH 保持官方默认回环监听。Nginx 将请求的 Host 改写为回环地址，不修改 Harness 源码，也不使用 `--host` 或 `--trusted-host`。普通局域网 HTTP 页面缺少官方前端使用的 `crypto.randomUUID()`；代理仅在返回 HTML 时提前加载一个基于 `crypto.getRandomValues()` 的兼容实现。

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

如需调用 DeepSeek 模型，先在当前 shell 设置官方环境变量：

```sh
export DEEPSEEK_API_KEY='<your-key>'
```

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
http://<服务器局域网 IP>:3080
```

DSH 状态持久化在 `data/`，默认工作区是 `workspace/`。

## 停止

```sh
docker compose -f compose.yml down
```

## 安全限制

该版本没有认证和 TLS，并会把代理请求呈现为本机回环请求。只应在可信局域网使用，不要把端口 `3080` 直接暴露到公网。

浏览器仍知道自己通过局域网 IP 访问，因此官方前端中明确要求回环页面的部分设置、凭据和宿主操作可能不可用；本项目不改写官方前端来绕过这一限制。
