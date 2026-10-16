# Bundles the Tesseract binary, its runtime DLLs, and the rus/eng language data
# from the dev machine's Tesseract installation into `pdfprepare/tesseract/`,
# ready for the PyInstaller build to pick up as data. Run from the repository
# root:
#
#   powershell -ExecutionPolicy Bypass -File scripts\bundle_tesseract.ps1
#
# The UB Mannheim tesseract.exe is dynamically linked against the sibling DLLs
# in the install directory (libtesseract-5.dll, libleptonica-6.dll, the MinGW
# runtime, ...), so those must be copied too for the bundle to run on a machine
# without Tesseract installed.

$ErrorActionPreference = "Stop"

$TesseractRoot = "C:\Program Files\Tesseract-OCR"
$Dest = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\pdfprepare\tesseract"))

$srcExe = Join-Path $TesseractRoot "tesseract.exe"
$srcData = Join-Path $TesseractRoot "tessdata"

if (-not (Test-Path $srcExe)) { throw "tesseract.exe not found at $srcExe" }
if (-not (Test-Path $srcData)) { throw "tessdata directory not found at $srcData" }

New-Item -ItemType Directory -Force -Path (Join-Path $Dest "tessdata") | Out-Null

Copy-Item -Path $srcExe -Destination (Join-Path $Dest "tesseract.exe") -Force
Copy-Item -Path (Join-Path $TesseractRoot "*.dll") -Destination $Dest -Force
Copy-Item -Path (Join-Path $srcData "rus.traineddata") -Destination (Join-Path $Dest "tessdata\rus.traineddata") -Force
Copy-Item -Path (Join-Path $srcData "eng.traineddata") -Destination (Join-Path $Dest "tessdata\eng.traineddata") -Force

Write-Host "Bundled tesseract.exe + runtime DLLs + rus/eng traineddata into $Dest"
