$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Виртуальное окружение .venv не найдено."
}

& $Python -m pip install --upgrade "pyinstaller>=6.14,<7" "pillow>=11,<12"
& $Python -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "Ven4Control.spec")

Write-Host "Готово: $ProjectRoot\dist\Ven4Control.exe"
