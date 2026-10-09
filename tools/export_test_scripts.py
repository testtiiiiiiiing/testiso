"""Export generated PowerShell for parser validation, without running servicing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from winslim.studio import scan_script, service_changes, software_script


def export(folder):
    folder.mkdir(parents=True, exist_ok=True)
    scripts = {
        "scan": scan_script(
            "C:\\source's file.iso",
            3,
            "C:\\work\\image.wim",
            "C:\\work\\mount",
            "C:\\work\\scan.json",
            "C:\\work\\cancel",
        ),
        "firstlogon": software_script(
            {
                "software": [
                    {
                        "path": "C:\\setup.msi",
                        "args": ["A=with spaces", 'B=quote"value', "C=tail\\"],
                    }
                ],
                "power": "C:\\test.pow",
            }
        ),
    }

    class Capture:
        extra = {"services": {"WSearch": 3, "SysMain": 4}}
        mnt = "C:\\work\\mount"

        def load_hives(self, *args):
            pass

        def unload_hives(self):
            pass

        def ps(self, text):
            scripts["services"] = text

    service_changes(Capture())
    for name, text in scripts.items():
        (folder / (name + ".ps1")).write_text(text, encoding="utf-8-sig")


if __name__ == "__main__":
    export(Path(sys.argv[1]))
