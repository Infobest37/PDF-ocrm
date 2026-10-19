$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dirs = @('pdfprepare','tests','docs','.superpowers','pdfgraph') | Where-Object { Test-Path -LiteralPath $_ }
$rootFiles = Get-ChildItem . -File -Force | Where-Object {
    ($_.Extension -in @('.py','.md','.txt','.json','.bat','.spec')) -or ($_.Name -eq '.gitignore')
}
$paths = @($dirs) + @($rootFiles | ForEach-Object { $_.Name })
Write-Output ('ZIP_SOURCE_COUNT: ' + $paths.Count)
Write-Output ('ZIP_SOURCE_DIRS: ' + ($dirs -join ','))
Compress-Archive -Path $paths -DestinationPath '..\PDF_OCR_APP_worktree_2026-10-19.zip' -Force
$z = Get-Item -LiteralPath '..\PDF_OCR_APP_worktree_2026-10-19.zip'
Write-Output ('ZIP_SIZE_MB: ' + [math]::Round($z.Length/1MB,1))
Write-Output ('ZIP_EXISTS: ' + [bool]$z)