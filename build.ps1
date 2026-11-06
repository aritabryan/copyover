# Builds dist\CopyOver\CopyOver.exe, dist\CopyOver-Setup-<version>.exe, dist\CopyOver-windows.zip and
# dist\latest.json.
# Usage: .\build.ps1   (from the repository folder, with Python 3.10 or newer and Inno Setup 6)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m pip install -q -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE) { throw 'Could not install the dependencies.' }

# The exe icon is the same one the tray draws.
python -c "import tray; tray.ensure_ico(force=True)"
if ($LASTEXITCODE) { throw 'Could not generate puente.ico.' }

python -m PyInstaller copyover.spec --noconfirm --clean
if ($LASTEXITCODE) { throw 'PyInstaller failed.' }

$version = (python -c "import version; print(version.VERSION)").Trim()
$repository = (python -c "import version; print(version.REPOSITORY)").Trim()
# The MIT notice travels with every copy: next to the exe, so it ends up in the zip and the installed folder.
Copy-Item LICENSE 'dist\CopyOver\LICENSE.txt' -Force
# The licenses of every library inside the exe, generated from what was actually packaged.
python tools\third_party_notices.py 'dist\CopyOver\THIRD-PARTY-NOTICES.txt'
if ($LASTEXITCODE) { throw 'Could not generate the third-party notices.' }
$zip = 'dist\CopyOver-windows.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path 'dist\CopyOver' -DestinationPath $zip

# Installer images: the logo on white for the header and on the panel's dark background for the side,
# at 100 % and 200 % so they stay sharp with scaling.
python -c @'
from pathlib import Path
from PIL import Image
import tray
d = Path('build/installer'); d.mkdir(parents=True, exist_ok=True)
for scale, suffix in ((1, ''), (2, '@2x')):
    side = 55 * scale
    logo = Image.new('RGB', (side, side), (255, 255, 255))
    mark = tray.draw_logo(size=side - 6 * scale)
    logo.paste(mark, (3 * scale, 3 * scale), mark)
    logo.save(d / f'logo{suffix}.bmp')
    # The same on the dark mode background of the installer (WizardBackColorDynamicDark).
    dark = Image.new('RGB', (side, side), (43, 43, 43))
    dark.paste(mark, (3 * scale, 3 * scale), mark)
    dark.save(d / f'logo-dark{suffix}.bmp')
    width, height = 164 * scale, 314 * scale
    mark = tray.draw_logo(size=96 * scale)
    # Same color as the installer pages, so the side image reaches the bottom without a visible edge.
    for background, name in (((255, 255, 255), 'side'), ((43, 43, 43), 'side-dark')):
        side_img = Image.new('RGB', (width, height), background)
        side_img.paste(mark, ((width - mark.width) // 2, height * 2 // 5 - mark.height // 2), mark)
        side_img.save(d / f'{name}{suffix}.bmp')
'@
if ($LASTEXITCODE) { throw 'Could not generate the installer images.' }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw 'Inno Setup 6 is missing (winget install JRSoftware.InnoSetup).' }
$sizeMB = [math]::Ceiling((Get-ChildItem 'dist\CopyOver' -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
& $iscc /Q "/DVersion=$version" "/DRepository=$repository" "/DSizeMB=$sizeMB" installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed.' }
$installer = "dist\CopyOver-Setup-$version.exe"

$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$installerSha = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLower()
[ordered]@{
    version = $version; tag = "v$version"
    zip = 'CopyOver-windows.zip'; sha256 = $sha
    instalador = "CopyOver-Setup-$version.exe"; sha256_instalador = $installerSha
} | ConvertTo-Json | Set-Content 'dist\latest.json' -Encoding utf8

Write-Host "Done: CopyOver $version"
Write-Host "  dist\CopyOver\CopyOver.exe"
Write-Host "  $installer ($([math]::Round((Get-Item $installer).Length / 1MB, 1)) MB)"
Write-Host "  $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
