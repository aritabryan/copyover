param(
    [string]$Apk = "$PSScriptRoot/app/build/outputs/apk/release/app-release.apk",
    [string]$Sdk = $env:ANDROID_HOME
)
$ErrorActionPreference = 'Stop'
if (-not $Sdk) { $Sdk = 'C:\Android' }
$apkPath = (Resolve-Path -LiteralPath $Apk).Path
$buildTools = Get-ChildItem -LiteralPath "$Sdk/build-tools" -Directory | Sort-Object Name -Descending | Select-Object -First 1
if (-not $buildTools) { throw 'Android Build Tools not found.' }
$certificates = & "$($buildTools.FullName)/apksigner.bat" verify --print-certs $apkPath
if ($LASTEXITCODE -ne 0) { throw 'The APK doesn''t have a valid signature.' }
# Phones reject an update signed with any other key, so stop it before it is published.
$releaseCertificate = 'a9a741b24fc6056ef7752f5ecaa45f29d57166ce308b69c161a1adb9c324441e'
if (-not ($certificates | Where-Object { $_ -match "certificate SHA-256 digest: $releaseCertificate$" })) {
    throw 'This APK is not signed with the CopyOver release key.'
}
$badging = & "$($buildTools.FullName)/aapt.exe" dump badging $apkPath
if ($LASTEXITCODE -ne 0) { throw 'Couldn''t read the APK.' }
$packageLine = $badging | Where-Object { $_ -match '^package:' } | Select-Object -First 1
if ($packageLine -notmatch "^package: name='dev.copyover.android' versionCode='(\d+)' versionName='([^']+)'") {
    throw 'The file isn''t a CopyOver APK.'
}
$versionCode = [int]$Matches[1]
$versionName = $Matches[2]
$output = Join-Path $PSScriptRoot 'app/build/update'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$target = Join-Path $output 'Puente.apk'
Copy-Item -LiteralPath $apkPath -Destination $target -Force
$manifest = [ordered]@{
    versionCode = $versionCode
    versionName = $versionName
    size = (Get-Item -LiteralPath $target).Length
    sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
}
$json = $manifest | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $output 'puente-update.json'), $json, [Text.UTF8Encoding]::new($false))
$cache = Join-Path (Split-Path $PSScriptRoot -Parent) '.android-updates'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$tempApk = Join-Path $cache ($manifest.sha256 + '.apk.tmp')
Copy-Item -LiteralPath $target -Destination $tempApk -Force
Move-Item -LiteralPath $tempApk -Destination (Join-Path $cache ($manifest.sha256 + '.apk')) -Force
$tempManifest = Join-Path $cache 'latest.json.tmp'
[IO.File]::WriteAllText($tempManifest, $json, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $tempManifest -Destination (Join-Path $cache 'latest.json') -Force
Write-Output "Version $versionName prepared in $output"
Write-Output 'Upload Puente.apk and puente-update.json to the release to make the update available.'
