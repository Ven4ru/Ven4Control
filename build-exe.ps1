$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Виртуальное окружение .venv не найдено."
}

& $Python -m pip install --upgrade "pyinstaller>=6.14,<7" "pillow>=11,<12"

# version_info.txt собирается из константы версии, а не правится руками:
# раньше номер в нём расходился с pyproject.toml, и готовый EXE представлялся
# не той версией, что на самом деле собрана.
& $Python (Join-Path $ProjectRoot "tools\gen_version_info.py")
if ($LASTEXITCODE -ne 0) { throw "Не удалось сгенерировать version_info.txt" }

& $Python -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "Ven4Control.spec")

Write-Host "Готово: $ProjectRoot\dist\Ven4Control.exe"