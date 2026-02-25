# PythonFileServer

一款轻量级、跨协议的通用文件服务器管理工具。它旨在为本地或私有网络提供安全、便捷的文件传输能力，通过统一的调度层支持多种标准传输协议。

## 功能特性

- **多协议中枢**：一键部署并管理 FTP、FTPS 和 SFTP 服务，各协议间无缝切换。
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

## 技术栈与依赖

本项目基于以下优秀的开源组件构建：

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** - 跨平台图形化界面驱动
- **[pyftpdlib](https://github.com/giampaolo/pyftpdlib)** - 高性能异步 FTP/FTPS 核心
- **[Paramiko](https://www.paramiko.org/)** - 工业级 SSH/SFTP 协议实现
- **[Cryptography](https://cryptography.io/)** - 底层安全加密支持

## 核心架构设计

FS 采用高度解耦的模块化设计：

- **调度层 (Controller)**：独立于具体协议的业务引擎。
- **接口层 (Backend)**：通过统一的后端抽象支持协议扩展。
- **虚拟层 (VFS)**：屏蔽物理路径差异，提供一致的文件访问接口。

## 许可证

[MIT License](LICENSE)
