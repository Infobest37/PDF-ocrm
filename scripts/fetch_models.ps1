# Ф1.0: one-shot download/verify of the multilingual-e5-small ONNX int8 model.
# Facts for the script (URLs, file names, sizes, sha256) come from the HF API
# tree endpoint. SHA256 is ALWAYS computed on the LOCAL files — the very
# artifacts the semantic benchmark was verified against; the value from HF is
# used as a cross-check (сверка) only. An already-verified local file is never
# silently overwritten: on a mismatch the human decides (delete by hand to
# force re-download). Network is allowed ONLY inside this script — runtime and
# tests run fully offline.
# Target dir: PDFGRAPH_MODELS_DIR (default C:\PDF_Data\models), subdir
# multilingual-e5-small (runtime names from pdfgraph.embedding).
# Run: powershell -ExecutionPolicy Bypass -File scripts\fetch_models.ps1
$ErrorActionPreference = "Stop"

# PS 5.1 по умолчанию поднимает только TLS 1.0, huggingface.co требует
# TLS 1.2+: без этой строки Invoke-RestMethod падает WebException'ом.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = "Xenova/multilingual-e5-small"
$modelsDir = "C:\PDF_Data\models"
if ($env:PDFGRAPH_MODELS_DIR) { $modelsDir = $env:PDFGRAPH_MODELS_DIR }
$subdir = Join-Path $modelsDir "multilingual-e5-small"
$hfBase = "https://huggingface.co/$repo"
# ВАЖНО: корень API без слага репозитория — раньше склейка дублировала
# репозиторий в пути (…/api/models/Xenova/multilingual-e5-small/tree/…) → 404.
$apiTree = "https://huggingface.co/api/models/$repo/tree/main?recursive=true"
# HF path -> local file name (runtime naming, single source: pdfgraph.embedding).
$wanted = @(
    @{ hf = "onnx/model_quantized.onnx"; local = "model_quantized.onnx" },
    @{ hf = "tokenizer.json";            local = "tokenizer.json" }
)

function Get-LocalSha256([string]$Path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

New-Item -ItemType Directory -Force -Path $subdir | Out-Null
Write-Host "HF tree: $apiTree"
$tree = Invoke-RestMethod -Uri $apiTree -UseBasicParsing
$entries = @{}
foreach ($entry in @($tree)) { $entries[$entry.path] = $entry }

foreach ($item in $wanted) {
    $hfPath = $item.hf
    $target = Join-Path $subdir $item.local
    $entry = $entries[$hfPath]
    if ($null -eq $entry) {
        throw "HF tree has no entry '$hfPath' for $repo — cannot proceed"
    }
    $hfSha = $null
    $hfSize = $entry.size
    if ($entry.lfs) {
        $hfSha = $entry.lfs.sha256
        $hfSize = $entry.lfs.size
    }

    if (Test-Path -LiteralPath $target) {
        # Local file is authoritative (verified by the benchmark): compute
        # SHA256 locally, use the HF value as a cross-check, never overwrite.
        $localSha = Get-LocalSha256 $target
        $localSize = (Get-Item -LiteralPath $target).Length
        if ($hfSha) {
            if ($localSha -ne $hfSha) {
                throw @"
verified local file differs from HF, not replacing:
    $target
    local sha256 $localSha (size $localSize)
    hf    sha256 $hfSha (size $hfSize)
Investigate first; delete the file BY HAND to re-download.
"@
            }
            Write-Host ("OK (local sha256 matches HF): {0}`n    sha256 {1}" -f $target, $localSha)
            continue
        }
        if ($hfSize -and $localSize -ne $hfSize) {
            throw @"
verified local file size differs from HF, not replacing:
    $target (local $localSize, HF $hfSize; HF gave no lfs.sha256)
"@
        }
        Write-Host ("OK (size matches, HF gave no lfs.sha256): {0}`n    local sha256 {1}" -f $target, $localSha)
        continue
    }

    Write-Host "Downloading $hfBase/resolve/main/$hfPath -> $target"
    $tmp = "$target.download"
    Invoke-WebRequest -Uri "$hfBase/resolve/main/$hfPath" -OutFile $tmp -UseBasicParsing
    $tmpSha = Get-LocalSha256 $tmp
    $tmpSize = (Get-Item -LiteralPath $tmp).Length
    if ($hfSha -and $tmpSha -ne $hfSha) {
        Remove-Item -LiteralPath $tmp -Force
        throw "sha256 mismatch after download of $hfPath : expected $hfSha, got $tmpSha"
    }
    if ($hfSize -and $tmpSize -ne $hfSize) {
        Remove-Item -LiteralPath $tmp -Force
        throw "size mismatch after download of $hfPath : expected $hfSize, got $tmpSize"
    }
    Move-Item -LiteralPath $tmp -Destination $target -Force
    Write-Host ("Saved+verified: {0}`n    sha256 {1}" -f $target, $tmpSha)
    if (-not ($hfSha -or $hfSize)) {
        Write-Warning "HF provided neither lfs.sha256 nor size for $hfPath — saved without cross-check"
    }
}

Write-Host "Models dir ready: $subdir"
exit 0