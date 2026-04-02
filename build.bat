@echo off
echo ============================================
echo Building skill-runner.exe
echo ============================================

REM Install pyinstaller if not present
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing pyinstaller...
    pip install pyinstaller
)

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist skill-runner.spec del /q skill-runner.spec

REM Build
echo Building...
pyinstaller build.spec --noconfirm

REM Check result
if exist dist\skill-runner\skill-runner.exe (
    echo.
    echo ============================================
    echo Build successful!
    echo Output: dist\skill-runner\skill-runner.exe
    echo ============================================
    echo.
    echo Usage:
    echo   dist\skill-runner\skill-runner.exe ^<skill-name^> [user-request]
    echo   dist\skill-runner\skill-runner.exe --list
) else (
    echo.
    echo ============================================
    echo Build failed!
    echo ============================================
    exit /b 1
)
