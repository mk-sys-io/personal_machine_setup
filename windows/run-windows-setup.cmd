@echo off
setlocal EnableExtensions
set "SETUP_SCRIPT=%~dp0windows-setup.ps1"
if not exist "%SETUP_SCRIPT%" (
    echo ERROR: windows-setup.ps1 not found next to this launcher.
    pause
    exit /b 1
)

net session >nul 2>&1
if not errorlevel 1 goto run

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; try { $arg = '-NoProfile -ExecutionPolicy Bypass -File ' + [char]34 + $env:SETUP_SCRIPT + [char]34; $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $arg -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { Write-Host $_.Exception.Message; exit 1 }"
set "RC=%ERRORLEVEL%"
goto done

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP_SCRIPT%"
set "RC=%ERRORLEVEL%"

:done
echo.
echo Exit code: %RC%
pause
exit /b %RC%