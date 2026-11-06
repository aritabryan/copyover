#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${ANDROID_HOME:?Set ANDROID_HOME to the Android SDK path}"
ndk_version="${NDK_VERSION:-27.1.12297006}"
host="linux-x86_64"
if [[ "$(uname -s)" == "Darwin" ]]; then host="darwin-x86_64"; fi
bin="$ANDROID_HOME/ndk/$ndk_version/toolchains/llvm/prebuilt/$host/bin"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$bin/aarch64-linux-android26-clang"
export CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER="$bin/x86_64-linux-android26-clang"
# ring (the TLS cryptography) includes C code: it's built with the NDK's clang and ar.
export CC_aarch64_linux_android="$bin/aarch64-linux-android26-clang"
export CC_x86_64_linux_android="$bin/x86_64-linux-android26-clang"
export AR_aarch64_linux_android="$bin/llvm-ar"
export AR_x86_64_linux_android="$bin/llvm-ar"
# This PC's paths (user, project folder) must not end up inside the .so.
export RUSTFLAGS="-C link-arg=-Wl,-z,max-page-size=16384 --remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo --remap-path-prefix=$PWD=/project"
for entry in aarch64-linux-android:arm64-v8a x86_64-linux-android:x86_64; do
    target="${entry%%:*}"
    abi="${entry##*:}"
    rustup target add "$target"
    cargo build --manifest-path native/Cargo.toml --release --locked --target "$target"
    mkdir -p "app/src/main/jniLibs/$abi"
    cp "native/target/$target/release/libpuente_core.so" "app/src/main/jniLibs/$abi/"
done
# License notices for the Rust crates inside libpuente_core.so, shown in the app.
# Generated once for both ABIs (they share the same crates); --fail stops on a license not in about.toml.
if ! command -v cargo-about >/dev/null 2>&1; then
    echo "cargo-about is needed to generate the license notices. Install it with: cargo install cargo-about --locked" >&2
    exit 1
fi
mkdir -p app/src/main/assets
cargo about generate --locked --offline --fail -m native/Cargo.toml -c native/about.toml native/about.hbs \
    -o app/src/main/assets/licenses-rust.txt
