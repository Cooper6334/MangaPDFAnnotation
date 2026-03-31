@echo off
cd /d "%~dp0"

for /f "tokens=1-3 delims=/" %%a in ("%date%") do set YMD=%%a%%b%%c
for /f "tokens=1-3 delims=:." %%a in ("%time: =0%") do set HMS=%%a%%b%%c
set ZIPNAME=JPocr_%YMD%_%HMS%.zip
set TMPDIR=%~dp0_pack_tmp\JPocr

mkdir "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\.claude\commands" 2>nul

copy /Y app.py              "%TMPDIR%\"
copy /Y ocr.py              "%TMPDIR%\"
copy /Y insert.py           "%TMPDIR%\"
copy /Y translate.py        "%TMPDIR%\"
copy /Y translate-prompt.md "%TMPDIR%\"
copy /Y glossary.txt        "%TMPDIR%\"
copy /Y requirements.txt    "%TMPDIR%\"
copy /Y run.bat             "%TMPDIR%\"
copy /Y ".claude\commands\ocr-translate.md"   "%TMPDIR%\.claude\commands\"
copy /Y ".claude\commands\translate-manga.md" "%TMPDIR%\.claude\commands\"

powershell -Command "Compress-Archive -Force -Path '%~dp0_pack_tmp\JPocr' -DestinationPath '%~dp0%ZIPNAME%'"

rmdir /S /Q "%~dp0_pack_tmp"

echo.
echo Packed: %ZIPNAME%
pause
