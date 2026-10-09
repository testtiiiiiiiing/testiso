"""Execute generated PowerShell with simulated Windows servicing commands."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from winslim.studio import psq, scan_script


POWERSHELL = (
    os.environ.get("WINSLIM_PWSH") or shutil.which("pwsh") or shutil.which("powershell.exe")
)


@unittest.skipUnless(POWERSHELL, "PowerShell unavailable")
class ScanTests(unittest.TestCase):
    def execute_scan(self, kind="wim", attached=False, fail=False, cancel=False):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            out = folder / "inventory.json"
            marker = folder / "cancel"
            if cancel:
                marker.touch()
            script = folder / "scan.ps1"
            wim = str(folder / "copy.wim")
            script.write_text(
                scan_script(
                    "C:\\source's file.iso", 5, wim, str(folder / "mount"), str(out), str(marker)
                ),
                encoding="utf-8-sig",
            )
            state = folder / "state.json"
            harness = folder / "harness.ps1"
            harness.write_text(
                "$global:calls=[System.Collections.Generic.List[object]]::new()\n"
                + "$global:kind="
                + psq(kind)
                + "\n"
                + "$global:attached=$"
                + str(attached).lower()
                + "\n"
                + "$global:fail=$"
                + str(fail).lower()
                + "\n"
                + r"""
function Test-Path {
 param($Path)
 if($Path -eq 'X:\sources\install.wim'){return $global:kind -eq 'wim'}
 if($Path -eq 'X:\sources\install.esd'){return $global:kind -eq 'esd'}
 Microsoft.PowerShell.Management\Test-Path -Path $Path
}
function Get-DiskImage { param($ImagePath) [pscustomobject]@{Attached=$global:attached} }
function Mount-DiskImage {
 param($ImagePath,[switch]$PassThru)
 $global:calls.Add(@{name='attach';image=$ImagePath})
 [pscustomobject]@{Attached=$true}
}
function Get-Volume { [pscustomobject]@{DriveLetter='X'} }
function Export-WindowsImage {
 param($SourceImagePath,$SourceIndex,$DestinationImagePath,$CompressionType)
 $global:calls.Add(@{name='export';index=$SourceIndex;image=$SourceImagePath;destination=$DestinationImagePath})
}
function Mount-WindowsImage {
 param($ImagePath,$Index,$Path,[switch]$ReadOnly,[switch]$Optimize)
 $global:calls.Add(@{name='mount';image=$ImagePath;index=$Index;readonly=[bool]$ReadOnly;optimize=[bool]$Optimize;path=$Path})
}
function Get-WindowsOptionalFeature {
 param($Path)
 if($global:fail){throw 'Simulated inventory failure'}
 [pscustomobject]@{FeatureName='Feature';State='Enabled'}
}
function Get-WindowsCapability {
 param($Path)
 [pscustomobject]@{Name='MathRecognizer~~~~0.0.1.0';State='Installed'}
}
function Get-WindowsPackage {
 param($Path)
 [pscustomobject]@{PackageName='Package';PackageState='Installed'}
}
function Dismount-WindowsImage {
 param($Path,[switch]$Discard)
 $global:calls.Add(@{name='unmount';path=$Path;discard=[bool]$Discard})
}
function Dismount-DiskImage {
 param($ImagePath)
 $global:calls.Add(@{name='detach';image=$ImagePath})
}
$failure=$null
try { & """
                + psq(str(script))
                + " } catch { $failure=$_.Exception.Message }\n"
                + "@{calls=@($global:calls.ToArray());failure=$failure} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 "
                + psq(str(state)),
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(state.read_text(encoding="utf-8-sig"))
            data["inventory"] = (
                json.loads(out.read_text(encoding="utf-8-sig")) if out.exists() else None
            )
            data["temporary_wim"] = wim
            return data

    def test_wim_is_mounted_directly_at_selected_index_readonly(self):
        data = self.execute_scan()
        self.assertIsNone(data["failure"])
        self.assertEqual(
            [c["name"] for c in data["calls"]], ["attach", "mount", "unmount", "detach"]
        )
        mount = data["calls"][1]
        self.assertEqual(mount["image"], r"X:\sources\install.wim")
        self.assertEqual(mount["index"], 5)
        self.assertTrue(mount["readonly"])
        self.assertTrue(mount["optimize"])
        self.assertTrue(data["calls"][2]["discard"])
        self.assertEqual(data["inventory"]["features"][0]["Name"], "Feature")

    def test_esd_is_converted_then_mounted_at_exported_index(self):
        data = self.execute_scan(kind="esd")
        self.assertIsNone(data["failure"])
        self.assertEqual(
            [c["name"] for c in data["calls"]], ["attach", "export", "mount", "unmount", "detach"]
        )
        self.assertEqual(data["calls"][1]["index"], 5)
        self.assertEqual(data["calls"][2]["index"], 1)
        self.assertEqual(data["calls"][2]["image"], data["temporary_wim"])
        self.assertTrue(data["calls"][2]["readonly"])

    def test_inventory_failure_discards_mount_and_preserves_existing_iso_attachment(self):
        data = self.execute_scan(attached=True, fail=True)
        self.assertEqual(data["failure"], "Simulated inventory failure")
        self.assertEqual([c["name"] for c in data["calls"]], ["mount", "unmount"])
        self.assertTrue(data["calls"][1]["discard"])
        self.assertIsNone(data["inventory"])

    def test_cancel_before_servicing_detaches_only_owned_iso(self):
        data = self.execute_scan(cancel=True)
        self.assertEqual(data["failure"], "Scansione annullata")
        self.assertEqual([c["name"] for c in data["calls"]], ["attach", "detach"])
        self.assertIsNone(data["inventory"])
