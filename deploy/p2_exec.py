"""Run a command or upload a file to the new project VM using its SSH key."""
import argparse
import ipaddress
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "vm" / "openEuler-dify"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("command", nargs="?", default="true")
    parser.add_argument("--upload", nargs=2, metavar=("LOCAL", "REMOTE"))
    parser.add_argument("--stdin", type=Path, help="Send a local file to the command without logging its contents")
    args = parser.parse_args()
    if not any(ipaddress.ip_address(args.host) in ipaddress.ip_network(subnet)
               for subnet in ("192.168.247.0/24", "192.168.18.0/24", "192.168.110.0/24")):
        parser.error("Only this project's installation NAT or bridged LAN subnet is allowed")
    known_hosts = VM / "known_hosts"
    client = paramiko.SSHClient()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    # First use follows vmrun identification or a fresh DHCP lease matching this VM's MAC.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(args.host, username="dify", key_filename=str(VM / "dify_ed25519"),
                       look_for_keys=False, allow_agent=False, timeout=15,
                       auth_timeout=20, banner_timeout=20)
        client.save_host_keys(str(known_hosts))
        if args.upload:
            with client.open_sftp() as sftp:
                sftp.put(args.upload[0], args.upload[1])
        channel = client.get_transport().open_session()
        channel.set_combine_stderr(True)
        channel.exec_command(args.command)
        if args.stdin:
            channel.sendall(args.stdin.read_bytes())
            channel.shutdown_write()
        with channel.makefile("r") as output:
            for line in output:
                print(line.rstrip("\n"), flush=True)
        return channel.recv_exit_status()
    finally:
        client.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
