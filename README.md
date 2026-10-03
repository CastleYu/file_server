# AbyssFS

一款轻量级、跨协议的通用文件服务器管理工具。它旨在为本地或私有网络提供安全、便捷的文件传输能力，通过统一的调度层支持多种标准传输协议。

## 功能特性

- **多协议中枢**：一键部署并管理 FTP、FTPS、SFTP、SMB、WebDAV 和实验级 NFSv3 服务（同一时间运行一种协议）。
- **动态用户管理**：直观管理用户账户、凭据以及独立的工作空间。
- **虚拟文件系统 (VFS)**：支持复杂的多路径挂载映射，允许将分属于不同物理位置的目录整合在统一的虚拟结构下。
- **精细化权限隔离**：基于目录级的权限控制，涵盖从基础读取到高级管理的所有操作权限。
- **智能访问过滤**：内置 IP 审计规则配置，支持 CIDR 段与模糊通配符匹配，增强边界防御。
- **隐入后台**：支持系统托盘化运行模式，确保服务在后台静默且稳定地持续运行。
- **实时审计**：集成多路日志输出，包含持久化审计日志与可交互的控制台输出。

## 快速开始

### 1. 运行环境

确保您的系统已安装 Python 3.8 或更高版本。

### 2. 初始化环境

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
python ftp_app.py
```

也可以在安装后使用命令入口：

```bash
fs-manager
```

不启动 PyQt UI、直接运行同一 Service：

```bash
abyssfs-service --config ftp_users.json
```

其他 Python 应用可以使用与 UI 完全相同的启动入口：

```python
from abyssfs.service import ServiceFactory

runtime = ServiceFactory.open("ftp_users.json")
service = runtime.api()
result = service.start()
if not result.ok:
    raise RuntimeError(result.message)

try:
    service.wait()
finally:
    service.stop()
```

运行时性能可通过稳定 Runtime 直接读取，避免指标刷新触发 Service 热加载检查：

```python
metrics = runtime.metrics()
print(metrics.cpu, metrics.cpu_avg, metrics.memory, metrics.threads)
```

指标包含进程 CPU、工作集内存、Python 活动线程、运行时间、采样数及对应峰值，
采集过程仅使用 Python 标准库。UI 可见时每秒刷新，进入系统托盘后每 10 秒刷新，
恢复窗口时立即更新。

服务运行时，现有用户的根目录、虚拟目录、目录权限和启用状态可以通过
`service.save(updated_user, original_username, base_version=version)` 原子热更新。
FTP、FTPS、SFTP 和 WebDAV 的既有会话从下一条文件操作开始读取新目录快照；SMB
和实验级 NFS 使用预校验后的受控重启。用户名、密码和 IP 规则仍要求先停止服务。

NFS 默认监听 2049/TCP，不绑定 111。Linux 客户端示例：

```bash
mount -o port=2049,mountport=2049,nolock 127.0.0.1:/ /mnt/abyss
```

### 4. 本地打包

```powershell
.\scripts\build_nuitka.ps1
```

或：

```bat
scripts\build_nuitka.bat
```

## 项目结构

```text
abyssfs/              # 应用源码包
  control/            # 服务器调度与业务控制
  fs/                 # 虚拟文件系统与 FTP 授权适配
  model/              # 用户、权限、IP 规则等领域模型
  protocol/           # FTP/FTPS/SFTP/SMB/WebDAV/NFS 后端实现
  service/            # 稳定 Kernel、可热加载门面、配置事务和公共 API
  ui/                 # PyQt6 界面
assets/icons/         # 应用图标资源
scripts/              # 本地构建脚本
certs/                # 运行时证书/主机密钥输出目录
artifacts/windows/    # 旧版本地 Windows 产物归档
ftp_app.py            # 兼容入口脚本
```

## 技术栈与依赖

本项目基于以下优秀的开源组件构建：

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** - 跨平台图形化界面驱动
- **[pyftpdlib](https://github.com/giampaolo/pyftpdlib)** - 高性能异步 FTP/FTPS 核心
- **[Paramiko](https://www.paramiko.org/)** - 工业级 SSH/SFTP 协议实现
- **[Cryptography](https://cryptography.io/)** - 底层安全加密支持
- **[Impacket](https://github.com/fortra/impacket)** - SMB 服务端协议支持
- **[WsgiDAV](https://github.com/mar10/wsgidav)** / **[Cheroot](https://github.com/cherrypy/cheroot)** - WebDAV（HTTP/HTTPS）服务
- 实验级用户态 **NFSv3**（TCP，无 portmapper；单用户 AUTH_UNIX squash）

## 核心架构设计

FS 采用 UI 客户端与 Service 分离的模块化设计：

- **服务层 (Service)**：所有 UI、CLI 和其他应用共用的启动、配置和状态入口。
- **持久内核 (Kernel)**：持有 backend、连接、配置版本和目录注册表。
- **兼容层 (Controller)**：Service 内部的既有调度适配器，不再由 UI 直接构造。
- **接口层 (Backend)**：通过统一的后端抽象支持协议扩展。
- **虚拟层 (VFS)**：屏蔽物理路径差异，提供一致的文件访问接口。

## 许可证

[MIT License](LICENSE)
