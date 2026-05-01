@echo off
SET VENV_NAME=.venv

echo ========================================
echo  Systema Auxilium Environment Setup
echo ========================================
echo.

REM Check if Python 3.10.11 is installed
py -3.10 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [1/5] Python 3.10 detected. Creating venv...
    py -3.10 -m venv "%VENV_NAME%"
) ELSE (
    echo [1/5] Python 3.10.11 not found. Using default Python version...
    python -m venv "%VENV_NAME%"
)

echo.
echo [2/5] Creating helper batch files...

REM open_env.bat
(
  echo @echo off
  echo start "" cmd /k "%%~dp0%VENV_NAME%\Scripts\activate.bat"
) > open_env.bat

REM run.bat
(
  echo @echo off
  echo cd /d "%%~dp0"
  echo call "%%~dp0%VENV_NAME%\Scripts\activate.bat"
  echo python "%%~dp0main.py"
  echo pause
) > run.bat

REM add_autostart.bat
(
  echo @echo off
  echo REM Adds Systema Auxilium to start at user login WITH ADMIN PRIVILEGES
  echo REM Works for ANY user on ANY computer
  echo.
  echo set "SCRIPT_DIR=%%~dp0"
  echo set "RUN_BAT=%%SCRIPT_DIR%%run.bat"
  echo.
  echo schtasks /create /tn "SystemaAuxilium_AutoStart" /tr "\"%%RUN_BAT%%\"" /sc onlogon /ru "%%USERDOMAIN%%\%%USERNAME%%" /rl HIGHEST /f
  echo.
  echo if %%ERRORLEVEL%% EQU 0 ^(
  echo     echo Scheduled task 'SystemaAuxilium_AutoStart' added successfully with admin rights.
  echo     echo Task points to: %%RUN_BAT%%
  echo     echo Running as user: %%USERDOMAIN%%\%%USERNAME%%
  echo ^) else ^(
  echo     echo Failed to create task. Please run this script as Administrator.
  echo ^)
  echo pause
) > add_autostart.bat

REM remove_autostart.bat
(
  echo @echo off
  echo REM Removes Systema Auxilium auto-start scheduled task
  echo REM Works for ANY user on ANY computer
  echo.
  echo echo Removing scheduled task 'SystemaAuxilium_AutoStart'...
  echo schtasks /delete /tn "SystemaAuxilium_AutoStart" /f
  echo.
  echo if %%ERRORLEVEL%% EQU 0 ^(
  echo     echo Scheduled task 'SystemaAuxilium_AutoStart' removed successfully.
  echo     echo Task was running as: %%USERDOMAIN%%\%%USERNAME%%
  echo ^) else ^(
  echo     echo Failed to remove task. It may not exist or you need admin privileges.
  echo ^)
  echo pause
) > remove_autostart.bat

echo    - open_env.bat
echo    - run.bat
echo    - add_autostart.bat
echo    - remove_autostart.bat

echo.
echo [3/5] Activating virtual environment...
call "%VENV_NAME%\Scripts\activate.bat"

echo.
echo [4/5] Installing dependencies from requirements.txt...
if exist "requirements.txt" (
    pip install -r "requirements.txt"
    if %ERRORLEVEL% EQU 0 (
        echo Dependencies installed successfully!
    ) else (
        echo WARNING: Some dependencies failed to install.
    )
) else (
    echo WARNING: No requirements.txt found. Skipping dependency installation.
)

echo.
echo [5/5] Verifying Python version in venv...
python --version

echo.
echo ========================================
echo  Setup Complete!
echo ========================================
echo.
echo Your environment is ready! You can now:
echo   - Run the app: run.bat
echo   - Open terminal with venv: open_env.bat
echo   - Enable autostart: add_autostart.bat (as Admin)
echo   - Disable autostart: remove_autostart.bat (as Admin)
echo.
pause