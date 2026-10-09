import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from winslim import base
from winslim.studio import (
    Builder,
    BuildCancelled,
    BuildError,
    inventory_diff,
    redact,
    service_changes,
    validate_extra,
    validate_inf_files,
    describe_error,
)


class BackendTests(unittest.TestCase):
    def test_missing_component_error_does_not_assume_json_import(self):
        _, hint = describe_error(
            BuildError(
                "Componente non presente nel catalogo di questa edizione (packages): Package"
            )
        )
        self.assertNotIn("JSON", hint)
        self.assertIn("componente selezionato", hint)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.iso"
        self.source.write_bytes(b"source iso")
        self.output = self.root / "output.iso"
        self.output.write_bytes(b"previous output")
        self.cfg = {
            "iso": str(self.source),
            "out": str(self.output),
            "work": str(self.root / "work"),
            "indexes": [3],
            "names": ["Windows 11 Pro"],
            "extra": {},
        }
        self.logs = []
        self.builder = Builder(self.cfg, self.logs.append)
        self.builder.cancel_event = threading.Event()
        self.builder.osc = "fake-oscdimg"
        image = Path(self.builder.isodir)
        (image / "boot").mkdir(parents=True)
        (image / "boot" / "etfsboot.com").write_bytes(b"boot")
        (image / "efi" / "microsoft" / "boot").mkdir(parents=True)
        (image / "efi" / "microsoft" / "boot" / "efisys.bin").write_bytes(b"efi")

    def test_command_success_progress_and_failure_exit_status(self):
        progress = []
        self.builder.on_unit_progress = progress.append
        rc, output = self.builder.run([sys.executable, "-c", "print('42.5%');print('done')"])
        self.assertEqual(rc, 0)
        self.assertEqual(output, ["42.5%", "done"])
        self.assertEqual(progress, [42.5])
        with self.assertRaisesRegex(BuildError, "codice 7"):
            self.builder.run([sys.executable, "-c", "import sys;print('broken');sys.exit(7)"])

    def test_cancel_does_not_launch_next_command(self):
        self.builder.cancel_event.set()
        with patch("winslim.studio.subprocess.Popen") as popen, self.assertRaises(BuildCancelled):
            self.builder.run(["must-not-launch"])
        popen.assert_not_called()

    def test_capability_failure_reports_full_identifier(self):
        class Process:
            stdout = io.StringIO("Error: 87\nA Windows capability name was not recognized.\n")

            def wait(self):
                return 87

        with patch("winslim.studio.subprocess.Popen", return_value=Process()):
            with self.assertRaisesRegex(BuildError, "CapabilityName:InvalidCapability"):
                self.builder.run(
                    [
                        "dism.exe",
                        "/English",
                        "/Image:test",
                        "/Remove-Capability",
                        "/CapabilityName:InvalidCapability",
                    ]
                )

    def test_adk_is_not_selected_from_oscdimg_directory(self):
        self.builder.osc = str(self.root / "ADK" / "amd64" / "Oscdimg" / "oscdimg.exe")
        adk = self.root / "ADK" / "amd64" / "DISM" / "dism.exe"
        adk.parent.mkdir(parents=True)
        adk.write_bytes(b"fixture")
        with patch.dict("os.environ", {"SystemRoot": str(self.root / "no-windows")}):
            with patch("winslim.base.shutil.which", return_value="system-dism.exe"):
                self.assertEqual(self.builder.find_dism(), "system-dism.exe")

    def test_windows_dism_is_selected_even_when_adk_is_on_path(self):
        system_root = self.root / "Windows"
        system = system_root / "System32" / "dism.exe"
        system.parent.mkdir(parents=True)
        system.write_bytes(b"fixture")
        with patch.dict("os.environ", {"SystemRoot": str(system_root)}):
            with patch("winslim.base.shutil.which", return_value="ADK-dism.exe"):
                self.assertEqual(self.builder.find_dism(), str(system))

    def test_mount_normalizes_dialog_paths_at_process_boundary(self):
        class Process:
            stdout = io.StringIO("")

            def wait(self):
                return 0

        self.builder.dism = "C:/Windows/System32/dism.exe"
        self.builder.dism_log = "C:/Users/user/Desktop/work folder/WinSlim_work/DISM.log"
        image = "C:/Users/user/Desktop/work folder/WinSlim_work/iso/sources/install.wim"
        mount = "C:/Users/user/Desktop/work folder/WinSlim_work/mount"
        with patch("winslim.studio.IS_WIN", True):
            with patch("winslim.studio.subprocess.Popen", return_value=Process()) as popen:
                base.Builder.mount(self.builder, image, 1, mount)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], r"C:\Windows\System32\dism.exe")
        self.assertIn(
            r"/ImageFile:C:\Users\user\Desktop\work folder\WinSlim_work\iso\sources\install.wim",
            command,
        )
        self.assertIn(r"/MountDir:C:\Users\user\Desktop\work folder\WinSlim_work\mount", command)
        self.assertIn(r"/LogPath:C:\Users\user\Desktop\work folder\WinSlim_work\DISM.log", command)
        self.assertEqual(self.builder.mounted, [mount])

    def test_failed_mount_captures_native_log_without_retry_or_registration(self):
        class Process:
            def __init__(self):
                self.stdout = io.StringIO(
                    "Error: 87\nAn error occurred while processing the command.\n"
                )

            def wait(self):
                return 87

        self.builder.dism_log = str(self.root / "DISM.log")
        for encoding in ("utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                Path(self.builder.dism_log).write_text(
                    "Failed to get the filename extension of the image file. hr:0x80070057\n",
                    encoding=encoding,
                )
                with patch("winslim.studio.subprocess.Popen", return_value=Process()) as popen:
                    with self.assertRaisesRegex(BuildError, "codice 87"):
                        base.Builder.mount(self.builder, "image.wim", 1, self.builder.mnt)
                popen.assert_called_once()
                self.assertEqual(self.builder.mounted, [])
                self.assertTrue(any("hr:0x80070057" in line for line in self.logs))
                self.logs.clear()

    def test_failed_iso_build_preserves_previous_output(self):
        def failing(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"partial")
            raise BuildError("oscdimg failed")

        self.builder.run = failing
        with self.assertRaises(BuildError):
            base.Builder.step_make_iso(self.builder)
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.assertFalse(list(self.root.glob(".winslim-*.iso")))

    def test_success_iso_is_hashed_then_atomically_replaced(self):
        def successful(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"new iso")
            return 0, []

        self.builder.run = successful
        base.Builder.step_make_iso(self.builder)
        self.assertEqual(self.output.read_bytes(), b"new iso")
        self.assertEqual(self.builder.output_sha256, hashlib.sha256(b"new iso").hexdigest())
        self.assertEqual(self.source.read_bytes(), b"source iso")

    def test_cancel_after_oscdimg_preserves_old_iso(self):
        def cancelled(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"new iso")
            self.builder.cancel_event.set()
            return 0, []

        self.builder.run = cancelled
        with self.assertRaises(BuildCancelled):
            base.Builder.step_make_iso(self.builder)
        self.assertEqual(self.output.read_bytes(), b"previous output")

    def test_cleanup_ignores_cancel_and_only_discards_owned_images(self):
        self.builder.mounted = [self.builder.mnt, self.builder.bmnt]
        self.builder.cancel_event.set()
        calls = []

        def run(cmd, **kwargs):
            self.assertTrue(self.builder._cleaning)
            calls.append(cmd)
            return 0, []

        self.builder.run = run
        self.builder.cleanup_on_error()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("/Discard" in cmd for cmd in calls))
        self.assertEqual(self.builder.mounted, [])
        self.assertFalse(self.builder._cleaning)

    def test_cleanup_failure_retains_owned_mount_for_recovery(self):
        self.builder.mounted = [self.builder.mnt]
        self.builder.run = lambda *a, **kw: (_ for _ in ()).throw(BuildError("busy"))
        self.builder.cleanup_on_error()
        self.assertEqual(self.builder.mounted, [self.builder.mnt])
        self.assertTrue(any("conserva" in line for line in self.logs))

    def test_cleanup_retries_readonly_file_without_touching_source(self):
        root = Path(self.builder.root)
        (root / "owner.json").write_text(json.dumps({"run_id": self.builder.run_id}))
        readonly = root / "readonly.txt"
        readonly.write_text("temporary")
        readonly.chmod(0o444)

        def simulate_windows_remove(folder, *, onexc):
            onexc(os.unlink, str(readonly), PermissionError("readonly"))

        with patch("winslim.base.shutil.rmtree", side_effect=simulate_windows_remove):
            self.builder.cleanup_work()
        self.assertFalse(readonly.exists())
        self.assertEqual(self.source.read_bytes(), b"source iso")
        self.assertEqual(self.output.read_bytes(), b"previous output")

    def test_preflight_does_not_delete_previous_work(self):
        marker = Path(self.builder.root) / "user-data.txt"
        marker.write_text("preserve me")
        with (
            patch("winslim.base.IS_WIN", True),
            patch("winslim.base.is_admin", return_value=True),
            self.assertRaisesRegex(BuildError, "precedente"),
        ):
            self.builder.preflight()
        self.assertEqual(marker.read_text(), "preserve me")

    def test_service_override_is_checked_before_write(self):
        self.builder.extra = {"services": {"WSearch": 3}}
        scripts = []
        self.builder.load_hives = lambda *a: None
        self.builder.unload_hives = lambda: None
        self.builder.ps = scripts.append
        service_changes(self.builder)
        self.assertLess(scripts[0].index("StartOverride"), scripts[0].index("Set-ItemProperty"))

    def test_protected_services_and_unsigned_flag_not_allowed(self):
        with self.assertRaises(ValueError):
            validate_extra({"services": {"RpcSs": 4}})
        self.assertNotIn("ForceUnsigned", self.cfg["extra"])

    def test_inventory_records_actual_removals_and_state_changes(self):
        before = {
            "features": [
                "Feature Name : Recall",
                "State : Enabled",
                "Feature Name : TelnetClient",
                "State : Disabled",
            ]
        }
        after = {
            "features": [
                "Feature Name : Recall",
                "State : Disabled",
                "Feature Name : NewFeature",
                "State : Enabled",
            ]
        }
        diff = inventory_diff(before, after)["features"]
        self.assertEqual(diff["removed"], ["TelnetClient"])
        self.assertEqual(diff["added"], ["NewFeature"])
        self.assertEqual(diff["changed"]["Recall"]["after"], {"State": "Disabled"})

    def test_report_excludes_password_and_does_not_mutate_input(self):
        cfg = {
            "pwd": "secret",
            "password": "secret",
            "variables": {"v_pwd": "secret", "v_user": "Utente"},
        }
        cleaned = redact(cfg)
        self.assertNotIn("secret", json.dumps(cleaned))
        self.assertEqual(cfg["pwd"], "secret")

    def test_driver_missing_catalog_or_payload_detected(self):
        driver = self.root / "driver.inf"
        driver.write_text("[Version]\nCatalogFile=driver.cat\n[SourceDisksFiles]\ndriver.sys=1\n")
        with self.assertRaisesRegex(ValueError, "Catalogo"):
            validate_inf_files(str(driver))
        (self.root / "driver.cat").write_bytes(b"cat")
        with self.assertRaisesRegex(ValueError, "driver.sys"):
            validate_inf_files(str(driver))
        (self.root / "driver.sys").write_bytes(b"sys")
        self.assertTrue(validate_inf_files(str(driver)))
