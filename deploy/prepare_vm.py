"""Prepare a new, isolated openEuler VM. Does not power on or touch old VMs."""
import io
import json
import subprocess
from pathlib import Path

import pycdlib

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "vm" / "openEuler-dify"
ISO = ROOT / "openEuler-22.03-LTS-SP4-x86_64-dvd.iso"
VDISK = Path(r"C:\Program Files (x86)\VMware\VMware Workstation\vmware-vdiskmanager.exe")
SSH_KEYGEN = Path(r"C:\Windows\System32\OpenSSH\ssh-keygen.exe")


def main():
    # This directory must be new: no existing VM or disk is ever overwritten.
    VM.mkdir(parents=True, exist_ok=False)
    key = VM / "dify_ed25519"
    subprocess.run([str(SSH_KEYGEN), "-q", "-t", "ed25519", "-N", "", "-C",
                    "dify-course-local-admin", "-f", str(key)], check=True)
    public_key = key.with_suffix(".pub").read_text(encoding="ascii").strip()
    kickstart = """#version=DEVEL
cdrom
text
lang en_US.UTF-8
keyboard --vckeymap=us --xlayouts='us'
timezone Asia/Shanghai --utc
network --bootproto=dhcp --device=link --activate --hostname=dify-p2
rootpw --lock
user --name=dify --groups=wheel --lock
services --enabled=sshd,NetworkManager
firewall --enabled --service=ssh --port=80:tcp,443:tcp,3306:tcp,9997:tcp
selinux --enforcing
firstboot --disable
ignoredisk --only-use=sda
zerombr
clearpart --all --initlabel --drives=sda
bootloader --location=mbr --boot-drive=sda
part /boot --fstype=ext4 --size=1024 --ondisk=sda
part swap --size=8192 --ondisk=sda
part / --fstype=ext4 --size=10240 --grow --ondisk=sda
reboot --eject
%packages
@^minimal-environment
openssh-server
sudo
curl
python3
%end
%post --log=/root/dify-install-post.log
install -d -m 700 -o dify -g dify /home/dify/.ssh
cat > /home/dify/.ssh/authorized_keys <<'DIFY_PUBLIC_KEY'
PUBLIC_KEY_PLACEHOLDER
DIFY_PUBLIC_KEY
chown dify:dify /home/dify/.ssh/authorized_keys
chmod 600 /home/dify/.ssh/authorized_keys
echo 'dify ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/dify-course
chmod 440 /etc/sudoers.d/dify-course
restorecon -Rv /home/dify/.ssh
sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\\?UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
%end
""".replace("PUBLIC_KEY_PLACEHOLDER", public_key)
    ks_bytes = kickstart.encode("utf-8")
    (VM / "ks.cfg").write_bytes(ks_bytes)
    disk = pycdlib.PyCdlib()
    disk.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="OEMDRV")
    disk.add_fp(io.BytesIO(ks_bytes), len(ks_bytes), iso_path="/KS.CFG;1",
                rr_name="ks.cfg", joliet_path="/ks.cfg")
    disk.write(str(VM / "OEMDRV.iso"))
    disk.close()
    subprocess.run([str(VDISK), "-c", "-s", "100GB", "-a", "lsilogic", "-t", "0",
                    str(VM / "system.vmdk")], check=True)
    vmx = f'''.encoding = "UTF-8"
config.version = "8"
virtualHW.version = "19"
displayName = "openEuler-dify"
guestOS = "other5xlinux-64"
firmware = "bios"
numvcpus = "4"
cpuid.coresPerSocket = "4"
memsize = "6144"
scsi0.present = "TRUE"
scsi0.virtualDev = "lsilogic"
scsi0:0.present = "TRUE"
scsi0:0.fileName = "system.vmdk"
ide1:0.present = "TRUE"
ide1:0.deviceType = "cdrom-image"
ide1:0.fileName = "{ISO.as_posix()}"
ide1:0.startConnected = "TRUE"
ide1:1.present = "TRUE"
ide1:1.deviceType = "cdrom-image"
ide1:1.fileName = "OEMDRV.iso"
ide1:1.startConnected = "TRUE"
ethernet0.present = "TRUE"
ethernet0.connectionType = "nat"
ethernet0.virtualDev = "e1000"
ethernet0.addressType = "generated"
usb.present = "FALSE"
sound.present = "FALSE"
floppy0.present = "FALSE"
svga.autodetect = "TRUE"
serial0.present = "TRUE"
serial0.fileType = "file"
serial0.fileName = "install-serial.log"
bios.bootOrder = "hdd,cdrom"
tools.syncTime = "TRUE"
'''
    (VM / "openEuler-dify.vmx").write_text(vmx, encoding="utf-8")
    report = {"vmx": str(VM / "openEuler-dify.vmx"), "cpu": 4, "memory_mib": 6144,
              "disk_gib": 100, "swap_mib": 8192, "network": "VMware NAT (VMnet8)",
              "state": "prepared, not powered on", "ssh_user": "dify",
              "ssh_key_path": str(key), "credential_content_logged": False,
              "note": "课程参考32GB内存，本机资源不足，实际采用6GB，模型分时验证。"}
    (ROOT / "evidence" / "20261019" / "p01_vm_preparation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
