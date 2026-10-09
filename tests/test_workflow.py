"""Exercise the complete orchestrator with simulated Windows tool responses.

These tests do not replace creating and booting a real Windows ISO.
"""

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from winslim.studio import Builder, BuildCancelled, BuildError


class SimulatedWindows(Builder):
    def __init__(self, cfg, fixture):
        self.messages = []
        super().__init__(cfg, self.messages.append)
        self.fixture = fixture
        self.active_index = None
        self.removed_apps = {}
        self.driver_added = {}
        self.exports = []
        self.commits = []
        self.discards = []
        self.cancel_event = threading.Event()
        self.cancel_on_mount = False
        self.change_source = False

    def ps(self, script, quiet=False):
        self.check_cancel()
        if "robocopy.exe" in script:
            shutil.copytree(self.fixture, self.isodir, dirs_exist_ok=True)
            if self.change_source:
                Path(self.c["iso"]).write_bytes(b"changed source")
        if "Remove-AppxProvisionedPackage" in script:
            self.removed_apps[self.active_index] = True
        return 0, []

    def run(self, cmd, ok=(0,), quiet=False):
        self.check_cancel()
        if "/Export-Image" in cmd:
            self.exports.append(
                int(next(a.split(":", 1)[1] for a in cmd if a.startswith("/SourceIndex:")))
            )
            target = Path(
                next(a.split(":", 1)[1] for a in cmd if a.startswith("/DestinationImageFile:"))
            )
            with target.open("ab") as stream:
                stream.write(b"exported image")
        if "/Mount-Image" in cmd:
            self.active_index = int(
                next(a.split(":", 1)[1] for a in cmd if a.startswith("/Index:"))
            )
            if self.cancel_on_mount:
                self.cancel_event.set()
        if "/Get-ProvisionedAppxPackages" in cmd:
            return 0, [] if self.removed_apps.get(self.active_index) else [
                "DisplayName : Microsoft.BingWeather",
                "PackageName : weather_1",
            ]
        if "/Get-Drivers" in cmd:
            return 0, [
                "Published Name : oem0.inf",
                "Provider Name : Fixture",
            ] if self.driver_added.get(self.active_index) else []
        if "/Add-Driver" in cmd:
            self.driver_added[self.active_index] = True
        if "/Commit" in cmd:
            self.commits.append(self.active_index)
        if "/Discard" in cmd:
            self.discards.append(self.active_index)
        if cmd[0] == self.osc:
            Path(cmd[-1]).write_bytes(b"rebuilt iso")
        return 0, []


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.iso"
        self.source.write_bytes(b"immutable source")
        self.output = self.root / "output.iso"
        self.output.write_bytes(b"old output")
        self.fixture = self.root / "fixture"
        for relative in (
            "sources/install.esd",
            "boot/etfsboot.com",
            "efi/microsoft/boot/efisys.bin",
        ):
            path = self.fixture / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        tool = self.root / "oscdimg.exe"
        tool.write_bytes(b"fake tool")
        driver = self.root / "driver.inf"
        driver.write_text("[Version]\n")
        self.cfg = {
            "iso": str(self.source),
            "out": str(self.output),
            "work": str(self.root / "work with spaces"),
            "oscdimg": str(tool),
            "indexes": [2, 5],
            "names": ["Windows 11 Pro", "Windows 11 Education"],
            "appx": ["Meteo"],
            "extra": {"drivers": [str(driver)]},
            "delete_work": True,
        }
        self.builder = SimulatedWindows(self.cfg, self.fixture)
        self.stack = []
        for mocked in (
            patch("winslim.base.ServicingLock", return_value=nullcontext()),
            patch("winslim.base.IS_WIN", True),
            patch("winslim.base.is_admin", return_value=True),
            patch(
                "winslim.base.shutil.disk_usage",
                return_value=shutil._ntuple_diskusage(200 * 1024**3, 0, 200 * 1024**3),
            ),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_multi_edition_full_build_and_report(self):
        self.builder.build()
        self.assertEqual(self.builder.exports, [2, 5])
        self.assertEqual(self.builder.commits, [1, 2])
        self.assertEqual(self.builder.mounted, [])
        self.assertFalse(Path(self.builder.root).exists())
        self.assertEqual(self.source.read_bytes(), b"immutable source")
        self.assertEqual(self.output.read_bytes(), b"rebuilt iso")
        report = json.loads(Path(str(self.output) + ".report.json").read_text())
        self.assertEqual([e["original_index"] for e in report["editions"]], [2, 5])
        self.assertEqual(report["iso"]["output_sha256"], hashlib.sha256(b"rebuilt iso").hexdigest())
        for edition in report["editions"]:
            self.assertEqual(edition["difference"]["appx"]["removed"], ["Microsoft.BingWeather"])
            self.assertEqual(edition["difference"]["drivers"]["added"], ["oem0.inf"])

    def test_cancel_immediately_after_mount_discards_it(self):
        self.builder.cancel_on_mount = True
        with self.assertRaises(BuildCancelled):
            self.builder.build()
        self.assertEqual(self.builder.discards, [1])
        self.assertEqual(self.builder.mounted, [])
        self.assertTrue(Path(self.builder.root).exists())
        self.assertEqual(self.output.read_bytes(), b"old output")

    def test_source_changed_during_copy_stops_build(self):
        self.builder.change_source = True
        with self.assertRaisesRegex(BuildError, "cambiata durante la copia"):
            self.builder.build()
        self.assertEqual(self.builder.exports, [])
        self.assertEqual(self.output.read_bytes(), b"old output")
