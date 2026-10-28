"""Check destructive installer operations stay within the new isolated VM."""
import io
import unittest
from pathlib import Path

import pycdlib

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "vm" / "openEuler-dify"


class NewVMInstallConfigTests(unittest.TestCase):
    def test_only_new_single_disk_and_known_installation_media(self):
        vmx = (VM / "openEuler-dify.vmx").read_text(encoding="utf-8")
        disks = [line.strip() for line in vmx.splitlines() if ".vmdk" in line]
        self.assertEqual(len(disks), 1)
        self.assertRegex(disks[0], r'^scsi0:0.fileName = "system(?:-\d{6})?\.vmdk"$')
        self.assertNotIn("HADOOP", vmx)
        self.assertIn('ethernet0.connectionType = "bridged"', vmx)
        self.assertIn('bios.bootOrder = "hdd,cdrom"', vmx)
        self.assertIn('ide1:1.fileName = "OEMDRV.iso"', vmx)

    def test_answer_iso_matches_disk_scoped_configuration_and_public_key(self):
        image = pycdlib.PyCdlib()
        image.open(str(VM / "OEMDRV.iso"))
        stream = io.BytesIO()
        image.get_file_from_iso_fp(stream, iso_path="/KS.CFG;1")
        image.close()
        config = stream.getvalue().decode("utf-8")
        self.assertEqual(config, (VM / "ks.cfg").read_text(encoding="utf-8"))
        self.assertIn("ignoredisk --only-use=sda", config)
        self.assertIn("clearpart --all --initlabel --drives=sda", config)
        self.assertIn("part swap --size=8192 --ondisk=sda", config)
        self.assertIn((VM / "dify_ed25519.pub").read_text().strip(), config)
        self.assertIn("rootpw --lock", config)
        self.assertNotIn("--plaintext", config)


if __name__ == "__main__":
    unittest.main()
