# Synthetic launcher contract only: never imports the real WCF UI or starts HTTP.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$OutputRoot
)
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/start-web-ui.ps1'
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot must be a new directory; no overwrite or cleanup.' }
$output = [IO.Path]::GetFullPath($OutputRoot)
$null = New-Item -ItemType Directory -Path $output
$fixture = Join-Path $output 'SYNTHETIC repo with space'
$scripts = Join-Path $fixture 'scripts'
$package = Join-Path $fixture 'web_content_fetcher'
$caller = Join-Path $output 'SYNTHETIC caller with space'
foreach ($dir in @($scripts,$package,$caller)) { $null = New-Item -ItemType Directory -Path $dir -Force }
Copy-Item -LiteralPath $source -Destination (Join-Path $scripts 'start-web-ui.ps1')
Set-Content -LiteralPath (Join-Path $package '__init__.py') -Value '' -Encoding UTF8
$stub = @'
import json,os,sys
from pathlib import Path
Path(os.environ['WCF_SYNTHETIC_MARKER']).write_text(json.dumps({
 'synthetic_only':True,'cwd':os.getcwd(),'pythonpath':os.environ.get('PYTHONPATH'),
 'python':sys.executable,'argv':sys.argv[1:]},ensure_ascii=False),encoding='utf-8')
raise SystemExit(int(os.environ['WCF_SYNTHETIC_EXIT']))
'@
Set-Content -LiteralPath (Join-Path $package 'ui.py') -Value $stub -Encoding UTF8
$driver = Join-Path $output 'synthetic-driver.ps1'
$driverText = @'
param([string]$Launcher,[string]$Interpreter,[string]$Caller,[string]$Report,[string]$Mode)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $Caller
if($Mode -eq 'absent'){Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue}
else{$env:PYTHONPATH='SYNTHETIC previous Python path'}
if($Mode -eq 'default'){$env:PATH=(Split-Path -Parent $Interpreter)+[IO.Path]::PathSeparator+$env:PATH}
if($Mode -eq 'function'){
 function Invoke-SyntheticNonNativePython { throw 'Must not invoke a non-native interpreter function.' }
 $Interpreter='Invoke-SyntheticNonNativePython'
}
if($Mode -eq 'missing'){$Interpreter=Join-Path $Caller 'SYNTHETIC-missing-python.exe'}
$expectedPresent=Test-Path -LiteralPath Env:PYTHONPATH
$expectedValue=$env:PYTHONPATH
$beforePath=$env:PATH
$global:LASTEXITCODE=29
$caught=$null
try {
 if($Mode -eq 'default'){& $Launcher -UiArgs @('--host','SYNTHETIC host','--port','7319')}
 else {& $Launcher -PythonExecutable $Interpreter -UiArgs @('--host','SYNTHETIC host','--port','7319')}
}
catch {$caught=@{type=$_.Exception.GetType().FullName;message=$_.Exception.Message;stack=$_.ScriptStackTrace;id=$_.FullyQualifiedErrorId}}
$observed=$global:LASTEXITCODE
$result=@{
 cwd_restored=((Get-Location).Path -eq $Caller)
 pythonpath_presence_restored=((Test-Path -LiteralPath Env:PYTHONPATH) -eq $expectedPresent)
 pythonpath_value_restored=($env:PYTHONPATH -eq $expectedValue)
 path_unchanged=($env:PATH -eq $beforePath)
 native_exit_observed=$observed
 caught=$caught
}
$result|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $Report -Encoding UTF8
if($null -ne $caught){exit 1}
if($null -eq $observed){exit 1}
exit ([int]$observed)
'@
Set-Content -LiteralPath $driver -Value $driverText -Encoding UTF8
$powerShell = (Get-Process -Id $PID).Path
$cases = @(
    @{name='native-zero-present';mode='present';child=0;expected=0},
    @{name='native-seven-present';mode='present';child=7;expected=7},
    @{name='native-zero-absent';mode='absent';child=0;expected=0},
    @{name='default-python-zero';mode='default';child=0;expected=0},
    @{name='missing-interpreter';mode='missing';child=0;expected=1},
    @{name='non-native-function-rejected';mode='function';child=0;expected=1}
)
$results = @()
foreach ($case in $cases) {
    $marker = Join-Path $output ($case.name+'-child.json')
    $report = Join-Path $output ($case.name+'-driver.json')
    $stdout = Join-Path $output ($case.name+'-stdout.log')
    $stderr = Join-Path $output ($case.name+'-stderr.log')
    $env:WCF_SYNTHETIC_MARKER=$marker
    $env:WCF_SYNTHETIC_EXIT=[string]$case.child
    & $powerShell -NoProfile -File $driver -Launcher (Join-Path $scripts 'start-web-ui.ps1') -Interpreter $PythonExecutable -Caller $caller -Report $report -Mode $case.mode 1> $stdout 2> $stderr
    $observed = $LASTEXITCODE
    $rawError = [string](Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
    if ($rawError -match 'AuthorizationManager|UnauthorizedAccess|ExecutionPolicy|Access is denied') {
        throw ('Runtime policy/permission refusal; affected test stopped without retry: '+$rawError)
    }
    if (-not (Test-Path -LiteralPath $report)) { throw ('Missing driver receipt; exit '+$observed) }
    $receipt=Get-Content -LiteralPath $report -Raw|ConvertFrom-Json
    if($null -ne $receipt.caught -and ([string]$receipt.caught.message -match 'AuthorizationManager|UnauthorizedAccess|ExecutionPolicy|Access is denied')){throw ('Policy refusal in driver: '+$receipt.caught.message)}
    if($observed -ne $case.expected){throw ($case.name+' expected '+$case.expected+' observed '+$observed)}
    if(-not $receipt.cwd_restored -or -not $receipt.pythonpath_presence_restored -or -not $receipt.pythonpath_value_restored -or -not $receipt.path_unchanged){throw ('Caller state not restored: '+$case.name)}
    $shouldRun=$case.mode -notin @('missing','function')
    if((Test-Path -LiteralPath $marker) -ne $shouldRun){throw ('Unexpected child launch: '+$case.name)}
    if($shouldRun){
        $child=Get-Content -LiteralPath $marker -Raw|ConvertFrom-Json
        if(-not $child.synthetic_only -or $child.cwd -ne $fixture -or $child.pythonpath -ne $fixture){throw 'Wrong synthetic child root'}
        if(($child.argv -join '|') -ne '--host|SYNTHETIC host|--port|7319'){throw 'Arguments did not round-trip'}
        if([IO.Path]::GetFullPath($child.python) -ne [IO.Path]::GetFullPath($PythonExecutable)){throw 'Unexpected Python selection'}
    }
    $results += @{name=$case.name;passed=$true;expected_exit=$case.expected;observed_exit=$observed;synthetic_child_ran=$shouldRun}
}
@{synthetic_only=$true;passed=$results.Count;failed=0;skip=0;cases=$results;actual_WCF_UI_started=$false;HTTP_browser_network_model=$false;security_settings_changed=$false}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $output 'RESULT.json') -Encoding UTF8
Write-Output ('PASS '+$results.Count+' synthetic launcher cases; no actual UI/server/network.')
