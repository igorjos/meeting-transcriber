# Interactive launcher for Windows: prompts for email, model, client/topic,
# streaming vs log-file mode, and the URL/filename that mode needs (reusing
# what was saved in .env last time), then runs transcribe.py until
# interrupted (Ctrl+C).
#
# Not comfortable with PowerShell? Double-click run_windows.cmd instead of
# this file - it runs this script for you and keeps the window open no
# matter what happens. See README.md "Windows quick start" for step-by-step
# instructions written for someone who has never used PowerShell before.
#
# Whatever goes wrong, and however this script is closed - a setup error, a
# bad answer, transcribe.py itself exiting, or a normal Ctrl+C - the window
# always prints why before it closes and waits for Enter, instead of just
# vanishing. That's what the try/catch around the whole script and the
# Read-Host at the very bottom are for.
$ErrorActionPreference = "Stop"

function Assert-Success([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Message (exit code $LASTEXITCODE)"
    }
}

$exitCode = 1
try {
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
                # A version the `py` launcher doesn't have installed writes to
                # stderr ("No suitable Python runtime found..."); redirecting
                # that with 2>$null still turns it into an ErrorRecord first,
                # which $ErrorActionPreference = "Stop" would then throw on -
                # silencing it that way instead of at the source stops this
                # whole probe loop (and the script) on the very first missing
                # version, before ever reaching a prompt.
                #
                # The one-liner is "pass", not "" - Windows PowerShell 5.1 drops an
                # empty-string argument entirely when building a native command's
                # command line, so `-c ""` actually reaches py.exe as bare `-c` with
                # nothing after it, which Python rejects ("Argument expected for the
                # -c option") for *every* version probed, installed or not - this
                # silently emptied $candidates of every "py -X.Y" entry regardless of
                # what's actually installed. A non-empty argument isn't dropped.
                $ErrorActionPreference = "SilentlyContinue"
                & py "-$ver" -c "pass" 2>$null
                $ErrorActionPreference = "Stop"
                if ($LASTEXITCODE -eq 0) {
                    $candidates += "py -$ver"
                }
            }
        }
        $probedVersionFound = $candidates.Count -gt 0
        foreach ($name in @("python3", "python")) {
            if (Get-Command $name -ErrorAction SilentlyContinue) {
                $candidates += $name
            }
        }
        $candidates += "Other (enter a command or path)"

        if (-not $probedVersionFound) {
            # None of 3.9-3.13 (the versions this project is known to work with)
            # were found via the `py` launcher - whatever "python"/"python3"
            # below resolves to might be a much newer release that this
            # project's dependencies (torch in particular) don't have
            # ready-built packages for yet, which can make the install below
            # fail partway through. Not fatal - just worth knowing before
            # picking it.
            Write-Host "Note: no Python 3.9-3.13 installation was found via the 'py' launcher."
            Write-Host "If 'python'/'python3' below turns out to be a much newer release, dependency"
            Write-Host "installation can fail - see the Windows section of README.md for the"
            Write-Host "recommended version if that happens."
            Write-Host ""
        }

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
            Assert-Success "Failed to create the virtual environment with '$pyCmd'"
        } else {
            if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue) -and -not (Test-Path $pyCmd)) {
                throw "'$pyCmd' is not a runnable command or path."
            }
            Write-Host "Using: $(& $pyCmd --version 2>&1) ($pyCmd)"
            & $pyCmd -m venv $VenvDir
            Assert-Success "Failed to create the virtual environment with '$pyCmd'"
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
    #
    # Each `2>$null` below silences the *text* of a failure, but under
    # $ErrorActionPreference = "Stop" the redirect itself still promotes any
    # stderr line to an ErrorRecord first, which then throws right there -
    # bypassing the $LASTEXITCODE check on the next line entirely and ending
    # the script with no useful message. Turning EAP off just for the native
    # call (and back on immediately after, before anything else can throw)
    # keeps the intended behavior: check $LASTEXITCODE ourselves, decide what
    # to do about it.
    $ErrorActionPreference = "SilentlyContinue"
    & $py -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" 2>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        $ver = & $py --version 2>&1
        throw "$py is $ver, but this project requires Python 3.11+. Fix: remove $VenvDir and re-run this script (then pick a 3.11+ interpreter)."
    }

    $ErrorActionPreference = "SilentlyContinue"
    & $py -c "import numpy" 2>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$VenvDir exists but required packages aren't installed in it - installing now..."
        & $py -m pip install --upgrade pip
        & $py -m pip install -r requirements.txt
        Write-Host ""
        $ErrorActionPreference = "SilentlyContinue"
        & $py -c "import numpy" 2>$null
        $ErrorActionPreference = "Stop"
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency install into $VenvDir failed. Re-run manually to see why: $py -m pip install -r requirements.txt"
        }
    }

    $models = @(
        "tiny.en", "tiny.en-q5_1", "tiny.en-q8_0",
        "base.en", "base.en-q5_1", "base.en-q8_0",
        "small.en", "small.en-q5_1", "small.en-q8_0",
        "medium.en", "medium.en-q5_0", "medium.en-q8_0",
        "large-v3", "large-v3-q5_0", "large-v3-turbo", "large-v3-turbo-q5_0"
    )

    # Email, model, the streaming endpoint URL and the API key header are
    # remembered in .env (managed by settings.py, which also validates them); the
    # client/topic and the store (upload) URL are asked every session and never
    # saved. A saved value is used as-is unless the user says they want to change
    # saved settings; a missing one is asked for.
    function Get-Setting([string]$Key) {
        $value = & $py settings.py get $Key
        if ($LASTEXITCODE -ne 0) { return $null }
        if ($null -eq $value) { return "" }
        return "$value"
    }

    function Set-Setting([string]$Key, [string]$Value) {
        $env:SETTINGS_VALUE = $Value
        & $py settings.py set $Key | Out-Null
        $saved = ($LASTEXITCODE -eq 0)
        Remove-Item Env:SETTINGS_VALUE -ErrorAction SilentlyContinue
        return $saved
    }

    function Resolve-Setting([string]$Key, [string]$Label, [string]$Prompt) {
        $saved = Get-Setting $Key
        if ($null -ne $saved) {
            if (-not $changeSaved) {
                Write-Host "Using saved ${Label}: $saved"
                return $saved
            }
            $answer = Read-Host "$Prompt [$saved]"
            if (-not $answer) { $answer = $saved }
            if ($answer -eq $saved) { return $saved }
        } else {
            $answer = Read-Host $Prompt
        }
        while (-not (Set-Setting $Key $answer)) {
            $answer = Read-Host $Prompt
        }
        return (Get-Setting $Key)
    }

    function Resolve-ApiKey {
        $saved = Get-Setting "TRANSCRIBER_AUTH_HEADER"
        if ($null -ne $saved -and -not $changeSaved) {
            if ($saved) { Write-Host "Using saved API key." } else { Write-Host "No API key saved (the endpoint needs none)." }
            return $saved
        }
        Write-Host "API key header sent with every POST, as 'Name: value' (e.g. 'Authorization: Bearer <token>')."
        if ($null -ne $saved) {
            Write-Host "Press Enter to keep the saved one, or type 'none' to remove it."
        } else {
            Write-Host "Leave blank if the endpoint needs no key."
        }
        while ($true) {
            $answer = Read-Host "API key header"
            if (-not $answer -and $null -ne $saved) { return $saved }
            if ($answer -eq "none") { $answer = "" }
            if (Set-Setting "TRANSCRIBER_AUTH_HEADER" $answer) { return (Get-Setting "TRANSCRIBER_AUTH_HEADER") }
        }
    }

    $changeSaved = $false
    & $py settings.py show
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        $changeAnswer = Read-Host "Change any of the saved settings? [y/N]"
        $changeSaved = $changeAnswer -match "^[Yy]"
    }
    Write-Host ""

    $email = Resolve-Setting "TRANSCRIBER_EMAIL" "email" "Your email address"

    $model = Get-Setting "TRANSCRIBER_MODEL"
    if ($null -ne $model -and -not $changeSaved) {
        Write-Host "Using saved model: $model"
    } else {
        $savedModel = $model
        Write-Host "Select a whisper.cpp model:"
        for ($i = 0; $i -lt $models.Count; $i++) {
            Write-Host ("  [{0}] {1}" -f ($i + 1), $models[$i])
        }
        $model = $null
        do {
            if ($savedModel) {
                $choice = Read-Host "Enter number [Enter keeps '$savedModel']"
            } else {
                $choice = Read-Host "Enter number"
            }
            if (-not $choice -and $savedModel) {
                $model = $savedModel
            } else {
                $parsed = 0
                if ([int]::TryParse($choice, [ref]$parsed) -and $parsed -ge 1 -and $parsed -le $models.Count) {
                    $model = $models[$parsed - 1]
                    Set-Setting "TRANSCRIBER_MODEL" $model | Out-Null
                } else {
                    Write-Host "Invalid choice, try again."
                }
            }
        } until ($model)
    }

    do {
        $topic = Read-Host "Client name / meeting topic (asked every session, never saved)"
        if (-not $topic.Trim()) { Write-Host "Client name / meeting topic cannot be empty." }
    } until ($topic.Trim())
    # Windows PowerShell 5.1 mangles embedded double quotes when passing arguments to native programs.
    $topic = $topic -replace '"', "'"

    Write-Host ""
    Write-Host "Available capture devices:"
    & $py -m transcribe --list-devices
    if ($LASTEXITCODE -ne 0) {
        # The most common real cause here is torch (a silero-vad dependency,
        # imported as soon as transcribe.py loads, --list-devices or not)
        # failing to load its native DLLs - visible above as an error
        # mentioning c10.dll. Stop now with the fix rather than continuing
        # through more questions only to hit the exact same crash later,
        # when actually starting the session.
        throw "Failed to list capture devices (see the error above). The most common cause on Windows is a missing VC++ Redistributable (x64) - see the link near the top of the Windows section in README.md, install it, then retry."
    }
    Write-Host ""
    $device = Read-Host "Device index from the list above (leave blank for auto-detected default)"

    $transcribeArgs = @("-m", "transcribe", "--model", $model, "--email=$email", "--topic=$topic")
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
        $url = Resolve-Setting "TRANSCRIBER_URL" "stream URL" "URL to POST each transcribed sentence to (must start with http:// or https://)"
        $transcribeArgs += @("--streaming", "--url", $url)
        $apiKey = Resolve-ApiKey
    } else {
        $upload = Read-Host "Upload the log file to a remote location when the session ends? [y/N]"
        if ($upload -match "^[Yy]") {
            do {
                $storeUrl = Read-Host "Store URL to POST the log file to (must start with http:// or https://)"
            } until ($storeUrl)
            $transcribeArgs += @("--store-url", $storeUrl)
            $apiKey = Resolve-ApiKey
        }
        $output = Read-Host "Transcript log filename [transcripts\transcript.log]"
        if (-not $output) { $output = "transcripts\transcript.log" }
        $transcribeArgs += @("--output", $output)
    }

    # The key goes to the app through the environment, not argv, so it never shows
    # up in the process list or in the "Starting:" line below.
    if ($apiKey) { $env:TRANSCRIBER_AUTH_HEADER = $apiKey }

    Write-Host ""
    Write-Host "Starting: $py $($transcribeArgs -join ' ')"
    Write-Host "Press Ctrl+C to stop (press it again to force-quit if shutdown stalls)."

    # Run in the foreground and let the app own Ctrl+C. Starting it with
    # Start-Process + Wait-Process and force-killing it in a finally block (as
    # this script used to) killed it while it was still shutting down, losing the
    # final transcript segment and the --store-url upload - and Start-Process
    # -ArgumentList doesn't quote arguments that contain spaces (URLs, log paths).
    & $py @transcribeArgs
    $exitCode = $LASTEXITCODE

    Write-Host ""
    if ($exitCode -eq 0) {
        Write-Host "Session ended."
    } else {
        Write-Host "transcribe.py exited with code $exitCode - see any messages above for the reason." -ForegroundColor Yellow
    }
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "The app is closing because of an error:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close this window"
exit $exitCode
