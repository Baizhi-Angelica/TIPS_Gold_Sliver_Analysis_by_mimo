$dir = Join-Path $env:USERPROFILE ".codex"
$path = Join-Path $dir ".env"

New-Item -ItemType Directory -Force -Path $dir | Out-Null

$updates = [ordered]@{
    HTTP_PROXY = '"http://127.0.0.1:7890"'
    HTTPS_PROXY = '"http://127.0.0.1:7890"'
    ALL_PROXY = '"socks5h://127.0.0.1:7890"'
    NO_PROXY = '"localhost,127.0.0.1,::1"'
}

$lines = if (Test-Path -LiteralPath $path) {
    [System.Collections.Generic.List[string]](Get-Content -LiteralPath $path)
}
else {
    [System.Collections.Generic.List[string]]::new()
}

foreach ($key in $updates.Keys) {
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*='
    $found = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "$key=$($updates[$key])"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $lines.Add("$key=$($updates[$key])")
    }
}

Set-Content -LiteralPath $path -Value $lines -Encoding UTF8

Write-Host "Updated: $path"
Write-Host ""
Get-Content -LiteralPath $path
Write-Host ""
Write-Host "Done. Restart Codex Desktop completely, then open it again."
Read-Host "Press Enter to close"
