@echo off
rem Double-click launcher for the portable pdfprepare pipeline (new version).
rem Pins the project Python (the venv), the working directory, and keeps the
rem console open so the processing log stays readable.
chcp 65001 >nul
cd /d "%~dp0"
title PDF prepare (new version)
echo Preparing PDFs with the portable pipeline (pdfprepare)...
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0pdfprepare_launcher.py"
echo.
pause
