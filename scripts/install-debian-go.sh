#!/bin/bash -p
# Install the release-selected Debian Go toolchain.
#
# The signed trixie-backports archive is the first source, through apt. That
# suite keeps only its current upload, so a release may also pin the exact
# package files (name, size and SHA-256). With those pins the same bytes can
# come from snapshot.debian.org or from the release mirror after Debian has
# replaced the upload. Every file is checked against its pins before apt is
# given it, and nothing is installed unless both files pass.
set -euo pipefail
while builtin read -r _ _ _inherited_function; do
    builtin unset -f -- "$_inherited_function"
done < <(builtin declare -F)
builtin unset _inherited_function
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset APT_CONFIG BASH_ENV ENV CDPATH GLOBIGNORE CURL_HOME
fail() { echo "Debian Go: $*" >&2; exit 1; }
note() { echo "Debian Go: $*" >&2; }
version=${1:-}
package_version=${2:-}
[[ "$version" =~ ^go1\.27\.[0-9]+$ ]] || fail 'expected an exact go1.27 patch version'
[[ "$package_version" =~ ^1\.27\.[0-9]+-[0-9]+~bpo13\+[0-9]+$ ]] || fail 'expected a trixie-backports package version'
[[ "$package_version" == "${version#go}-"* ]] || fail 'package and compiler versions differ'
root=/usr/lib/go-1.27
packages=(golang-1.27-go golang-1.27-src)

# Exact package files. Either all six identity pins are set or none is; the two
# fallback locations are optional, and without either only apt is used.
go_file=${PLEBIAN_OS_KILIX_GO_DEB_AMD64_FILE:-}
go_bytes=${PLEBIAN_OS_KILIX_GO_DEB_AMD64_BYTES:-}
go_sha=${PLEBIAN_OS_KILIX_GO_DEB_AMD64_SHA256:-}
src_file=${PLEBIAN_OS_KILIX_GO_DEB_SRC_FILE:-}
src_bytes=${PLEBIAN_OS_KILIX_GO_DEB_SRC_BYTES:-}
src_sha=${PLEBIAN_OS_KILIX_GO_DEB_SRC_SHA256:-}
snapshot=${PLEBIAN_OS_KILIX_GO_DEB_SNAPSHOT:-}
mirror=${PLEBIAN_OS_KILIX_GO_DEB_MIRROR_URL:-}
pinned=0
if [ -n "$go_file$go_bytes$go_sha$src_file$src_bytes$src_sha$snapshot$mirror" ]; then
    for pin in "$go_file" "$go_bytes" "$go_sha" "$src_file" "$src_bytes" "$src_sha"; do
        [ -n "$pin" ] || fail 'incomplete package file pins: the compiler and source files each need a name, size and SHA-256'
    done
    [ "$go_file" = "golang-1.27-go_${package_version}_amd64.deb" ] \
        || fail "pinned compiler file $go_file is not the amd64 package $package_version"
    [ "$src_file" = "golang-1.27-src_${package_version}_all.deb" ] \
        || fail "pinned source file $src_file is not the package $package_version"
    for size in "$go_bytes" "$src_bytes"; do
        [[ "$size" =~ ^[1-9][0-9]{0,9}$ ]] || fail "invalid pinned package size: $size"
    done
    for digest in "$go_sha" "$src_sha"; do
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "invalid pinned package SHA-256: $digest"
    done
    [[ "$snapshot" =~ ^([0-9]{8}T[0-9]{6}Z)?$ ]] || fail "invalid snapshot.debian.org timestamp: $snapshot"
    [[ "$mirror" =~ ^(https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~%+-]+)*)?$ ]] \
        || fail "invalid package mirror URL: $mirror"
    pinned=1
fi

run_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo -- "$@"; fi
}
ready() {
    local package found verification
    for package in "${packages[@]}"; do
        found=$(dpkg-query -W -f='${db:Status-Status} ${Version}' "$package" 2>/dev/null) || return 1
        [ "$found" = "installed $package_version" ] || return 1
        verification=$(dpkg --verify "$package" 2>/dev/null) || return 1
        [ -z "$verification" ] || return 1
    done
    [ "$(GOENV=off GOROOT='' GOFLAGS='' GOTOOLCHAIN=local "$root/bin/go" version 2>/dev/null)" = "go version $version linux/$(dpkg --print-architecture)" ]
}

# check_file PATH BYTES SHA256 PACKAGE ARCH: the file is exactly the pinned one.
check_file() {
    local path=$1 bytes=$2 sha=$3 package=$4 arch=$5 actual name
    name=${path##*/}
    [ -f "$path" ] && [ ! -L "$path" ] || return 1
    actual=$(stat -c %s -- "$path") || return 1
    if [ "$actual" != "$bytes" ]; then
        note "refused $name from $source_name: $actual bytes, pinned $bytes"
        return 1
    fi
    actual=$(sha256sum -- "$path") || return 1
    actual=${actual%% *}
    if [ "$actual" != "$sha" ]; then
        note "refused $name from $source_name: SHA-256 $actual, pinned $sha"
        return 1
    fi
    # shellcheck disable=SC2016  # dpkg-deb expands these control fields itself.
    actual=$(dpkg-deb --showformat='${Package} ${Version} ${Architecture}' -W -- "$path" 2>/dev/null) || {
        note "refused $name from $source_name: not a readable Debian package"
        return 1
    }
    if [ "$actual" != "$package $package_version $arch" ]; then
        note "refused $name from $source_name: control fields '$actual'"
        return 1
    fi
}

# accept FILE: copy a downloaded file into the root-owned verified set and keep
# it only when that copy, the one apt will install, matches its pins.
accept() {
    local path=$1 name=${1##*/} copy ok=0
    copy=$apt_work/verified/$name
    [ -f "$path" ] && [ ! -L "$path" ] || return 0
    case "$name" in
        "$go_file") [ "$have_go" = 0 ] || return 0 ;;
        "$src_file") [ "$have_src" = 0 ] || return 0 ;;
        *) return 0 ;;
    esac
    run_root install -m 0644 -- "$path" "$copy"
    run_root rm -f -- "$path"
    if [ "$name" = "$go_file" ]; then
        check_file "$copy" "$go_bytes" "$go_sha" golang-1.27-go amd64 && ok=1 && have_go=1
    else
        check_file "$copy" "$src_bytes" "$src_sha" golang-1.27-src all && ok=1 && have_src=1
    fi
    if [ "$ok" = 1 ]; then
        note "verified $name from $source_name"
    else
        run_root rm -f -- "$copy"
    fi
}

# fetch URL DESTINATION BYTES: one bounded HTTPS download.
fetch() {
    local url=$1 dest=$2 bytes=$3
    rm -f -- "$dest"
    curl --proto =https --proto-redir =https --tlsv1.2 -fsSL \
        --connect-timeout 30 --speed-limit 1024 --speed-time 120 \
        --retry 2 --max-filesize "$bytes" -o "$dest" -- "$url"
}

if ! ready; then
    # An older installation may still use its install snapshot. Use private,
    # signed Debian indexes without changing its configured apt sources before
    # the surrounding release transaction commits.
    apt_work=$(mktemp -d /tmp/pleb-debian-go.XXXXXXXX)
    trap 'run_root rm -rf -- "$apt_work"' EXIT
    chmod 0755 "$apt_work"
    mkdir "$apt_work/lists"
    cat > "$apt_work/debian.sources" <<'SOURCES'
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie trixie-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SOURCES
    chmod 0644 "$apt_work/debian.sources"
    apt_options=(-o "Dir::Etc::sourcelist=$apt_work/debian.sources"
                 -o Dir::Etc::sourceparts=- -o "Dir::State::lists=$apt_work/lists"
                 -o APT::Get::AllowUnauthenticated=false
                 -o Acquire::AllowInsecureRepositories=false)
    arch=$(dpkg --print-architecture)
    if [ "$pinned" = 1 ] && [ "$arch" != amd64 ]; then
        note "the release pins amd64 package files only; using the live archive on $arch"
        pinned=0
    fi
    if [ "$pinned" = 0 ]; then
        run_root apt-get "${apt_options[@]}" update
        run_root env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
            -o APT::Get::AllowUnauthenticated=false \
            -o Acquire::AllowInsecureRepositories=false \
            install -y --no-install-recommends --reinstall --allow-downgrades -t trixie-backports \
            "golang-1.27-go=$package_version" "golang-1.27-src=$package_version"
    else
        mkdir "$apt_work/live" "$apt_work/snapshot" "$apt_work/mirror"
        run_root mkdir -m 0755 "$apt_work/verified"
        # apt downloads as its sandbox user when that user can write here.
        run_root chown _apt "$apt_work/live" 2>/dev/null || true
        have_go=0
        have_src=0

        # 1. The live signed archive, through apt.
        source_name='the live trixie-backports archive'
        if run_root apt-get "${apt_options[@]}" update \
                && (cd "$apt_work/live" && run_root apt-get "${apt_options[@]}" download \
                    "golang-1.27-go=$package_version" "golang-1.27-src=$package_version"); then
            for path in "$apt_work/live"/*.deb; do
                [ -e "$path" ] && accept "$path"
            done
        else
            note "$source_name does not offer $package_version; trying the pinned files"
        fi

        # 2. The same files from snapshot.debian.org.
        source_name="snapshot.debian.org $snapshot"
        if [ -n "$snapshot" ]; then
            for name in "$go_file" "$src_file"; do
                case "$name" in
                    "$go_file") [ "$have_go" = 0 ] || continue; bytes=$go_bytes ;;
                    *) [ "$have_src" = 0 ] || continue; bytes=$src_bytes ;;
                esac
                if fetch "https://snapshot.debian.org/archive/debian/$snapshot/pool/main/g/golang-1.27/${name//+/%2B}" \
                        "$apt_work/snapshot/$name" "$bytes"; then
                    accept "$apt_work/snapshot/$name"
                else
                    note "$source_name could not supply $name"
                fi
            done
        fi

        # 3. The release mirror. Asset names replace each character outside
        # [A-Za-z0-9._-] with '.', so they are valid release-asset names.
        source_name="the release mirror $mirror"
        if [ -n "$mirror" ]; then
            for name in "$go_file" "$src_file"; do
                case "$name" in
                    "$go_file") [ "$have_go" = 0 ] || continue; bytes=$go_bytes ;;
                    *) [ "$have_src" = 0 ] || continue; bytes=$src_bytes ;;
                esac
                if fetch "$mirror/${name//[^A-Za-z0-9._-]/.}" "$apt_work/mirror/$name" "$bytes"; then
                    accept "$apt_work/mirror/$name"
                else
                    note "$source_name could not supply $name"
                fi
            done
        fi

        if [ "$have_go" != 1 ] || [ "$have_src" != 1 ]; then
            fail "no source supplied verified files for $package_version (live archive, snapshot ${snapshot:-unset}, mirror ${mirror:-unset}); nothing was installed"
        fi
        # Both files are local and verified. Install them with no package
        # indexes at all: given an index that still lists the same version, apt
        # downloads that copy instead of using the verified file. On amd64 the
        # compiler depends only on the source package, so the pair is complete;
        # a dependency outside it makes apt refuse before dpkg changes anything.
        : > "$apt_work/local.sources"
        mkdir -p "$apt_work/local-lists/partial"
        run_root env DEBIAN_FRONTEND=noninteractive apt-get \
            -o "Dir::Etc::sourcelist=$apt_work/local.sources" -o Dir::Etc::sourceparts=- \
            -o "Dir::State::lists=$apt_work/local-lists" \
            -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= \
            -o APT::Get::AllowUnauthenticated=false \
            -o Acquire::AllowInsecureRepositories=false \
            install -y --no-install-recommends --reinstall --allow-downgrades \
            "$apt_work/verified/$go_file" "$apt_work/verified/$src_file"
    fi
    ready || fail 'installed packages or compiler do not match the selected release'
fi
# Replace only the links made by our archive installer or an earlier Debian
# selection. Keep the old archive tree so an older release can still use it.
for name in go gofmt; do
    link=/usr/local/bin/$name
    if [ -e "$link" ] || [ -L "$link" ]; then
        [ -L "$link" ] || fail "refusing to replace a custom command: $link"
        case "$(readlink "$link")" in
            /usr/local/go/bin/"$name"|"$root/bin/$name") ;;
            *) fail "refusing to replace an unrelated link: $link" ;;
        esac
    fi
done
run_root mkdir -p /usr/local/bin
for name in go gofmt; do
    run_root ln -sfnT "$root/bin/$name" "/usr/local/bin/$name"
done
printf 'Debian Go ready: %s (%s)\n' "$version" "$package_version"
