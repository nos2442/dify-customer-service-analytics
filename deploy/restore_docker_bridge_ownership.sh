#!/bin/sh
# Restore ownership of this VM's four existing Docker bridges after NM takeover.
set -eu
bridges='docker0 br-57e45f0a12e9 br-695ec78b10ad br-9ed201ade18f'
config=/etc/NetworkManager/conf.d/99-course-docker-unmanaged.conf
expected='[keyfile]
unmanaged-devices=interface-name:docker0;interface-name:br-57e45f0a12e9;interface-name:br-695ec78b10ad;interface-name:br-9ed201ade18f'
for bridge in $bridges; do
    test -d "/sys/class/net/$bridge"
done
test "$(firewall-cmd --permanent --zone=docker --get-target)" = ACCEPT
if test -e "$config"; then
    test "$(cat "$config")" = "$expected" || {
        echo 'Existing NM config differs; preserved without change.' >&2
        exit 1
    }
else
    printf '%s\n' "$expected" > "$config"
    chmod 644 "$config"
fi
nmcli general reload
for bridge in $bridges; do
    nmcli device set "$bridge" managed no
    firewall-cmd --zone=docker --change-interface="$bridge"
done
systemctl reset-failed docker.service docker.socket
systemctl start docker.service
systemctl is-active docker.service
nmcli -t -f DEVICE,TYPE,STATE device status
firewall-cmd --get-active-zones
docker ps --format '{{.Names}}|{{.Status}}'
