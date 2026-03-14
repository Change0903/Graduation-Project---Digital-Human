@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo Failed to install PyInstaller.
    pause
    exit /b 1
)

echo [2/3] Building EXE...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name DigitalHumanLauncher launcher_gui.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo [3/3] Build done.
echo EXE path: %cd%\dist\DigitalHumanLauncher.exe
pause
