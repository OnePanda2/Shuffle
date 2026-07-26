@echo off
REM Build a standalone Windows executable for the Smart VLC Randomizer.
REM Produces dist\SmartVLCRandomizer.exe (a single double-clickable file).
REM Requires: py -3.10 -m pip install pyinstaller
setlocal
cd /d "%~dp0"
echo Building SmartVLCRandomizer.exe ...
py -3.10 -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "SmartVLCRandomizer" ^
    --collect-submodules vlc_randomizer ^
    main.py
echo.
echo Done. The executable is in the dist folder:
echo    %~dp0dist\SmartVLCRandomizer.exe
pause
