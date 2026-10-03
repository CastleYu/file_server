Param()

$PROJECT_NAME = 'AbyssFS'
$ENTRY_POINT = 'ftp_app.py'
$ICON_PATH = 'assets\icons\ftp.ico'

$ROOT = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ROOT

Write-Host "[Step 1] Cleaning old builds..."
if (Test-Path -Path build) { Remove-Item -Recurse -Force build }
if (Test-Path -Path "$PROJECT_NAME.dist") { Remove-Item -Recurse -Force "$PROJECT_NAME.dist" }
if (Test-Path -Path "$PROJECT_NAME.exe") { Remove-Item -Force "$PROJECT_NAME.exe" }

Write-Host "[Step 2] Starting Nuitka Build..."
# 使用与现有 Windows 批处理相同的选项
python -m nuitka --standalone --lto=yes --enable-plugins=pyqt6 --include-qt-plugins=styles,platforms --windows-console-mode=disable --windows-icon-from-ico=$ICON_PATH --include-data-files=assets/icons/ftp.ico=assets/icons/ftp.ico --include-data-files=assets/icons/fs.ico=assets/icons/fs.ico --include-package=pyftpdlib --include-package=paramiko --include-package=impacket --include-package=wsgidav --include-package=cheroot --include-package=asyncore --include-package=asynchat --output-dir=build --output-filename=$PROJECT_NAME.exe $ENTRY_POINT

Write-Host "[Step 3] Post-processing..."
$distDir = Get-ChildItem -Path build -Directory | Where-Object { $_.Name -like "*.dist" } | Select-Object -First 1
if ($distDir) {
    $iconOut = Join-Path $distDir.FullName 'assets\icons'
    New-Item -ItemType Directory -Path $iconOut -Force | Out-Null
    Copy-Item -Path 'assets\icons\*.ico' -Destination $iconOut -Force -ErrorAction SilentlyContinue
    Write-Host "Build Finished: $($distDir.FullName)\$PROJECT_NAME.exe"
} else {
    Write-Host "Build output directory not found; checking for exe in build folder."
    $exe = Get-ChildItem -Path build -Filter '*.exe' -Recurse | Select-Object -First 1
    if ($exe) { Write-Host "Build Finished: $($exe.FullName)" }
    else { Write-Error "No executable found."; exit 1 }
}

Write-Host "Done."
