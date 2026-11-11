#!/bin/bash -p
# Install the release-selected Debian Go toolchain through authenticated apt.
set -euo pipefail
while builtin read -r _ _ _inherited_function; do
    builtin unset -f -- "$_inherited_function"
done < <(builtin declare -F)
builtin unset _inherited_function
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset APT_CONFIG BASH_ENV ENV CDPATH GLOBIGNORE
fail() { echo "Debian Go: $*" >&2; exit 1; }
version=${1:-}
package_version=${2:-}
[[ "$version" =~ ^go1\.27\.[0-9]+$ ]] || fail 'expected an exact go1.27 patch version'
[[ "$package_version" =~ ^1\.27\.[0-9]+-[0-9]+~bpo13\+[0-9]+$ ]] || fail 'expected a trixie-backports package version'
[[ "$package_version" == "${version#go}-"* ]] || fail 'package and compiler versions differ'
root=/usr/lib/go-1.27
packages=(golang-1.27-go golang-1.27-src)
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
    run_root apt-get "${apt_options[@]}" update
    run_root env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
        -o APT::Get::AllowUnauthenticated=false \
        -o Acquire::AllowInsecureRepositories=false \
        install -y --no-install-recommends --reinstall --allow-downgrades -t trixie-backports \
        "golang-1.27-go=$package_version" "golang-1.27-src=$package_version"
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
