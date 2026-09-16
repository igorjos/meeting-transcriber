# Interactive launcher for Windows: prompts for model, streaming vs log-file
# mode, and the URL/filename that mode needs, then runs transcribe.py in
# the background until interrupted (Ctrl+C).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$VenvDir = ".venv"
$py = "$VenvDir\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "No virtualenv found at $VenvDir yet - let's create one."
    Write-Host "This project requires Python 3.11+."
    Write-Host ""

    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in @("3.13", "3.12", "3.11", "3.10", "3.9")) {
            & py "-$ver" -c "" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $candidates += "py -$ver"
            }
        }
    }
    foreach ($name in @("python3", "python")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            $candidates += $name
        }
    }
    $candidates += "Other (enter a command or path)"

    Write-Host "Select the Python interpreter to create the virtualenv with:"
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-Host ("  [{0}] {1}" -f ($i + 1), $candidates[$i])
    }
    $choiceIndex = 0
    do {
        $choice = Read-Host "Enter number"
        $parsed = 0
        $valid = [int]::TryParse($choice, [ref]$parsed) -and $parsed -ge 1 -and $parsed -le $candidates.Count
        if ($valid) { $choiceIndex = $parsed }
    } until ($valid)
    $pyChoiceLabel = $candidates[$choiceIndex - 1]

    if ($pyChoiceLabel -eq "Other (enter a command or path)") {
        $pyCmd = Read-Host "Python command or path to use"
    } else {
        $pyCmd = $pyChoiceLabel
    }

    if ($pyCmd -like "py -*") {
        $verArg = $pyCmd.Substring(3)
        Write-Host "Using: $(& py $verArg --version 2>&1) ($pyCmd)"
        & py $verArg -m venv $VenvDir
    } else {
        if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue) -and -not (Test-Path $pyCmd)) {
            Write-Error "'$pyCmd' is not a runnable command or path."
            exit 1
        }
        Write-Host "Using: $(& $pyCmd --version 2>&1) ($pyCmd)"
        & $pyCmd -m venv $VenvDir
    }

    Write-Host "Installing dependencies into $VenvDir (this can take a few minutes)..."
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
    Write-Host ""
}

# A venv can exist (pass the Test-Path check above) but still be unusable -
# e.g. created with the wrong interpreter, or dependencies were never
# actually installed into it. Catch that now instead of launching
# transcribe.py and having it die instantly.
& $py -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    $ver = & $py --version 2>&1
    Write-Error "$py is $ver, but this project requires Python 3.11+. Fix: remove $VenvDir and re-run this script (then pick a 3.11+ interpreter)."
    exit 1
}

& $py -c "import numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "$VenvDir exists but required packages aren't installed in it - installing now..."
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
    Write-Host ""
    & $py -c "import numpy" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency install into $VenvDir failed. Re-run manually to see why: $py -m pip install -r requirements.txt"
        exit 1
    }
}

$models = @(
    "tiny.en", "tiny.en-q5_1", "tiny.en-q8_0",
    "base.en", "base.en-q5_1", "base.en-q8_0",
    "small.en", "small.en-q5_1", "small.en-q8_0",
    "medium.en", "medium.en-q5_0", "medium.en-q8_0",
    "large-v3", "large-v3-q5_0", "large-v3-turbo", "large-v3-turbo-q5_0"
)

Write-Host "Select a whisper.cpp model:"
for ($i = 0; $i -lt $models.Count; $i++) {
    Write-Host ("  [{0}] {1}" -f ($i + 1), $models[$i])
}
$modelIndex = 0
do {
    $choice = Read-Host "Enter number"
    $parsed = 0
    $valid = [int]::TryParse($choice, [ref]$parsed) -and $parsed -ge 1 -and $parsed -le $models.Count
    if ($valid) { $modelIndex = $parsed }
} until ($valid)
$model = $models[$modelIndex - 1]

Write-Host ""
Write-Host "Available capture devices:"
& $py -m transcribe --list-devices
Write-Host ""
$device = Read-Host "Device index from the list above (leave blank for auto-detected default)"

$transcribeArgs = @("-m", "transcribe", "--model", $model)
if ($device) {
    $transcribeArgs += @("--device", $device)
}

Write-Host ""
Write-Host "Select mode:"
Write-Host "  [1] Streaming (POST each sentence to a URL)"
Write-Host "  [2] Log file (append to a transcript file)"
do {
    $modeChoice = Read-Host "Enter number"
} until ($modeChoice -eq "1" -or $modeChoice -eq "2")

if ($modeChoice -eq "1") {
    do {
        $url = Read-Host "URL to POST each transcribed sentence to (must start with http:// or https://)"
    } until ($url)
    $transcribeArgs += @("--streaming", "--url", $url)
} else {
    $output = Read-Host "Transcript log filename [transcripts\transcript.log]"
    if (-not $output) { $output = "transcripts\transcript.log" }
    $transcribeArgs += @("--output", $output)
}

Write-Host ""
Write-Host "Starting: $py $($transcribeArgs -join ' ')"
$proc = Start-Process -FilePath $py -ArgumentList $transcribeArgs -NoNewWindow -PassThru
Write-Host "Running in background (PID $($proc.Id)). Press Ctrl+C to stop."

try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
    }
}
