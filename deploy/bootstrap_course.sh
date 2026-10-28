#!/bin/sh
# wolai chapters 1 and 3; run only after SSH and OpenEuler-Base verification.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
. /etc/os-release
[ "$ID" = openEuler ] && [ "$VERSION_ID" = 22.03 ] || exit 1
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p /etc/yum.repos.d/course-backup
if [ -e /etc/yum.repos.d/openEuler.repo ] && [ ! -e /etc/yum.repos.d/course-backup/openEuler.repo ]; then
    cp -p /etc/yum.repos.d/openEuler.repo /etc/yum.repos.d/course-backup/openEuler.repo
fi
install -m 0644 "$package_dir/course-openEuler.repo" /etc/yum.repos.d/openEuler.repo
dnf makecache
dnf install -y vim wget curl tar gzip unzip net-tools lsof git bash-completion \
    firewalld policycoreutils chrony open-vm-tools \
    mysql mysql-server 'dnf-command(config-manager)'

hostnamectl set-hostname node2
timedatectl set-timezone Asia/Shanghai
systemctl enable --now chronyd firewalld vmtoolsd mysqld
setenforce 0
sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config
mkdir -p /home/software /root/Desktop
chmod 755 /home/software

# Match the tutorial's CentOS 7 Docker CE repository for openEuler 22.03 SP4.
# The existing binary installer is retained as a fallback, never run over an existing daemon.
if ! command -v docker >/dev/null 2>&1; then
    dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
    sed -i 's/$releasever/7/g' /etc/yum.repos.d/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker
rpm -q mysql mysql-server open-vm-tools
docker --version
docker compose version
systemctl is-active sshd NetworkManager chronyd firewalld vmtoolsd mysqld docker
