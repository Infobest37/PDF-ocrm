@echo off
rem Launcher for the knowledge-graph GUI app (pdfgraph).
rem Keeps the working directory at the project root so the default
rem graph.db and the registry file are found relative to this script.
cd /d "%~dp0"
start "" "dist\pdfgraph\pdfgraph.exe"
