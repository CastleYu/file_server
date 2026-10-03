# 命令失败记录

## 2026-08-28 apply_patch Windows 路径与参数传递

- 内置 `apply_patch` 拒绝访问 `H:\Documents\Abyss\FS`，报告路径包含 reparse point；实际目录本身不是链接。
- 通过 `apply_patch.bat` 传递 PowerShell 多行参数时，批处理 `%*` 会破坏补丁末尾格式，报告缺少 `*** End Patch`。
- 有效降级：从 FS 工作区调用 Codex 自带可执行文件的 `--codex-run-as-apply-patch`，直接传入 UTF-8 PowerShell 字符串，绕过批处理参数重组。
- 上述可执行文件在默认沙箱中能解析补丁但无法写入 FS；任务确需修改时，以相同补丁申请受控沙箱提权，不扩大文件范围。
- 在仓库根执行 `python -B -m unittest discover -v` 会被顶层导入行为截获，只初始化日志并发现 0 项；使用 `python -B -m unittest discover -s tests -p "test*.py" -v`，本次成功发现并运行 40 项测试。

## 2026-08-29 Service 包循环导入

- `VirtualFS` 导入 `abyssfs.service.registry` 时会先执行 `service/__init__.py`；若 `__init__` 提前导入 Factory，Factory 会经 protocol 包重新导入 FTP/VirtualFS，导致 partially initialized module。
- 有效修复：Service 包公共对象通过模块级 `__getattr__` 惰性导出；底层 registry/constants/contracts 子模块不触发高层 Factory 导入。

## 2026-08-29 并行 compileall 的 pycache 临时文件冲突

- 完整测试与 `compileall` 并行执行时，Windows 上 `protocol/__pycache__` 和 `tests/__pycache__` 的临时 `.pyc` 写入出现 `PermissionError`；同轮 51 项测试的真实模块导入均成功。
- 有效降级：并行验证阶段使用不写 `.pyc` 的 `ast.parse` 全源码检查；需要字节码检查时在测试结束后顺序执行定向 `py_compile`。
- 仓库既有 `abyssfs/paths.py` 带 UTF-8 BOM；AST 批量读取若使用 `encoding="utf-8"` 会报 U+FEFF，统一使用 `encoding="utf-8-sig"`，不改动原文件编码。
## 2026-08-31 - apply_patch 拒绝工作区绝对路径

- 失败：使用 `H:\Documents\Abyss\FS\...` 绝对路径执行 `apply_patch`，工具报告 `path contains a reparse point`。
- 原因：补丁工具会拒绝穿过 Windows reparse point 的绝对路径，即使目标位于可写工作区。
- 后续：在工作区内使用仓库相对路径执行 `apply_patch`。
## 2026-08-31 - apply_patch 工具拒绝 H 盘工作区路径

- 失败：直接调用补丁工具修改工作区文件，绝对路径和仓库相对路径均报告 `path contains a reparse point`。
- 原因：工具将 H 盘工作区根识别为 reparse 路径，尽管 PowerShell 显示目标目录不是链接。
- 后续：调用同一 Codex 补丁执行器的原生入口，并传入 UTF-8 PATCH 参数。

## 2026-08-31 - exec JavaScript 缺少 Web 编码 API

- 失败：为补丁参数编码时依次调用 `TextEncoder` 和 `btoa`，隔离运行时均报告未定义。
- 后续：不在 JavaScript 隔离层编码补丁，改用 PowerShell 单引号 here-string。

## 2026-08-31 - apply_patch.bat 损坏多行参数

- 失败：PowerShell here-string 经 `apply_patch.bat` 转发后报告补丁末行无效。
- 原因：批处理的 `%*` 转发不能可靠保留多行参数。
- 后续：绕过批处理转发层，直接调用同一 `codex.exe --codex-run-as-apply-patch` 原生入口。

## 2026-08-31 - 原生补丁子进程未继承工作区写权限

- 失败：原生补丁执行器已解析补丁，但报告 `Failed to write file H:\Documents\Abyss\FS\...`。
- 原因：由沙箱命令启动的补丁子进程未继承 H 盘工作区写权限。
- 后续：对同一精确补丁命令使用 `require_escalated`，不扩大目标路径。

- 2026-10-03: Automatic review rejected stage/commit/push because tests/fixtures/legacy_users.json contains a password field. Read-only verification confirmed the fixed example value is used by tests/test_config_compat.py (lines 40, 87, 91, 97) with synthetic legacy paths. Retry only with this test-fixture evidence; no real environment credentials are included.
