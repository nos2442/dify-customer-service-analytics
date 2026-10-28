#!/bin/sh
# Course VM only; packages are offline, dnf dependencies use existing OS repos.
# https://docs.docker.com/engine/install/binaries/
# https://docs.docker.com/compose/install/linux/
# https://github.com/moby/moby/blob/v27.5.1/contrib/init/systemd/docker.service
# Parse-only check: sh -n deploy/install_docker.sh
set -eu

PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export PATH
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo 'Linux x86_64 is required.' >&2; exit 1; }
if command -v docker >/dev/null 2>&1; then
    docker --version
    echo 'Existing Docker preserved; installation skipped.'
    exit 0
fi
if command -v dockerd >/dev/null 2>&1 || [ -e /etc/systemd/system/docker.service ] || [ -e /usr/lib/systemd/system/docker.service ]; then
    echo 'Existing daemon or service found; review it before installing.' >&2
    exit 1
fi

package_dir=${1:-"$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/installers"}
archive=$package_dir/docker-27.5.1.tgz
compose=$package_dir/docker-compose-linux-x86_64-v2.39.1
# Docker hash checks transfer integrity; Compose hash is from official checksums.txt.
printf '%s  %s\n' \
    4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640 "$archive" \
    a5ea28722d5da628b59226626f7d6c33c89a7ed19e39f750645925242044c9d2 "$compose" | sha256sum -c -
dnf -y install iptables tar xz gzip procps-ng
tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' 0
trap 'exit 1' HUP INT TERM
tar -xzf "$archive" -C "$tmpdir"
install -d -m 0755 /usr/local/bin /usr/local/lib/docker/cli-plugins
install -m 0755 "$tmpdir"/docker/* /usr/local/bin/
install -m 0755 "$compose" /usr/local/lib/docker/cli-plugins/docker-compose
getent group docker >/dev/null || groupadd --system docker
cat > /etc/systemd/system/docker.service <<'UNIT'
[Unit]
Description=Docker Application Container Engine
After=network-online.target firewalld.service
Wants=network-online.target

[Service]
Type=notify
Environment=PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/local/bin/dockerd
Restart=on-failure
RestartSec=2
Delegate=yes
KillMode=process
TasksMax=infinity
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now docker.service
docker version
docker compose version
docker info --format 'StorageDriver={{.Driver}}'
