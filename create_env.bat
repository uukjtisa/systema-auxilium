@echo off
SET VENV_NAME=.venv

REM Check if Python 3.10.11 is installed
py -3.10 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo Python 3.10 detected. Creating venv using Python 3.10.11...
    py -3.10 -m venv "%VENV_NAME%"
) ELSE (
    echo Python 3.10.11 not found. Using default Python version...
    python -m venv "%VENV_NAME%"
)

REM Create helper batch files that use the venv
echo Creating helper batch files...

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
  echo REM Replace main.py with your script name if needed
  echo python "%%~dp0main.py"
  echo pause
) > run.bat

REM install_requirements.bat (fixed version)
(
  echo @echo off
  echo call "%%~dp0%VENV_NAME%\Scripts\activate.bat"
  echo if exist "%%~dp0requirements.txt" ^(
  echo     pip install -r "%%~dp0requirements.txt"
  echo ^) else ^(
  echo     echo No requirements.txt found.
  echo ^)
  echo pause
) > install_requirements.bat

echo Helper files created: open_env.bat, run.bat, install_requirements.bat

REM Activate the venv in the current window
echo Activating virtual environment in this window...
call "%VENV_NAME%\Scripts\activate.bat"

REM Show Python version inside venv
python --version
pause
