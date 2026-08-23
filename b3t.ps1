$Runner = Join-Path $PSScriptRoot "lib/venv_exec.py"
$B3tPath = Join-Path $PSScriptRoot "apps/b3t/__main__.py"

$Python = Get-Command python3 -ErrorAction SilentlyContinue
if ($Python) { & $Python.Source $Runner b3t $B3tPath @args; exit $LASTEXITCODE }
$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) { & $Python.Source $Runner b3t $B3tPath @args; exit $LASTEXITCODE }
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) { & $Python.Source -3 $Runner b3t $B3tPath @args; exit $LASTEXITCODE }

Write-Error "Could not find python3, python, or py on PATH."
exit 127
