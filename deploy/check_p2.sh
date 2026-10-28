#!/bin/sh
# Read-only P2 inventory. All findings go to stdout; no privilege escalation.

check() {
    title=$1
    shift
    printf '\n=== %s ===\n' "$title"
    if ! command -v "$1" >/dev/null 2>&1; then
        printf '[MISSING] %s\n' "$1"
        return
    fi
    if "$@" 2>&1; then
        :
    else
        status=$?
        printf '[UNAVAILABLE] exit=%s; check command, permissions or service state.\n' "$status"
    fi
}

printf 'P2 read-only inventory; missing/failed checks are not installation requests.\n'
check 'Recorded at' date -Iseconds
check 'Host' hostname
check 'Operating system' cat /etc/os-release
check 'Architecture' uname -m
check 'CPU' lscpu
check 'Memory' free -h
check 'Disk' df -h
check 'IP addresses' ip -brief address
check 'Listening TCP/UDP ports' ss -lntu
check 'Docker client and server versions' docker version
check 'Docker Compose version' docker compose version
check 'Existing containers' docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
printf '\nInventory complete. Review MISSING/UNAVAILABLE items; completion does not mean CP1/CP2 passed.\n'
exit 0
