<#
.SYNOPSIS
    Build WTToolbox.exe with PyInstaller.

.DESCRIPTION
    Produces a single-file, console-free executable in .\dist\WTToolbox.exe.
    Intermediate artefacts go to D:\ThunderKit-build\pyi so the project folder
    stays clean.

    Qt modules this app never touches are excluded to keep the binary small and
    startup fast.  QtCore / QtGui / QtWidgets / QtSvg / QtNetwork are required
    and are deliberately NOT excluded (QtSvg renders the icon set, QtNetwork
    provides the single-instance socket).

    NOTE: this file is deliberately ASCII-only in its commands - Windows
    PowerShell 5.1 otherwise reads .ps1 as ANSI and mangles non-ASCII text.

.PARAMETER Onedir
    Build a folder instead of a single file.  Starts noticeably faster.

.PARAMETER NoClean
    Skip --clean (reuse the PyInstaller cache) for a quicker rebuild.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Onedir
#>
[CmdletBinding()]
param(
    [switch]$Onedir,
    [switch]$NoClean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Where the interpreter and the intermediate trees live.  Override either with
# an environment variable if your checkout is laid out differently:
#
#   $env:WTTOOLBOX_PYTHON = 'C:\Python312\python.exe'
#   $env:WTTOOLBOX_BUILD  = 'D:\build\wttoolbox'
#
# Otherwise a .venv inside the project is used, then whatever "python" is on
# PATH, so a fresh clone can build without editing this file.
$python = $env:WTTOOLBOX_PYTHON
if (-not $python) {
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path $venv) {
        $python = $venv
    } else {
        $onPath = Get-Command python -ErrorAction SilentlyContinue
        if ($onPath) { $python = $onPath.Source } else { $python = 'python' }
    }
}

$build_root = $env:WTTOOLBOX_BUILD
if (-not $build_root) { $build_root = Join-Path $root 'build\pyi' }

# Separate work/dist trees per mode: sharing them makes --no-clean reuse a
# stale analysis from the other mode and silently ship an out-of-date binary.
$mode_dir = if ($Onedir) { 'onedir' } else { 'onefile' }
$work = Join-Path $build_root "$mode_dir\work"
$dist = Join-Path $build_root "$mode_dir\dist"

if (-not (Test-Path $python)) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $python = $onPath.Source }
}
try {
    & $python -c "import PySide6, PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "missing modules" }
} catch {
    throw ("Build python cannot import PySide6 and PyInstaller: {0}`n" +
           "Point WTTOOLBOX_PYTHON at an interpreter that has them, e.g.`n" +
           "  python -m venv .venv; .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt") -f $python
}

$icon = Join-Path $root 'src\wttoolbox\assets\icon.ico'
$banner = Join-Path $root 'src\wttoolbox\assets\banner.png'
$versionFile = Join-Path $root 'build\version_info.txt'
foreach ($f in @($icon, $banner, $versionFile)) {
    if (-not (Test-Path $f)) { throw "Missing build asset: $f" }
}

# Qt submodules WTToolbox does not import - removing them saves tens of MB.
$excludes = @(
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtQmlModels', 'PySide6.QtQmlWorkerScript',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtXmlPatterns',
    'PySide6.QtConcurrent', 'PySide6.QtPrintSupport',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    'PySide6.QtDesigner', 'PySide6.QtUiTools', 'PySide6.QtHelp',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtWebSockets', 'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtStateMachine', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
    'PySide6.QtRemoteObjects', 'PySide6.QtSpatialAudio', 'PySide6.QtTextToSpeech',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning', 'PySide6.QtLocation',
    'PySide6.QtNetworkAuth', 'PySide6.QtHttpServer', 'PySide6.QtSensors',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'tkinter', 'turtle', 'turtledemo', 'idlelib', 'pydoc_data', 'lib2to3',
    'unittest', 'doctest', 'pdb', 'distutils', 'setuptools', 'pip', 'wheel',
    'numpy', 'pandas', 'matplotlib', 'PIL', 'lxml', 'docx', 'pptx', 'openpyxl',
    'sqlite3', 'pysqlite2', 'test'
)

$assets = Join-Path $root 'src\wttoolbox\assets'
$arguments = [System.Collections.Generic.List[string]]::new()
$arguments.Add('-m'); $arguments.Add('PyInstaller')
$arguments.Add('--noconfirm')
if (-not $NoClean) { $arguments.Add('--clean') }
$arguments.Add('--name'); $arguments.Add('WTToolbox')
$arguments.Add('--windowed')
$arguments.Add('--noupx')
if (-not $Onedir) { $arguments.Add('--onefile') }
$arguments.Add('--icon'); $arguments.Add($icon)
$arguments.Add('--version-file'); $arguments.Add($versionFile)
$arguments.Add('--add-data'); $arguments.Add("$assets;assets")
$arguments.Add('--paths'); $arguments.Add((Join-Path $root 'src'))
# Belt and braces: the pages are imported statically now, but naming them
# explicitly (and collecting the package) means a future lazy import cannot
# silently drop a page from the frozen build.
$arguments.Add('--collect-submodules'); $arguments.Add('wttoolbox')
foreach ($module in @(
    'wttoolbox.ui.pages.home', 'wttoolbox.ui.pages.sound', 'wttoolbox.ui.pages.tools',
    'wttoolbox.ui.pages.library', 'wttoolbox.ui.pages.settings',
    'wttoolbox.ui.pages.vehicles', 'wttoolbox.ui.pages.stats',
    'wttoolbox.ui.dialogs.config_editor', 'wttoolbox.ui.dialogs.vehicle_picker'
)) {
    $arguments.Add('--hidden-import'); $arguments.Add($module)
}
$arguments.Add('--workpath'); $arguments.Add($work)
$arguments.Add('--distpath'); $arguments.Add($dist)
$arguments.Add('--specpath'); $arguments.Add((Join-Path $root "build\spec-$mode_dir"))
foreach ($module in $excludes) {
    $arguments.Add('--exclude-module'); $arguments.Add($module)
}
$arguments.Add((Join-Path $root 'src\main.py'))

$mode = if ($Onedir) { 'onedir' } else { 'onefile' }
Write-Host "WTToolbox build starting..."
Write-Host "  mode      : $mode"
Write-Host "  output    : $dist"
Write-Host "  work dir  : $work"
Write-Host ""

& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$target = if ($Onedir) { Join-Path $dist 'WTToolbox' } else { Join-Path $dist 'WTToolbox.exe' }
if (-not (Test-Path $target)) { throw "Build produced no artefact at $target" }

$info = Get-Item $target
if ($info.PSIsContainer) {
    $size = (Get-ChildItem $target -Recurse -File | Measure-Object -Property Length -Sum).Sum
} else {
    $size = $info.Length
}
Write-Host ""
Write-Host "BUILD OK" -ForegroundColor Green
Write-Host ("  artefact : {0}" -f $target)
Write-Host ("  size     : {0:N1} MB" -f ($size / 1MB))

$localDist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force -Path $localDist | Out-Null
if ($Onedir) {
    # Copy-Item -Recurse onto an existing folder nests instead of replacing,
    # so clear the destination first.
    $destDir = Join-Path $localDist 'WTToolbox'
    if (Test-Path $destDir) { Remove-Item -Recurse -Force $destDir }
    Copy-Item -Recurse -Force $target $destDir
    $built = Join-Path $destDir 'WTToolbox.exe'
} else {
    Copy-Item -Force $target (Join-Path $localDist 'WTToolbox.exe')
    $built = Join-Path $localDist 'WTToolbox.exe'
}
Write-Host ("  copied to: {0}" -f $localDist)

$hash = (Get-FileHash -Algorithm SHA256 -Path $built).Hash
Write-Host ("  sha256   : {0}" -f $hash)
Write-Host ("  finished : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
