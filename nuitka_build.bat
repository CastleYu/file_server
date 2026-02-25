@echo off
set PROJECT_NAME=AbyssFS
set ENTRY_POINT=ftp_app.py
set ICON_PATH=ftp.ico

echo [Step 1] Cleaning old builds...
if exist "build" rd /s /q "build"
if exist "%PROJECT_NAME%.dist" rd /s /q "%PROJECT_NAME%.dist"
if exist "%PROJECT_NAME%.exe" del /f /q "%PROJECT_NAME%.exe"

echo [Step 2] Starting Nuitka Build...
:: 修正模块名为 asyncore/asynchat (pyasyncore 只是 pip 安装包名)
:: 将 --windows-disable-console 替换为 Nuitka 推荐的 --windows-console-mode=disable
python -m nuitka --standalone --lto=yes --enable-plugins=pyqt6 --include-qt-plugins=styles,platforms --windows-console-mode=disable --windows-icon-from-ico=%ICON_PATH% --include-package=pyftpdlib --include-package=paramiko --include-package=asyncore --include-package=asynchat --output-dir=build --output-filename=%PROJECT_NAME%.exe %ENTRY_POINT%

echo [Step 3] Post-processing...
:: 自动将图标复制到 dist 目录（如果程序运行时需要从外部读取）
if exist "build\%PROJECT_NAME%.dist" (
    copy /y "%ICON_PATH%" "build\%PROJECT_NAME%.dist\"
    echo Build Finished: build\%PROJECT_NAME%.dist\%PROJECT_NAME%.exe
)

pause
