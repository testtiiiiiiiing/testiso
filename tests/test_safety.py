import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from winslim.base import BuildError, make_unattend, rewrite_reg, safe_dest
from winslim.safety import (
    atomic_json,
    fingerprint,
    sha256_file,
    validate_build_paths,
    validate_config,
    resolve_components,
)


class ComponentResolutionTests(unittest.TestCase):
    def catalog(self, **states):
        return {
            "capabilities": [
                line
                for name, state in states.items()
                for line in ["Capability Identity : " + name, "State : " + state]
            ]
        }

    def test_bare_name_resolves_to_full_installed_identity(self):
        log = []
        names, decisions = resolve_components(
            {"capabilities": ["MathRecognizer"]},
            self.catalog(**{"MathRecognizer~~~~0.0.2.0": "Installed"}),
            log.append,
        )
        self.assertEqual(names["capabilities"], ["MathRecognizer~~~~0.0.2.0"])
        self.assertEqual(decisions[0]["requested"], "MathRecognizer")
        self.assertTrue(log)

    def test_old_version_resolves_without_changing_language(self):
        names, _ = resolve_components(
            {"capabilities": ["Language.Basic~~~it-IT~0.0.1.0"]},
            self.catalog(
                **{
                    "Language.Basic~~~it-IT~0.0.2.0": "Installed",
                    "Language.Basic~~~en-US~0.0.2.0": "Installed",
                }
            ),
            lambda line: None,
        )
        self.assertEqual(names["capabilities"], ["Language.Basic~~~it-IT~0.0.2.0"])

    def test_ambiguous_language_prefix_is_rejected(self):
        with self.assertRaisesRegex(BuildError, "ambiguo"):
            resolve_components(
                {"capabilities": ["Language.Basic"]},
                self.catalog(
                    **{
                        "Language.Basic~~~it-IT~0.0.1.0": "Installed",
                        "Language.Basic~~~en-US~0.0.1.0": "Installed",
                    }
                ),
                lambda line: None,
            )

    def test_known_standard_missing_and_not_present_are_reported(self):
        log = []
        names, decisions = resolve_components(
            {"capabilities": ["Microsoft.Windows.WordPad~~~~0.0.1.0", "MathRecognizer"]},
            self.catalog(**{"MathRecognizer~~~~0.0.1.0": "Not Present"}),
            log.append,
            ["Microsoft.Windows.WordPad"],
        )
        self.assertEqual(names["capabilities"], [])
        self.assertEqual([d["status"] for d in decisions], ["unavailable", "already_absent"])
        self.assertEqual(len(log), 2)

    def test_unknown_name_rejected_not_silently_ignored(self):
        with self.assertRaisesRegex(BuildError, "BogusCapability"):
            resolve_components(
                {"capabilities": ["BogusCapability"]},
                self.catalog(**{"MathRecognizer~~~~0.0.1.0": "Installed"}),
                lambda line: None,
            )

    def test_same_component_is_not_removed_twice(self):
        names, _ = resolve_components(
            {"capabilities": ["MathRecognizer", "MathRecognizer~~~~0.0.1.0"]},
            self.catalog(**{"MathRecognizer~~~~0.0.1.0": "Installed"}),
            lambda line: None,
        )
        self.assertEqual(names["capabilities"], ["MathRecognizer~~~~0.0.1.0"])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_destinations_confined(self):
        for dest in (
            "../escape",
            r"a\..\escape",
            r"C:\Windows",
            r"\Windows",
            "//server/share",
            "file:stream",
            "a\x00b",
            "AUX.txt",
            r"a\.. \escape",
            "folder.",
        ):
            with self.subTest(dest=dest), self.assertRaises(BuildError):
                safe_dest(self.root, dest)
        self.assertEqual(
            safe_dest(self.root, r"ProgramData\Extras"), str(self.root / "ProgramData" / "Extras")
        )
        self.assertEqual(safe_dest(self.root, ""), str(self.root))

    def test_symlink_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            try:
                (self.root / "link").symlink_to(elsewhere, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation unavailable")
            with self.assertRaises(BuildError):
                safe_dest(self.root, "link/file")

    def test_source_cannot_be_output_even_via_hardlink(self):
        source = self.root / "source.iso"
        source.write_bytes(b"source")
        target = self.root / "output.iso"
        os.link(source, target)
        with self.assertRaises(BuildError):
            validate_build_paths({"iso": str(source), "out": str(target)}, str(self.root / "work"))

    def test_work_cannot_contain_source_or_output(self):
        work = self.root / "work"
        work.mkdir()
        for source, out in [
            (work / "in.iso", self.root / "out.iso"),
            (self.root / "in.iso", work / "out.iso"),
        ]:
            with self.subTest(source=source), self.assertRaises(BuildError):
                validate_build_paths({"iso": str(source), "out": str(out)}, str(work))

    def test_copy_source_cannot_contain_work(self):
        with self.assertRaises(BuildError):
            validate_build_paths(
                {
                    "iso": str(self.root / "in.iso"),
                    "out": str(self.root / "out.iso"),
                    "files": [{"src": str(self.root), "dest": "Extra"}],
                },
                str(self.root / "work"),
            )

    def test_registry_rewrites_only_headers_including_delete(self):
        text = 'Windows Registry Editor Version 5.00\n[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\Test]\n"Path"="HKEY_CURRENT_USER\\Original"\n[-HKEY_CURRENT_USER\\Software\\Test]\n[HKEY_CLASSES_ROOT\\.test]\n'
        rewritten = rewrite_reg(text, "ControlSet002")
        self.assertIn("[HKEY_LOCAL_MACHINE\\WS_SYSTEM\\ControlSet002\\Services\\Test]", rewritten)
        self.assertIn('"Path"="HKEY_CURRENT_USER\\Original"', rewritten)
        self.assertIn("[-HKEY_LOCAL_MACHINE\\WS_NTUSER\\Software\\Test]", rewritten)
        self.assertIn("[HKEY_LOCAL_MACHINE\\WS_SOFTWARE\\Classes\\.test]", rewritten)

    def test_registry_rejects_live_roots_and_malformed_headers(self):
        for text in (
            "[HKEY_USERS\\S-1-5-18]",
            "[HKEY_LOCAL_MACHINE\\SAM]",
            "[HKEY_CURRENT_USER",
            "no keys",
        ):
            with self.subTest(text=text), self.assertRaises(BuildError):
                rewrite_reg(text)

    def test_atomic_save_failure_preserves_existing_file(self):
        path = self.root / "config.json"
        path.write_text('{"before":true}')
        with self.assertRaises(TypeError):
            atomic_json(path, {"invalid": object()})
        self.assertEqual(json.loads(path.read_text()), {"before": True})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_atomic_replace_failure_preserves_existing_file(self):
        path = self.root / "config.json"
        path.write_text("old")
        with (
            patch("winslim.safety.os.replace", side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            atomic_json(path, {"new": True})
        self.assertEqual(path.read_text(), "old")
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_atomic_save_unicode_and_repeatability(self):
        path = self.root / "config.json"
        for number in (1, 2):
            atomic_json(path, {"nome": "edizione italiana è", "n": number})
            self.assertEqual(
                json.loads(path.read_text()), {"nome": "edizione italiana è", "n": number}
            )

    def test_sha256_and_cancellation(self):
        path = self.root / "input.iso"
        path.write_bytes(b"iso content" * 1000)
        self.assertEqual(sha256_file(path), hashlib.sha256(path.read_bytes()).hexdigest())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(BuildError):
            sha256_file(path, cancel)

    def test_sha256_rejects_file_changed_while_reading(self):
        path = self.root / "input.iso"
        path.write_bytes(b"original")
        first = fingerprint(path)
        with (
            patch("winslim.safety.fingerprint", side_effect=[first, (*first[:1], 999, *first[2:])]),
            self.assertRaises(BuildError),
        ):
            sha256_file(path)

    def test_unattended_xml_escapes_user_and_password(self):
        options = {
            "lang": "it-IT",
            "kbd": "it-IT",
            "tz": "W. Europe Standard Time",
            "user": "A&B",
            "pwd": '<secret>"&',
            "index": 2,
            "autologon": True,
            "wipe": False,
        }
        xml = make_unattend(options)
        root = ET.fromstring(xml)
        ns = {"u": "urn:schemas-microsoft-com:unattend"}
        self.assertEqual(root.find(".//u:LocalAccount/u:Name", ns).text, "A&B")
        self.assertEqual(root.find(".//u:LocalAccount/u:Password/u:Value", ns).text, '<secret>"&')
        self.assertEqual(root.find(".//u:MetaData/u:Value", ns).text, "2")
        self.assertIsNone(root.find(".//u:WillWipeDisk", ns))

    def test_unattended_wipe_requires_explicit_option(self):
        options = {
            "lang": "it-IT",
            "kbd": "it-IT",
            "tz": "UTC",
            "user": "User",
            "index": 1,
            "wipe": True,
        }
        root = ET.fromstring(make_unattend(options))
        ns = {"u": "urn:schemas-microsoft-com:unattend"}
        self.assertEqual(root.find(".//u:WillWipeDisk", ns).text, "true")
        self.assertEqual(root.find(".//u:InstallTo/u:PartitionID", ns).text, "3")
        self.assertEqual(len(root.findall(".//u:CreatePartition", ns)), 3)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "schema": 2,
            "variables": {"v_iso": "C:\\input.iso", "v_unatt": False},
            "editions": [[1, "Windows 11 Pro"]],
            "selected_indexes": [1],
            "extra": {
                "services": {"WSearch": 3},
                "software": [{"path": "setup.exe", "args": ["/S"]}],
            },
        }

    def validate(self, cfg):
        return validate_config(
            cfg,
            {"v_iso": str, "v_unatt": bool},
            {"appx": {"Meteo"}, "caps": set(), "tweaks": {"telemetry"}},
            {"dark"},
            {"RpcSs"},
        )

    def test_valid_and_partial_configs(self):
        self.validate(self.cfg)
        self.validate({"schema": 2})

    def test_invalid_collections_types_and_protected_services(self):
        mutations = [
            {"files": {}},
            {"extra": []},
            {"extra": {"ui": []}},
            {"extra": {"services": {"rpcss": 4}}},
            {"extra": {"services": {"WSearch": True}}},
            {"editions": [[True, "Pro"]]},
            {"editions": [[1, "Pro"], [1, "Home"]]},
            {"selected_indexes": [1, 1]},
            {"selected_indexes": [True]},
            {"selected_indexes": [99]},
            {"variables": {"v_unatt": "false"}},
            {"variables": {"v_pwd": "secret"}},
            {"variables": {"v_iso": "bad\x00path"}},
            {"appx": {"Meteo": 1}},
            {"extra": {"packages": ["name; command"]}},
            {"extra": {"software": {}}},
            {"extra": {"software": [{"path": "app.exe", "args": ["a\ncommand"]}]}},
            {"unknown": True},
        ]
        for mutation in mutations:
            cfg = copy.deepcopy(self.cfg)
            cfg.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(cfg)

    def test_extra_file_cannot_escape_image(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["files"] = [{"src": "a", "where": "win", "dest": "../outside", "contents": False}]
        with self.assertRaises(BuildError):
            self.validate(cfg)
