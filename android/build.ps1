param(
    [string]$Sdk = $env:ANDROID_HOME,
    [string]$NdkVersion = '27.1.12297006',
    [switch]$NativeOnly,
    [switch]$Release
)
$ErrorActionPreference = 'Stop'
if (-not $Sdk) { $Sdk = $env:ANDROID_SDK_ROOT }
if (-not $Sdk) { throw 'Pass -Sdk with the Android SDK folder or set ANDROID_HOME.' }
$ndkBin = Join-Path $Sdk "ndk/$NdkVersion/toolchains/llvm/prebuilt/windows-x86_64/bin"
if (-not (Test-Path -LiteralPath $ndkBin)) { throw "NDK $NdkVersion is missing from $Sdk. Install it with SDK Manager." }
$env:ANDROID_HOME = $Sdk
$env:ANDROID_NDK_HOME = Join-Path $Sdk "ndk/$NdkVersion"
$project = $PSScriptRoot
$targets = @(
    @{ Rust = 'aarch64-linux-android'; Abi = 'arm64-v8a'; Compiler = 'aarch64-linux-android26-clang.cmd'; Env = 'CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER' },
    @{ Rust = 'x86_64-linux-android'; Abi = 'x86_64'; Compiler = 'x86_64-linux-android26-clang.cmd'; Env = 'CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER' }
)
foreach ($target in $targets) {
    & rustup target add $target.Rust
    if ($LASTEXITCODE -ne 0) { throw 'Couldn''t install the Rust target.' }
    [Environment]::SetEnvironmentVariable($target.Env, (Join-Path $ndkBin $target.Compiler), 'Process')
    # ring (the TLS cryptography) includes C code: it's built with the NDK's clang and ar.
    $suffix = $target.Rust -replace '-', '_'
    [Environment]::SetEnvironmentVariable("CC_$suffix", (Join-Path $ndkBin $target.Compiler), 'Process')
    [Environment]::SetEnvironmentVariable("AR_$suffix", (Join-Path $ndkBin 'llvm-ar.exe'), 'Process')
    # Android 15+ devices can use 16 KiB memory pages.
    # This PC's paths (user, project folder) must not end up inside the .so.
    $cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE '.cargo' }
    $env:RUSTFLAGS = "-C link-arg=-Wl,-z,max-page-size=16384 --remap-path-prefix=$cargoHome=/cargo --remap-path-prefix=$project=/project"
    & cargo build --manifest-path "$project/native/Cargo.toml" --release --locked --target $target.Rust
    if ($LASTEXITCODE -ne 0) { throw "The Rust build failed for $($target.Abi)." }
    $destination = Join-Path $project "app/src/main/jniLibs/$($target.Abi)"
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath "$project/native/target/$($target.Rust)/release/libpuente_core.so" -Destination $destination
}
# License notices for the Rust crates inside libpuente_core.so, shown in the app.
# Generated once for both ABIs (they share the same crates); --fail stops on a license not in about.toml.
if (-not (Get-Command cargo-about -ErrorAction SilentlyContinue)) {
    throw 'cargo-about is needed to generate the license notices. Install it with: cargo install cargo-about --locked'
}
$assets = Join-Path $project 'app/src/main/assets'
New-Item -ItemType Directory -Path $assets -Force | Out-Null
& cargo about generate --locked --offline --fail -m "$project/native/Cargo.toml" -c "$project/native/about.toml" "$project/native/about.hbs" -o (Join-Path $assets 'licenses-rust.txt')
if ($LASTEXITCODE -ne 0) { throw 'Generating the Rust license notices failed.' }
if (-not $NativeOnly) {
    & "$project/gradlew.bat" -p $project assembleDebug testDebugUnitTest lintDebug --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'The APK checks failed.' }
    Write-Output "APK: $project/app/build/outputs/apk/debug/app-debug.apk"
}
if ($Release -and -not $NativeOnly) {
    & "$project/gradlew.bat" -p $project assembleRelease lintRelease --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'The release build failed.' }
    Write-Output "APK: $project/app/build/outputs/apk/release/app-release.apk"
}
