# 内网 Python pip 镜像源配置功能

## 背景与目标

项目部署在内网环境中，DSH 在执行任务时可能需要安装额外的 Python 库，但内网无法访问外部 PyPI 源。需要提供可配置的 pip 镜像源功能，让用户可以配置内网 pip 镜像，以便 DSH 能够正常安装 Python 包。

### 目标

- 在 DSM 管理界面提供 pip 镜像源配置入口
- 将配置存储到系统设置数据库
- 将配置传递给 DSH 运行环境
- 确保 DSH 可以通过配置的镜像源安装 Python 包

## 涉及的文件与模块

| 文件 | 修改内容 | 状态 |
|------|----------|------|
| `common/storage/settings.py` | 添加 pip index 配置的 getter/setter | ✅ 已完成 |
| `common/contracts/control.py` | 添加控制请求类型 | ✅ 已完成 |
| `supervisor/application.py` | 添加配置方法 | ✅ 已完成 |
| `supervisor/control/server.py` | 处理控制请求 | ✅ 已完成 |
| `gateway/services/supervisor.py` | 添加调用函数 | ✅ 已完成 |
| `gateway/routers/system/settings.py` | 添加管理 API 端点 | ✅ 已完成 |
| `gateway/routers/system/router.py` | 注册子路由 | ✅ 已完成 |
| `gateway/web/templates/system-settings.html` | 创建前端配置界面 | ✅ 已完成 |
| `gateway/web/static/management/system-settings.js` | 添加前端 JavaScript 逻辑 | ✅ 已完成 |
| `gateway/web/pages.py` | 添加页面渲染函数 | ✅ 已完成 |
| `gateway/routers/management.py` | 添加系统设置页面路由 | ✅ 已完成 |
| `gateway/web/templates/management-header.html` | 添加系统设置导航链接 | ✅ 已完成 |
| `supervisor/tenants/manager.py` | 读取 pip index 配置并传递给 DSH | ✅ 已完成 |
| `supervisor/application.py` | 传递 database_path 给 TenantManager | ✅ 已完成 |
| `docker/Dockerfile` | 构建时支持 pip 镜像 | ✅ 已完成 |

## 技术方案

### 数据存储

使用现有的 `system_settings` 表，key 为 `python.pip_index`，value 为镜像源 URL。

### API 设计

```python
# 获取 pip index 配置
GET /api/v1/admin/settings/pip-index
Response: { "pip_index": "https://pypi.tuna.tsinghua.edu.cn/simple" | null }

# 设置 pip index 配置
POST /api/v1/admin/settings/pip-index
Body: { "pip_index": "https://pypi.tuna.tsinghua.edu.cn/simple" }
```

### 配置传递

DSH 运行时需要读取 pip index 配置，通过以下方式传递：

1. **环境变量**：在启动 DSH 租户时设置 `PIP_INDEX_URL` 环境变量
   - `supervisor/tenants/manager.py` 中的 `_run` 方法读取配置
   - 如果配置存在，添加 `PIP_INDEX_URL` 到 DSH 进程的环境变量
   - DSH 执行的 `pip install` 命令会自动使用这个环境变量

2. **pip.conf**：在用户目录中生成 pip.conf 文件（备选方案）

3. **命令行参数**：在执行 pip install 时添加 `--index-url` 参数（备选方案）

当前实现采用方案 1（环境变量），因为：
- 最简单直接
- 无需修改 DSH 的执行逻辑
- pip 原生支持 `PIP_INDEX_URL` 环境变量

### 前端界面

在管理界面的"系统设置"页面添加 pip 镜像源配置项，参考现有的 npm registry 配置。

## 实施步骤

- [x] 步骤 1：在 `settings.py` 中添加配置存储函数
- [x] 步骤 2：在 supervisor 中添加配置 API
- [x] 步骤 3：在 gateway 中添加管理接口
- [x] 步骤 4：在前端添加配置界面
- [x] 步骤 5：将配置传递给 DSH 运行环境
- [ ] 步骤 6：测试验证

## 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| DSH 沙箱无法访问配置的镜像源 | 🟢 低 | 已通过环境变量 `PIP_INDEX_URL` 传递配置 |
| 配置传递方式不兼容 | 🟢 低 | 使用 pip 原生支持的环境变量 |
| 用户配置错误的镜像源 | 🟢 低 | 前端验证 URL 格式 |

## 依赖

- 无外部依赖
- 复用现有的系统设置基础设施
