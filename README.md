# DSH LAN Deployment

使用一个 Docker 容器从官方 DeepSeek Harness 源码构建 Web 服务，并通过同容器内的 Nginx 暴露局域网访问地址。

## 架构

```text
浏览器 -> http://<局域网 IP>:3080 -> Nginx :8080 -> DSH 127.0.0.1:3080
```

DSH 保持官方默认回环监听。Nginx 将请求的 Host 改写为回环地址，不修改 Harness 源码，也不使用 `--host` 或 `--trusted-host`。

代理会对返回流量做两处局域网兼容处理：

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

该版本没有认证和 TLS，会把代理请求和浏览器能力都呈现为本机回环访问。局域网客户端能够读取或修改 Harness 设置、凭据和插件配置，并能使用宿主能力。只应在可信局域网中供单个用户使用，不要把端口 `3080` 直接暴露到公网。
