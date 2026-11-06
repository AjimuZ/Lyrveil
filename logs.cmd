@rem Runs "scripts\lyrveil.ps1 logs" with Windows PowerShell 5.1, passes every argument, and returns its exit status.
@"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lyrveil.ps1" logs %*
@exit /b %ERRORLEVEL%
