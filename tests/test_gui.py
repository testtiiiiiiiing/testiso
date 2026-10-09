import copy
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from winslim.studio import App, check_config
from winslim.safety import apply_config, fingerprint


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = App()
            cls.app.update()
        except tk.TclError as exc:
            if os.environ.get("WINSLIM_REQUIRE_GUI"):
                raise
            raise unittest.SkipTest("GUI display unavailable: " + str(exc))
        cls.default = copy.deepcopy(cls.app.default_config)

    @classmethod
    def tearDownClass(cls):
        cls.app._close()

    def setUp(self):
        apply_config(self.app, self.default)
        self.app.undo_stack.clear()
        self.app.confirm = lambda *a: True
        self.app.update()

    def test_all_eight_pages_render(self):
        for page in self.app.page_frames:
            with self.subTest(page=page):
                self.app.navigate(page)
                self.app.update()
                self.assertEqual(self.app.current_page, page)
                self.assertTrue(self.app.page_frames[page].winfo_ismapped())

    def test_completed_iso_with_cleanup_warning_shows_success_and_warning(self):
        self.app.operation = "build"
        self.app.total_phases = 12
        self.app.q.put(
            (
                "studio_done",
                {
                    "out": "result.iso",
                    "report": "result.iso.report.json",
                    "warnings": ["Pulizia temporanei non completata: work-folder"],
                },
            )
        )
        with patch.object(self.app, "after"):
            self.app._pump()
        self.assertEqual(self.app.phase_var.get(), "ISO creata con avvisi")
        self.assertIn("work-folder", self.app.note_var.get())
        self.assertEqual(self.app.done_phases, 12)
        self.assertEqual(self.app.last_report, "result.iso.report.json")
        self.assertIsNone(self.app.operation)

    def test_stock_defaults_make_no_removal_requests(self):
        cfg = self.app.collect_build()
        self.assertEqual(cfg["appx"], [])
        self.assertEqual(cfg["caps"], [])
        self.assertFalse(cfg["defender"])
        self.assertFalse(cfg["wipe"])

    def test_privacy_profile_and_undo(self):
        before = self.app.config()
        self.app.apply_preset("Privacy")
        self.assertTrue(self.app.tw_vars["telemetry"].get())
        self.assertTrue(self.app.tw_vars["recall"].get())
        self.assertFalse(any(v.get() for v in self.app.appx_vars.values()))
        self.app.undo_config()
        self.assertEqual(self.app.config()["tweaks"], before["tweaks"])

    def test_config_roundtrip_never_exports_password(self):
        self.app.v_pwd.set("sensitive-password")
        cfg = self.app.config()
        check_config(cfg, self.app)
        self.assertNotIn("sensitive-password", json.dumps(cfg))
        apply_config(self.app, cfg)
        self.assertEqual(self.app.v_pwd.get(), "")

    def test_partial_import_clears_previous_choices(self):
        self.app.tw_vars["telemetry"].set(True)
        self.app.appx_vars["Meteo"].set(True)
        cfg = {"schema": 2}
        check_config(cfg, self.app)
        apply_config(self.app, cfg)
        self.assertFalse(self.app.tw_vars["telemetry"].get())
        self.assertFalse(self.app.appx_vars["Meteo"].get())

    def test_legacy_defender_request_is_normalized_on_import(self):
        cfg = self.app.config()
        cfg["variables"]["v_defender"] = True
        check_config(cfg, self.app)
        apply_config(self.app, cfg)
        self.assertFalse(self.app.v_defender.get())
        self.assertFalse(self.app.collect_build()["defender"])
        self.assertFalse(self.app.config()["variables"]["v_defender"])

    def test_stale_defender_state_cannot_block_build(self):
        self.app.v_defender.set(True)
        self.assertFalse(self.app.collect_build()["defender"])
        self.assertFalse(self.app.v_defender.get())

    def test_invalid_import_leaves_state_unchanged(self):
        before = self.app.config()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text(
                json.dumps(
                    {"schema": 2, "variables": {"v_iso": "new.iso"}, "extra": {"software": {}}}
                )
            )
            with (
                patch("winslim.studio.filedialog.askopenfilename", return_value=str(path)),
                self.assertRaises(ValueError),
            ):
                self.app.import_config()
        self.assertEqual(self.app.config(), before)

    def test_import_requires_fresh_edition_read(self):
        cfg = self.app.config()
        cfg["editions"] = [[1, "Windows 11 Pro"]]
        cfg["selected_indexes"] = [1]
        with tempfile.TemporaryDirectory() as temp:
            iso = Path(temp) / "in.iso"
            iso.write_bytes(b"iso")
            cfg["variables"]["v_iso"] = str(iso)
            path = Path(temp) / "valid.json"
            path.write_text(json.dumps(cfg))
            with patch("winslim.studio.filedialog.askopenfilename", return_value=str(path)):
                self.app.import_config()
            with self.assertRaisesRegex(ValueError, "Rileggi"):
                self.app.validate_project(self.app.collect_build())

    def test_changed_iso_requires_fresh_read(self):
        with tempfile.TemporaryDirectory() as temp:
            iso = Path(temp) / "in.iso"
            iso.write_bytes(b"iso")
            self.app.v_iso.set(str(iso))
            self.app.editions = [(1, "Windows 11 Pro")]
            self.app.lbed.insert("end", "Windows 11 Pro")
            self.app.lbed.selection_set(0)
            self.app.last_editions_iso = str(iso)
            self.app.editions_fingerprint = fingerprint(iso)
            iso.write_bytes(b"different iso")
            with self.assertRaisesRegex(ValueError, "Rileggi"):
                self.app.validate_project(self.app.collect_build())

    def test_disabled_button_does_not_invoke(self):
        calls = []
        old = self.app.next_btn.command
        self.app.next_btn.configure(command=lambda: calls.append(1), state="disabled")
        self.app.next_btn.invoke()
        self.assertEqual(calls, [])
        self.app.next_btn.configure(command=old, state="normal")

    def test_extra_files_removal_keeps_indices_consistent(self):
        for name in ("first", "second", "third"):
            self.app.add_file({"src": name, "dest": "Extras", "where": "win", "contents": False})
        self.app.tv.selection_set("1")
        self.app.del_file()
        self.assertEqual([i["src"] for i in self.app.files], ["first", "third"])
        self.app.tv.selection_set("1")
        self.app.del_file()
        self.assertEqual([i["src"] for i in self.app.files], ["first"])

    def test_component_filter_and_deduplicated_removals(self):
        self.app.scan_records = [
            ("features", "Recall", "Enabled"),
            ("packages", "Other", "Installed"),
        ]
        self.app.search_components.set("Recall")
        self.assertEqual(len(self.app.scan_tree.get_children()), 1)
        self.app.scan_tree.selection_set(self.app.scan_tree.get_children()[0])
        self.app.set_component_choice()
        self.app.set_component_choice()
        self.assertEqual(self.app.extra_config()["features"], ["Recall"])
        item = self.app.scan_tree.get_children()[0]
        self.assertEqual(self.app.scan_tree.set(item, "state"), "Enabled")
        self.assertEqual(self.app.scan_tree.set(item, "choice"), "Da disattivare")
        self.assertNotIn("features", self.app.plus_text)

    def test_catalog_state_filter_supports_all_discovered_states(self):
        self.app.scan_records = [
            ("features", "FeatureOn", "Enabled"),
            ("features", "FeatureOff", "Disabled"),
            ("packages", "StagedPackage", "Staged"),
            ("capabilities", "InstalledCapability", "Installed"),
        ]
        self.app.filter_components()
        for state in ("Enabled", "Disabled", "Staged", "Installed"):
            self.app.component_state.set(state)
            children = self.app.scan_tree.get_children()
            self.assertEqual(len(children), 1)
            self.assertEqual(self.app.scan_tree.set(children[0], "state"), state)
        self.app.component_state.set("Tutti gli stati")
        self.assertEqual(len(self.app.scan_tree.get_children()), 4)

    def test_catalog_d_and_r_keys_update_selected_rows_in_place(self):
        self.app.navigate("apps")
        self.app.update()
        self.app.scan_records = [
            ("features", "FeatureA", "Enabled"),
            ("packages", "PackageB", "Staged"),
        ]
        self.app.filter_components()
        items = self.app.scan_tree.get_children()
        self.app.scan_tree.selection_set(items)
        self.app.page_canvases["apps"].yview_moveto(1)
        self.app.update()
        self.app.scan_tree.focus_force()
        self.app.update()
        self.app.scan_tree.event_generate("<KeyPress-d>")
        self.app.update()
        self.assertEqual(self.app.scan_tree.selection(), items)
        self.assertEqual(self.app.scan_tree.set(items[0], "choice"), "Da disattivare")
        self.assertEqual(self.app.scan_tree.set(items[1], "choice"), "Da rimuovere")
        self.assertEqual(self.app.collect_build()["extra"]["packages"], ["PackageB"])
        self.app.scan_tree.event_generate("<KeyPress-r>")
        self.app.update()
        self.assertTrue(all(self.app.scan_tree.set(item, "choice") == "Mantieni" for item in items))
        self.assertEqual(self.app.collect_build()["extra"]["features"], [])
        self.assertEqual(self.app.collect_build()["extra"]["packages"], [])

    def test_imported_component_choices_remain_visible_and_can_be_cancelled(self):
        cfg = self.app.config()
        cfg["extra"]["features"] = ["OldFeature"]
        cfg["extra"]["packages"] = ["OldPackage"]
        check_config(cfg, self.app)
        apply_config(self.app, cfg)
        items = self.app.scan_tree.get_children()
        self.assertEqual(len(items), 2)
        self.assertTrue(
            all(self.app.scan_tree.set(item, "state") == "Da verificare" for item in items)
        )
        self.assertEqual(self.app.config()["extra"]["features"], ["OldFeature"])
        self.app.scan_tree.selection_set(items)
        self.app.set_component_choice(False)
        self.assertEqual(self.app.config()["extra"]["features"], [])
        self.assertEqual(self.app.scan_tree.get_children(), ())

    def test_catalog_choices_survive_filters_and_keep_state_sorting(self):
        self.app.scan_records = [
            ("features", "ZFeature", "Enabled"),
            ("packages", "APackage", "Staged"),
        ]
        self.app.filter_components()
        self.app.sort_tree(self.app.scan_tree, "state")
        item = self.app.scan_tree.get_children()[0]
        self.app.scan_tree.selection_set(item)
        self.app.set_component_choice()
        self.app.component_kind.set("packages")
        self.app.component_kind.set("Tutti")
        self.assertEqual(self.app.extra_config()["features"], ["ZFeature"])
        self.assertEqual(self.app.scan_tree.set(item, "choice"), "Da disattivare")
        self.assertEqual(self.app.scan_tree.get_children()[0], item)
        self.assertFalse(self.app.scan_tree.sort_descending)

    def test_catalog_reflects_standard_capability_choices_and_can_cancel_them(self):
        self.app.scan_records = [("capabilities", "MathRecognizer~~~~0.0.1.0", "Installed")]
        self.app.filter_components()
        item = self.app.scan_tree.get_children()[0]
        self.app.cap_vars["Math Recognizer"].set(True)
        self.assertEqual(self.app.scan_tree.set(item, "choice"), "Da rimuovere")
        self.app.scan_tree.selection_set(item)
        self.app.set_component_choice(False)
        self.assertFalse(self.app.cap_vars["Math Recognizer"].get())
        self.assertEqual(self.app.scan_tree.set(item, "choice"), "Mantieni")
