# Persistent hidden spine watchdog — runs as a single long-lived process started
# at logon by launcher.vbs. Replaces the every-2-minute Task Scheduler repetition.
#
# Health check mirrors _watchdog_spine.py exactly: real HTTP POST to /mcp,
# any response (incl. 401) = alive, connection failure / timeout = down.
# Revival path: delegates to _relaunch_spine.py, which holds the production
# env config and the ACL-locked token — no credentials stored here.

$ROOT         = "C:\Users\Raide\code\claunker-hermes"
$PYTHON       = "$ROOT\.venv\Scripts\python.exe"
$RELAUNCH     = "$ROOT\logs\_relaunch_spine.py"
$URL          = "http://127.0.0.1:8848/mcp"
$LOG_DIR      = "$env:LOCALAPPDATA\claunker"
$LOG_FILE     = "$LOG_DIR\watchdog.log"
$CHECK_NAME   = "http_post_127.0.0.1:8848/mcp"
$SLEEP_SECS   = 120
$REQ_TIMEOUT  = 5000   # ms
$REVIVE_WAIT  = 10     # seconds between post-relaunch health re-checks
$REVIVE_TRIES = 12     # up to 120 s total wait for spine to come up

if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
}

function Write-Log($line) {
    $ts = [System.DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -LiteralPath $LOG_FILE -Value "[$ts] $line" -Encoding UTF8
}

function Test-SpineHealth {
    $body = [System.Text.Encoding]::UTF8.GetBytes(
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}'
    )
    try {
        $req = [System.Net.HttpWebRequest]::Create($URL)
        $req.Method      = "POST"
        $req.ContentType = "application/json"
        $req.Timeout     = $REQ_TIMEOUT
        $req.ContentLength = $body.Length
        $s = $req.GetRequestStream()
        $s.Write($body, 0, $body.Length)
        $s.Close()
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        # Any HTTP status code (e.g. 401 Unauthorized) means the process answered
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    } catch {
        return $false
    }
}

Write-Log "WATCHDOG_START $([System.DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"))"

while ($true) {
    if (-not (Test-SpineHealth)) {
        # Delegate to the proven trampoline — do not inline its env or token logic
        & $PYTHON $RELAUNCH 2>&1 | Out-Null

        $revived = $false
        for ($i = 0; $i -lt $REVIVE_TRIES; $i++) {
            Start-Sleep -Seconds $REVIVE_WAIT
            if (Test-SpineHealth) { $revived = $true; break }
        }

        if ($revived) {
            Write-Log "REVIVED $([System.DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")) check_failed=$CHECK_NAME"
        } else {
            Write-Log "REVIVE_FAILED $([System.DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")) check_failed=$CHECK_NAME spine still unresponsive after relaunch"
        }
    }

    Start-Sleep -Seconds $SLEEP_SECS
}
