"""Readable Windows servicing backend; no embedded bytecode or shell evaluation.

The UI is usable on other platforms. Servicing requires Windows and elevation.
All writes target a private working copy, never the source ISO or live Windows.
"""

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree as ET

APP_NAME = "WinSlim Studio"
IS_WIN = os.name == "nt"
ENC = "oem" if IS_WIN else "utf-8"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
D, S = "REG_DWORD", "REG_SZ"
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
APPX = [
    ("Notizie (Bing News)", ["Microsoft.BingNews"], False),
    ("Meteo", ["Microsoft.BingWeather"], False),
    ("Bing Search", ["Microsoft.BingSearch"], False),
    ("Ottieni assistenza", ["Microsoft.GetHelp"], False),
    ("Suggerimenti (Get Started)", ["Microsoft.Getstarted"], False),
    ("Microsoft 365 (Office Hub)", ["Microsoft.MicrosoftOfficeHub"], False),
    ("Solitaire Collection", ["Microsoft.MicrosoftSolitaireCollection"], False),
    ("Persone", ["Microsoft.People"], False),
    ("Power Automate", ["Microsoft.PowerAutomateDesktop"], False),
    ("Microsoft To Do", ["Microsoft.Todos"], False),
    ("Hub di Feedback", ["Microsoft.WindowsFeedbackHub"], False),
    ("Mappe", ["Microsoft.WindowsMaps"], False),
    ("Collegamento al telefono", ["Microsoft.YourPhone", "MicrosoftWindows.CrossDevice"], False),
    ("Groove Musica", ["Microsoft.ZuneMusic"], False),
    ("Film e TV", ["Microsoft.ZuneVideo"], False),
    ("Clipchamp", ["Clipchamp.Clipchamp"], False),
    ("Teams (consumer)", ["MicrosoftTeams", "MSTeams"], False),
    ("Nuovo Outlook", ["Microsoft.OutlookForWindows"], False),
    ("Posta e Calendario", ["microsoft.windowscommunicationsapps"], False),
    ("Dev Home", ["Microsoft.Windows.DevHome"], False),
    ("Skype", ["Microsoft.SkypeApp"], False),
    ("Cortana", ["Microsoft.549981C3F5F10"], False),
    ("Mixed Reality", ["Microsoft.MixedReality.Portal"], False),
    ("App Copilot", ["Microsoft.Copilot", "Microsoft.Windows.Ai.Copilot.Provider"], False),
    (
        "Xbox (app, overlay, identity)",
        [
            "Microsoft.XboxApp",
            "Microsoft.GamingApp",
            "Microsoft.XboxGamingOverlay",
            "Microsoft.XboxSpeechToTextOverlay",
            "Microsoft.Xbox.TCUI",
            "Microsoft.XboxIdentityProvider",
        ],
        False,
    ),
    ("Sticky Notes", ["Microsoft.MicrosoftStickyNotes"], False),
    ("Sveglie e orologio", ["Microsoft.WindowsAlarms"], False),
    ("Registratore di suoni", ["Microsoft.WindowsSoundRecorder"], False),
    ("Quick Assist (app)", ["MicrosoftCorporationII.QuickAssist"], False),
]
CAPS = [
    ("Internet Explorer", "Browser.InternetExplorer", False),
    ("Registratore passaggi", "App.StepsRecorder", False),
    ("Math Recognizer", "MathRecognizer", False),
    ("WordPad", "Microsoft.Windows.WordPad", False),
    ("Quick Assist (componente)", "App.Support.QuickAssist", False),
]
TWEAKS = [
    (
        "telemetry",
        "Riduci telemetria e notifiche di feedback",
        False,
        [
            ("SOFTWARE", r"Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", D, 0),
            (
                "SOFTWARE",
                r"Policies\Microsoft\Windows\DataCollection",
                "DoNotShowFeedbackNotifications",
                D,
                1,
            ),
        ],
    ),
    (
        "consumer",
        "Blocca app e suggerimenti automatici",
        False,
        [
            (
                "SOFTWARE",
                r"Policies\Microsoft\Windows\CloudContent",
                "DisableWindowsConsumerFeatures",
                D,
                1,
            )
        ]
        + [
            ("NTUSER", CDM, n, D, 0)
            for n in (
                "SilentInstalledAppsEnabled",
                "OemPreInstalledAppsEnabled",
                "PreInstalledAppsEnabled",
                "ContentDeliveryAllowed",
                "SystemPaneSuggestionsEnabled",
                "SubscribedContent-338388Enabled",
                "SubscribedContent-338389Enabled",
                "SubscribedContent-310093Enabled",
            )
        ],
    ),
    (
        "adid",
        "Disattiva ID pubblicitario",
        False,
        [("NTUSER", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", D, 0)],
    ),
    (
        "copilot",
        "Disattiva Copilot",
        False,
        [("SOFTWARE", r"Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot", D, 1)],
    ),
    (
        "recall",
        "Disattiva analisi dati AI (Recall)",
        False,
        [("SOFTWARE", r"Policies\Microsoft\Windows\WindowsAI", "DisableAIDataAnalysis", D, 1)],
    ),
    (
        "bingsearch",
        "Niente risultati web nel menu Start",
        False,
        [
            (
                "NTUSER",
                r"Software\Policies\Microsoft\Windows\Explorer",
                "DisableSearchBoxSuggestions",
                D,
                1,
            )
        ],
    ),
    (
        "widgets",
        "Disattiva Widget / Notizie e interessi",
        False,
        [("SOFTWARE", r"Policies\Microsoft\Dsh", "AllowNewsAndInterests", D, 0)],
    ),
    ("chat", "Nascondi icona Chat/Teams", False, [("NTUSER", ADV, "TaskbarMn", D, 0)]),
    (
        "ext",
        "Mostra estensioni file e file nascosti",
        False,
        [("NTUSER", ADV, "HideFileExt", D, 0), ("NTUSER", ADV, "Hidden", D, 1)],
    ),
    (
        "ctxmenu",
        "Menu contestuale classico (Windows 11)",
        False,
        [
            (
                "NTUSER",
                r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32",
                "",
                S,
                "",
            )
        ],
    ),
    (
        "taskbarleft",
        "Barra delle applicazioni a sinistra",
        False,
        [("NTUSER", ADV, "TaskbarAl", D, 0)],
    ),
    (
        "hidesearch",
        "Nascondi casella di ricerca",
        False,
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Search",
                "SearchboxTaskbarMode",
                D,
                0,
            )
        ],
    ),
    (
        "nro",
        "Setup senza internet/account Microsoft (dipende dalla build)",
        False,
        [("SOFTWARE", r"Microsoft\Windows\CurrentVersion\OOBE", "BypassNRO", D, 1)],
    ),
]
BYPASS_HW = [
    ("SYSTEM", r"Setup\LabConfig", n, D, 1)
    for n in (
        "BypassTPMCheck",
        "BypassSecureBootCheck",
        "BypassRAMCheck",
        "BypassCPUCheck",
        "BypassStorageCheck",
    )
]
BYPASS_HW.append(("SYSTEM", r"Setup\MoSetup", "AllowUpgradesWithUnsupportedTPMOrCPU", D, 1))
HIVE_FILES = {
    "SOFTWARE": r"Windows\System32\config\SOFTWARE",
    "SYSTEM": r"Windows\System32\config\SYSTEM",
    "NTUSER": r"Users\Default\NTUSER.DAT",
}
HOME_PRO_RE = re.compile(r"^Windows\s+\d+\s+(Home|Pro)$", re.I)
GENERIC_KEYS = {"home": "YTMG3-N6DKC-DKB77-7M9GH-8HVX7", "pro": "VK7JG-NPHTM-C97JM-9MPGT-3V66T"}


class BuildError(Exception):
    """A diagnosed error in a Windows servicing operation."""


def is_admin():
    return bool(IS_WIN and ctypes.windll.shell32.IsUserAnAdmin())


def windows_file_version(path):
    """Read a local tool's version resource without launching it."""
    if not IS_WIN:
        return None
    from ctypes import wintypes

    lib = ctypes.WinDLL("version", use_last_error=True)
    lib.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    lib.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    lib.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    ignored = wintypes.DWORD()
    size = lib.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not lib.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if (
        not lib.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length))
        or length.value < 52
    ):
        return None
    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if values[0] != 0xFEEF04BD:
        return None
    return values[2] >> 16, values[2] & 0xFFFF, values[3] >> 16, values[3] & 0xFFFF


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def safe_dest(base, dest):
    """Confine Windows-style relative paths, including on non-Windows test hosts."""
    import ntpath

    if not isinstance(dest, str) or any(c in dest for c in '\x00\r\n:*?"<>|'):
        raise BuildError("Destinazione non valida: " + str(dest))
    if (
        dest.startswith(("\\", "/"))
        or ntpath.isabs(dest)
        or ntpath.splitdrive(dest)[0]
        or any(p in ("..",) for p in re.split(r"[\\/]", dest))
    ):
        raise BuildError("La destinazione deve essere relativa, senza '..'.")
    for component in re.split(r"[\\/]", dest):
        if component in ("", "."):
            continue
        if component.endswith((" ", ".")) or re.fullmatch(
            r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])(?:\..*)?",
            component,
            re.I,
        ):
            raise BuildError("Nome di destinazione riservato o ambiguo: " + component)
    root = os.path.realpath(base)
    full = os.path.realpath(os.path.join(root, *re.split(r"[\\/]", dest)))
    if os.path.commonpath([root, full]) != root:
        raise BuildError("Destinazione fuori dalla cartella consentita.")
    return full


def hkpath(hive, key):
    if hive not in HIVE_FILES:
        raise BuildError("Hive non supportato: " + hive)
    return "HKLM\\WS_" + hive + ("\\" + key if key else "")


def read_reg_file(path):
    with open(path, "rb") as stream:
        raw = stream.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def rewrite_reg(text, control_set="ControlSet001"):
    """Rewrite key headers only; reject every unsupported root before import."""
    if not re.fullmatch(r"ControlSet\d{3}", control_set):
        raise BuildError("ControlSet non valido.")
    mappings = [
        (
            "HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet",
            "HKEY_LOCAL_MACHINE\\WS_SYSTEM\\" + control_set,
        ),
        ("HKEY_LOCAL_MACHINE\\SYSTEM", "HKEY_LOCAL_MACHINE\\WS_SYSTEM"),
        ("HKEY_LOCAL_MACHINE\\SOFTWARE", "HKEY_LOCAL_MACHINE\\WS_SOFTWARE"),
        ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE\\WS_NTUSER"),
        ("HKEY_CLASSES_ROOT", "HKEY_LOCAL_MACHINE\\WS_SOFTWARE\\Classes"),
    ]
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            match = re.fullmatch(r"\[(-?)([^\]]+)\]", stripped)
            if not match:
                raise BuildError("Intestazione REG non valida.")
            delete, path = match.groups()
            for source, target in mappings:
                if path.casefold() == source.casefold() or path.casefold().startswith(
                    source.casefold() + "\\"
                ):
                    path = target + path[len(source) :]
                    break
            else:
                raise BuildError("Radice REG non supportata: " + path)
            line = "[" + delete + path + "]"
        result.append(line)
    if not any(line.strip().startswith("[") for line in result):
        raise BuildError("Il file REG non contiene chiavi.")
    return "\r\n".join(result) + "\r\n"


def make_unattend(o):
    """Build XML with the XML library: names/passwords cannot inject markup."""
    ns = "urn:schemas-microsoft-com:unattend"
    wcm = "http://schemas.microsoft.com/WMIConfig/2002/State"
    ET.register_namespace("", ns)
    ET.register_namespace("wcm", wcm)
    root = ET.Element("{" + ns + "}unattend")

    def add(parent, tag, value=None, **attrs):
        el = ET.SubElement(parent, "{" + ns + "}" + tag, attrs)
        if value is not None:
            el.text = str(value)
        return el

    def component(settings, name):
        return add(
            settings,
            "component",
            name=name,
            processorArchitecture="amd64",
            publicKeyToken="31bf3856ad364e35",
            language="neutral",
            versionScope="nonSxS",
        )

    pe = add(root, "settings", pass_="windowsPE")
    pe.attrib = {"pass": "windowsPE"}
    intl = component(pe, "Microsoft-Windows-International-Core-WinPE")
    for tag, value in [
        ("InputLocale", o["kbd"]),
        ("SystemLocale", o["lang"]),
        ("UILanguage", o["lang"]),
        ("UserLocale", o["lang"]),
    ]:
        add(intl, tag, value)
    add(add(intl, "SetupUILanguage"), "UILanguage", o["lang"])
    setup = component(pe, "Microsoft-Windows-Setup")
    if o.get("wipe"):
        disk = add(add(setup, "DiskConfiguration"), "Disk", **{"{" + wcm + "}action": "add"})
        add(disk, "DiskID", 0)
        add(disk, "WillWipeDisk", "true")
        creates = add(disk, "CreatePartitions")
        modifies = add(disk, "ModifyPartitions")
        for order, typ, size in [(1, "EFI", 260), (2, "MSR", 16), (3, "Primary", None)]:
            part = add(creates, "CreatePartition", **{"{" + wcm + "}action": "add"})
            add(part, "Order", order)
            add(part, "Type", typ)
            add(part, "Size" if size else "Extend", size or "true")
            if typ != "MSR":
                mod = add(modifies, "ModifyPartition", **{"{" + wcm + "}action": "add"})
                add(mod, "Order", 1 if order == 1 else 2)
                add(mod, "PartitionID", order)
                add(mod, "Format", "FAT32" if typ == "EFI" else "NTFS")
                if typ == "Primary":
                    add(mod, "Letter", "C")
    image = add(add(setup, "ImageInstall"), "OSImage")
    meta = add(add(image, "InstallFrom"), "MetaData", **{"{" + wcm + "}action": "add"})
    add(meta, "Key", "/IMAGE/INDEX")
    add(meta, "Value", o["index"])
    if o.get("wipe"):
        target = add(image, "InstallTo")
        add(target, "DiskID", 0)
        add(target, "PartitionID", 3)
    user_data = add(setup, "UserData")
    add(user_data, "AcceptEula", "true")
    if o.get("key"):
        add(add(user_data, "ProductKey"), "Key", o["key"])
    oobe_settings = add(root, "settings", **{"pass": "oobeSystem"})
    intl = component(oobe_settings, "Microsoft-Windows-International-Core")
    for tag, value in [
        ("InputLocale", o["kbd"]),
        ("SystemLocale", o["lang"]),
        ("UILanguage", o["lang"]),
        ("UserLocale", o["lang"]),
    ]:
        add(intl, tag, value)
    shell = component(oobe_settings, "Microsoft-Windows-Shell-Setup")
    add(shell, "TimeZone", o["tz"])
    local = add(
        add(add(shell, "UserAccounts"), "LocalAccounts"),
        "LocalAccount",
        **{"{" + wcm + "}action": "add"},
    )
    add(local, "Name", o["user"])
    add(local, "Group", "Administrators")
    password = add(local, "Password")
    add(password, "Value", o.get("pwd", ""))
    add(password, "PlainText", "true")
    if o.get("autologon"):
        auto = add(shell, "AutoLogon")
        add(auto, "Username", o["user"])
        add(auto, "Enabled", "true")
        add(auto, "LogonCount", 1)
        password = add(auto, "Password")
        add(password, "Value", o.get("pwd", ""))
        add(password, "PlainText", "true")
    oobe = add(shell, "OOBE")
    add(oobe, "HideEULAPage", "true")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


class Builder:
    def __init__(self, cfg, log):
        self.c = json.loads(json.dumps(cfg))
        self.log = log
        self.root = os.path.join(cfg["work"], "WinSlim_work")
        self.isodir = os.path.join(self.root, "iso")
        self.mnt = os.path.join(self.root, "mount")
        self.bmnt = os.path.join(self.root, "bootmount")
        self.mounted = []
        self.hives = []
        self.osc = ""
        self.dism = "dism.exe"
        self._cleaning = False
        self.run_id = str(time.time_ns())
        self.source_fingerprint = None
        self.check_cancel = lambda: None

    def run(self, cmd, ok=(0,), quiet=False):
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8" if "powershell" in os.path.basename(cmd[0]).lower() else ENC,
            errors="replace",
            creationflags=NOWIN,
        )
        lines = (proc.stdout + proc.stderr).splitlines()
        if not quiet:
            for line in lines:
                self.log(line)
        if ok is not None and proc.returncode not in ok:
            raise BuildError("Comando fallito (%s): %s" % (proc.returncode, "\n".join(lines[-18:])))
        return proc.returncode, lines

    def ps(self, script, quiet=False):
        os.makedirs(self.root, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", dir=self.root, encoding="utf-8-sig", delete=False
        ) as stream:
            path = stream.name
            stream.write(
                "$ErrorActionPreference='Stop'\n$ProgressPreference='SilentlyContinue'\n[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
                + script
            )
        try:
            return self.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                quiet=quiet,
            )
        finally:
            os.unlink(path)

    def load_hives(self, mnt, hives):
        try:
            for hive in sorted(set(hives)):
                self.run(
                    ["reg.exe", "load", "HKLM\\WS_" + hive, safe_dest(mnt, HIVE_FILES[hive])],
                    quiet=True,
                )
                self.hives.append(hive)
        except BaseException:
            self.unload_hives()
            raise

    def unload_hives(self):
        failed = []
        cleaning = self._cleaning
        self._cleaning = True
        try:
            for hive in reversed(self.hives[:]):
                for attempt in range(4):
                    rc, _ = self.run(["reg.exe", "unload", "HKLM\\WS_" + hive], ok=None, quiet=True)
                    if rc == 0:
                        self.hives.remove(hive)
                        break
                    time.sleep(0.2)
                else:
                    failed.append(hive)
        finally:
            self._cleaning = cleaning
        if failed:
            raise BuildError(
                "Hive ancora caricati: " + ", ".join(failed) + ". Conserva " + self.root
            )

    def reg_add(self, entry):
        hive, key, name, typ, value = entry
        args = (
            ["reg.exe", "add", hkpath(hive, key), "/v", name]
            if name
            else ["reg.exe", "add", hkpath(hive, key), "/ve"]
        )
        self.run(args + ["/t", typ, "/d", str(value), "/f"], quiet=True)

    def apply_entries(self, mnt, entries, regfiles, defender=False):
        # Validate all REG roots before invoking reg.exe, including deletions.
        converted = [rewrite_reg(read_reg_file(path)) for path in regfiles]
        hives = {e[0] for e in entries}
        if converted:
            hives.update(HIVE_FILES)
        if defender:
            raise BuildError(
                "La rimozione fisica di Defender non è supportata: usa un criterio offline verificabile."
            )
        if not hives:
            return
        self.load_hives(mnt, hives)
        try:
            for entry in entries:
                self.reg_add(entry)
            if converted:
                _, output = self.run(
                    ["reg.exe", "query", "HKLM\\WS_SYSTEM\\Select", "/v", "Current"], quiet=True
                )
                match = re.search(r"REG_DWORD\s+(0x[0-9a-fA-F]+|\d+)", "\n".join(output))
                if not match:
                    raise BuildError("ControlSet corrente non rilevato.")
                current = "ControlSet%03d" % int(match.group(1), 0)
                for source in regfiles:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".reg", dir=self.root, encoding="utf-16", delete=False
                    ) as stream:
                        path = stream.name
                        stream.write(rewrite_reg(read_reg_file(source), current))
                    try:
                        self.run(["reg.exe", "import", path], quiet=True)
                    finally:
                        os.unlink(path)
        finally:
            self.unload_hives()

    def find_oscdimg(self):
        specified = self.c.get("oscdimg", "")
        if specified:
            if not os.path.isfile(specified):
                raise BuildError("oscdimg.exe non trovato nel percorso indicato.")
            return specified
        found = shutil.which("oscdimg.exe")
        if found:
            return found
        for variable in ("ProgramFiles(x86)", "ProgramFiles"):
            for arch in ("amd64", "x86", "arm64"):
                candidate = os.path.join(
                    os.environ.get(variable, "C:\\Program Files"),
                    "Windows Kits",
                    "10",
                    "Assessment and Deployment Kit",
                    "Deployment Tools",
                    arch,
                    "Oscdimg",
                    "oscdimg.exe",
                )
                if os.path.isfile(candidate):
                    return candidate
        raise BuildError("oscdimg.exe non trovato. Installa Deployment Tools del Windows ADK.")

    def preflight(self):
        if not IS_WIN or not is_admin():
            raise BuildError("La creazione richiede Windows 10/11 e diritti di amministratore.")
        c = self.c
        if not os.path.isfile(c["iso"]) or not c["iso"].lower().endswith(".iso"):
            raise BuildError("Seleziona un file ISO valido.")
        if not c.get("out", "").lower().endswith(".iso"):
            raise BuildError("Scegli la ISO di uscita.")
        if (
            not c.get("indexes")
            or len(c["indexes"]) != len(set(c["indexes"]))
            or any(type(i) is not int or i < 1 for i in c["indexes"])
        ):
            raise BuildError("Selezione degli indici non valida.")
        if len(c["indexes"]) != len(c["names"]):
            raise BuildError("Indici e nomi delle edizioni non corrispondono.")
        for item in c.get("files", []):
            if not os.path.exists(item["src"]):
                raise BuildError("File aggiuntivo non trovato: " + item["src"])
            safe_dest(self.root, item["dest"])
        for source in c.get("regfiles", []):
            rewrite_reg(read_reg_file(source))
        if c.get("defender"):
            raise BuildError("Rimozione di Defender non supportata: la protezione è preservata.")
        if c.get("unattended"):
            user = c.get("user", "")
            if (
                not user
                or user != user.strip()
                or len(user) > 20
                or any(ch in user for ch in '\\/:*?"<>|@+[];=,\x00\r\n')
            ):
                raise BuildError(
                    "Nome utente non valido (1–20 caratteri, senza caratteri riservati)."
                )
            if c.get("auto_name") not in c["names"]:
                raise BuildError("Scegli l'edizione da installare automaticamente.")
            for key in ("lang", "kbd", "tz", "pwd"):
                if any(ord(ch) < 32 for ch in c.get(key, "")):
                    raise BuildError("Caratteri di controllo non ammessi: " + key)
        if os.path.lexists(self.root):
            raise BuildError(
                "La cartella di lavoro contiene una lavorazione precedente. Scegli una nuova cartella o recupera quella esistente: "
                + self.root
            )
        os.makedirs(c["work"], exist_ok=True)
        output_parent = os.path.dirname(os.path.abspath(c["out"]))
        os.makedirs(output_parent, exist_ok=True)
        required = max(20 * 1024**3, os.path.getsize(c["iso"]) * 4)
        if shutil.disk_usage(c["work"]).free < required:
            raise BuildError(
                "Spazio insufficiente nella cartella di lavoro: richiesti %.1f GB."
                % (required / 1024**3)
            )
        if shutil.disk_usage(output_parent).free < os.path.getsize(c["iso"]):
            raise BuildError("Spazio insufficiente per la ISO di uscita.")
        self.osc = self.find_oscdimg()
        self.dism = self.find_dism()
        self.log("Strumento DISM: " + self.dism)

    def find_dism(self):
        candidate = os.path.join(os.path.dirname(os.path.dirname(self.osc)), "DISM", "dism.exe")
        system = shutil.which("dism.exe")
        if os.path.isfile(candidate):
            if not system:
                return candidate
            adk_version, system_version = (
                windows_file_version(candidate),
                windows_file_version(system),
            )
            if (
                adk_version is not None
                and system_version is not None
                and adk_version > system_version
            ):
                return candidate
        return system or "dism.exe"

    def step_extract(self):
        # mkdir is exclusive: a concurrent build cannot take over this work tree.
        os.mkdir(self.root)
        with open(os.path.join(self.root, "owner.json"), "x", encoding="utf-8") as stream:
            json.dump({"run_id": self.run_id, "pid": os.getpid()}, stream)
        for path in (self.isodir, self.mnt, self.bmnt):
            os.mkdir(path)
        iso = q(os.path.abspath(self.c["iso"]))
        self.ps(
            """$owned=$false
try {
 $disk=Get-DiskImage -ImagePath """
            + iso
            + """
 if(!$disk.Attached){$disk=Mount-DiskImage -ImagePath """
            + iso
            + """ -PassThru;$owned=$true}
 $vol=$disk|Get-Volume
 if(!$vol.DriveLetter){throw 'Lettera ISO non disponibile'}
 & robocopy.exe (([string]$vol.DriveLetter)+':\\') """
            + q(self.isodir)
            + """ /E /NFL /NDL /NJH /NJS /NP
 if($LASTEXITCODE -ge 8){throw ('Copia ISO fallita: '+$LASTEXITCODE)}
} finally {if($owned){Dismount-DiskImage -ImagePath """
            + iso
            + """ | Out-Null}}
"""
        )
        self.run(["attrib.exe", "-R", os.path.join(self.isodir, "*"), "/S", "/D"])
        if self.source_fingerprint is not None:
            from .safety import fingerprint

            if fingerprint(self.c["iso"]) != self.source_fingerprint:
                raise BuildError("La ISO sorgente è cambiata durante la copia.")

    def step_prepare_wim(self):
        sources = os.path.join(self.isodir, "sources")
        source = next(
            (
                os.path.join(sources, n)
                for n in ("install.wim", "install.esd")
                if os.path.isfile(os.path.join(sources, n))
            ),
            None,
        )
        if source is None:
            raise BuildError(
                "install.wim/install.esd non trovato; immagini suddivise SWM non supportate."
            )
        target = os.path.join(sources, "install_new.wim")
        if os.path.exists(target):
            raise BuildError("install_new.wim esiste già nella ISO sorgente.")
        for index in self.c["indexes"]:
            self.run(
                [
                    "dism.exe",
                    "/English",
                    "/Export-Image",
                    "/SourceImageFile:" + source,
                    "/SourceIndex:" + str(index),
                    "/DestinationImageFile:" + target,
                    "/Compress:max",
                    "/CheckIntegrity",
                ]
            )
        for name in ("install.wim", "install.esd"):
            path = os.path.join(sources, name)
            if os.path.exists(path):
                os.unlink(path)
        os.replace(target, os.path.join(sources, "install.wim"))

    def mount(self, image, index, path):
        self.run(
            [
                "dism.exe",
                "/English",
                "/Mount-Image",
                "/ImageFile:" + image,
                "/Index:" + str(index),
                "/MountDir:" + path,
                "/CheckIntegrity",
            ]
        )
        self.mounted.append(path)

    def dismount(self, path, save=True):
        self.run(
            [
                "dism.exe",
                "/English",
                "/Unmount-Image",
                "/MountDir:" + path,
                "/Commit" if save else "/Discard",
            ],
            ok=(0, 3010),
        )
        self.mounted.remove(path)

    def step_remove_apps(self):
        names = [
            name for label, items, _ in APPX if label in self.c.get("appx", []) for name in items
        ]
        if not names:
            return
        self.ps(
            "$names=@("
            + ",".join(q(name) for name in names)
            + ")\nGet-AppxProvisionedPackage -Path "
            + q(self.mnt)
            + " | Where-Object { $names -contains $_.DisplayName } | ForEach-Object { Remove-AppxProvisionedPackage -Path "
            + q(self.mnt)
            + " -PackageName $_.PackageName -ErrorAction Stop | Out-Null; Write-Output ('Rimossa: '+$_.DisplayName) }"
        )

    def step_remove_caps(self):
        raise NotImplementedError("Provided by the servicing extension")

    def step_onedrive(self):
        for relative in (
            r"Windows\SysWOW64\OneDriveSetup.exe",
            r"Windows\System32\OneDriveSetup.exe",
        ):
            path = safe_dest(self.mnt, relative)
            if os.path.isfile(path):
                self.run(["takeown.exe", "/f", path], quiet=True)
                self.run(["icacls.exe", path, "/grant", "*S-1-5-32-544:F"], quiet=True)
                os.unlink(path)
        self.load_hives(self.mnt, ["NTUSER"])
        try:
            self.ps(
                "$p='Registry::HKEY_LOCAL_MACHINE\\WS_NTUSER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'; if(Test-Path $p){Remove-ItemProperty $p -Name OneDriveSetup -ErrorAction SilentlyContinue}"
            )
        finally:
            self.unload_hives()

    def step_recall(self):
        self.ps(
            "$features=@(Get-WindowsOptionalFeature -Path "
            + q(self.mnt)
            + " | Where-Object {$_.FeatureName -eq 'Recall'}); foreach($f in $features){Disable-WindowsOptionalFeature -Path "
            + q(self.mnt)
            + " -FeatureName $f.FeatureName -Remove -NoRestart -ErrorAction Stop | Out-Null}; if(!$features){Write-Output 'Recall non presente'}"
        )

    def _copy(self, src, dest, contents=False):
        if os.path.islink(src) or (hasattr(os.path, "isjunction") and os.path.isjunction(src)):
            raise BuildError("Link/junction non supportato come sorgente: " + src)
        if os.path.isdir(src):
            target = dest if contents else safe_dest(dest, os.path.basename(os.path.normpath(src)))
            # Reject links/junctions before copytree can escape the mounted image.
            for root, dirs, files in os.walk(src, followlinks=False):
                for name in dirs + files:
                    path = os.path.join(root, name)
                    safe_dest(target, os.path.relpath(path, src))
                    if os.path.islink(path) or (
                        hasattr(os.path, "isjunction") and os.path.isjunction(path)
                    ):
                        raise BuildError(
                            "Link/junction non supportato nei file aggiuntivi: " + path
                        )
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(src, safe_dest(dest, os.path.basename(src)))

    def copy_files(self, where, base):
        for item in self.c.get("files", []):
            if item["where"] == where:
                self._copy(item["src"], safe_dest(base, item["dest"]), item["contents"])

    def step_iso_files(self):
        self.copy_files("iso", self.isodir)
        if self.c.get("unattended"):
            index = self.c["names"].index(self.c["auto_name"]) + 1
            name = self.c["auto_name"]
            edition = (
                "pro"
                if re.search(r"\bPro$", name, re.I)
                else "home"
                if re.search(r"\bHome$", name, re.I)
                else ""
            )
            options = dict(self.c, index=index, key=GENERIC_KEYS.get(edition, ""))
            with open(
                os.path.join(self.isodir, "autounattend.xml"), "w", encoding="utf-8"
            ) as stream:
                stream.write(make_unattend(options))

    def step_bypass_boot(self):
        boot = os.path.join(self.isodir, "sources", "boot.wim")
        if not os.path.isfile(boot):
            raise BuildError("boot.wim non trovato per il bypass richiesto.")
        self.mount(boot, 2, self.bmnt)
        self.apply_entries(self.bmnt, BYPASS_HW, [], False)
        self.dismount(self.bmnt, True)

    def step_make_iso(self):
        boot = safe_dest(self.isodir, r"boot\etfsboot.com")
        efi = next(
            (
                safe_dest(self.isodir, p)
                for p in (
                    r"efi\microsoft\boot\efisys_noprompt.bin",
                    r"efi\microsoft\boot\efisys.bin",
                )
                if os.path.isfile(safe_dest(self.isodir, p))
            ),
            None,
        )
        if not os.path.isfile(boot) or efi is None:
            raise BuildError(
                "File di avvio BIOS/UEFI non trovati. Sono supportate ISO Windows x64 ufficiali."
            )
        output = os.path.abspath(self.c["out"])
        handle, pending = tempfile.mkstemp(
            prefix=".winslim-", suffix=".iso", dir=os.path.dirname(output)
        )
        os.close(handle)
        os.unlink(pending)
        try:
            self.run(
                [
                    self.osc,
                    "-m",
                    "-o",
                    "-u2",
                    "-udfver102",
                    "-lWINSLIM",
                    "-bootdata:2#p0,e,b" + boot + "#pEF,e,b" + efi,
                    self.isodir,
                    pending,
                ]
            )
            if not os.path.isfile(pending) or os.path.getsize(pending) == 0:
                raise BuildError("oscdimg non ha prodotto una ISO valida.")
            from .safety import sha256_file

            event = getattr(self, "cancel_event", None)
            try:
                self.output_sha256 = sha256_file(pending, event)
            except BuildError:
                self.check_cancel()
                raise
            self.check_cancel()
            os.replace(pending, output)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)

    def cleanup_on_error(self):
        self._cleaning = True
        try:
            self.unload_hives()
            for path in self.mounted[:]:
                self.dismount(path, False)
        finally:
            self._cleaning = False

    def build(self):
        with ServicingLock():
            self._build()

    def _build(self):
        self.preflight()
        entries = [
            entry
            for key, _, _, values in TWEAKS
            if key in self.c.get("tweaks", [])
            for entry in values
        ]
        if self.c.get("bypass"):
            entries += BYPASS_HW
        plan = [
            ("Estrazione ISO", self.step_extract),
            ("Preparazione immagini", self.step_prepare_wim),
        ]
        image = os.path.join(self.isodir, "sources", "install.wim")
        for index, name in enumerate(self.c["names"], 1):
            plan += [
                (name + " · Montaggio", lambda i=index: self.mount(image, i, self.mnt)),
                (name + " · App", self.step_remove_apps),
                (name + " · Componenti", self.step_remove_caps),
            ]
            if self.c.get("onedrive"):
                plan.append((name + " · OneDrive", self.step_onedrive))
            if self.c.get("recall"):
                plan.append((name + " · Recall", self.step_recall))
            plan += [
                (
                    name + " · Registro",
                    lambda: self.apply_entries(self.mnt, entries, self.c.get("regfiles", [])),
                ),
                (name + " · File", lambda: self.copy_files("win", self.mnt)),
                (name + " · Integrazioni e salvataggio", lambda: self.dismount(self.mnt, True)),
            ]
        if self.c.get("bypass"):
            plan.append(("Requisiti Windows 11", self.step_bypass_boot))
        plan += [
            ("File ISO e installazione", self.step_iso_files),
            ("Creazione ISO e report", self.step_make_iso),
        ]
        try:
            for step, (name, fn) in enumerate(plan, 1):
                self.log("=== [%d/%d] %s ===" % (step, len(plan), name))
                fn()
        except BaseException:
            self.cleanup_on_error()
            raise
        if self.c.get("delete_work"):
            if self.mounted or self.hives:
                raise BuildError("Pulizia impossibile: immagini o hive ancora in uso.")
            with open(os.path.join(self.root, "owner.json"), encoding="utf-8") as stream:
                owner = json.load(stream)
            if owner.get("run_id") != self.run_id:
                raise BuildError("La cartella di lavoro non appartiene a questa operazione.")
            shutil.rmtree(self.root)
        self.log("ISO creata: " + self.c["out"])


class ServicingLock:
    """Hold the named Windows mutex while shared offline hive names are in use."""

    def __enter__(self):
        if not IS_WIN:
            raise BuildError("La creazione richiede Windows.")
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateMutexW(None, False, "Global\\WinSlimStudioOfflineServicing")
        if not self.handle:
            raise BuildError(
                "Impossibile creare il blocco di lavorazione: " + str(ctypes.get_last_error())
            )
        result = kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 0x80):
            kernel.CloseHandle(self.handle)
            raise BuildError(
                "Un'altra istanza di WinSlim sta lavorando. Attendi la sua conclusione."
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        self.kernel.ReleaseMutex(self.handle)
        self.kernel.CloseHandle(self.handle)


def read_editions(iso, log):
    with tempfile.TemporaryDirectory(prefix="WinSlimEditions_") as temp:
        builder = Builder({"work": temp}, log)
        builder.root = temp
        script = (
            """$owned=$false
try {
 $disk=Get-DiskImage -ImagePath """
            + q(iso)
            + """
 if(!$disk.Attached){$disk=Mount-DiskImage -ImagePath """
            + q(iso)
            + """ -PassThru;$owned=$true}
 $vol=$disk|Get-Volume
 if(!$vol.DriveLetter){throw 'Lettera ISO non disponibile'}
 $f=([string]$vol.DriveLetter)+':\\sources\\install.wim'
 if(!(Test-Path $f)){$f=([string]$vol.DriveLetter)+':\\sources\\install.esd'}
 Get-WindowsImage -ImagePath $f | ForEach-Object { Write-Output ('EDITION|'+$_.ImageIndex+'|'+$_.ImageName) }
 $info=Get-WindowsImage -ImagePath $f -Index 1
 Write-Output ('LANG|'+(@($info.Languages)[0]))
} finally {if($owned){Dismount-DiskImage -ImagePath """
            + q(iso)
            + """ | Out-Null}}
"""
        )
        _, output = builder.ps(script, quiet=True)
    editions, lang = [], "it-IT"
    for line in output:
        match = re.match(r"^EDITION\|(\d+)\|(.+)$", line)
        if match:
            editions.append((int(match.group(1)), match.group(2)))
        elif line.startswith("LANG|") and line[5:].strip():
            lang = line[5:].strip()
    if not editions:
        raise BuildError("Nessuna edizione trovata.")
    return editions, lang


class FileDialog(tk.Toplevel):
    def __init__(self, parent, on_ok):
        super().__init__(parent)
        self.title("Aggiungi file o cartella")
        self.transient(parent)
        self.on_ok = on_ok
        self.source = tk.StringVar()
        self.dest = tk.StringVar(value="Extras")
        self.where = tk.StringVar(value="win")
        self.contents = tk.BooleanVar(value=False)
        panel = ttk.Frame(self, padding=24)
        panel.pack(fill="both", expand=True)
        for text, var in [
            ("Origine", self.source),
            ("Destinazione relativa (es. ProgramData\\Extras)", self.dest),
        ]:
            ttk.Label(panel, text=text).pack(anchor="w", pady=5)
            ttk.Entry(panel, textvariable=var, width=65).pack(fill="x")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=10)
        ttk.Button(row, text="File", command=lambda: self.pick(False)).pack(side="left")
        ttk.Button(row, text="Cartella", command=lambda: self.pick(True)).pack(side="left", padx=8)
        for label, value in [("Windows installato", "win"), ("ISO", "iso")]:
            ttk.Radiobutton(panel, text=label, value=value, variable=self.where).pack(anchor="w")
        ttk.Checkbutton(
            panel, text="Copia solo il contenuto della cartella", variable=self.contents
        ).pack(anchor="w", pady=10)
        ttk.Button(panel, text="Aggiungi", command=self.ok).pack(anchor="e")
        self.grab_set()

    def pick(self, directory):
        value = (
            filedialog.askdirectory(parent=self)
            if directory
            else filedialog.askopenfilename(parent=self)
        )
        if value:
            self.source.set(value)

    def ok(self):
        try:
            if not os.path.exists(self.source.get()):
                raise BuildError("Seleziona un file o una cartella esistente.")
            safe_dest(os.getcwd(), self.dest.get())
            self.on_ok(
                {
                    "src": self.source.get(),
                    "where": self.where.get(),
                    "dest": self.dest.get(),
                    "contents": self.contents.get(),
                }
            )
            self.destroy()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)


class App(tk.Tk):
    """Shared state and small UI actions; the Studio module renders the pages."""

    def __init__(self):
        super().__init__()
        self.q = queue.Queue()
        self.running = False
        self.editions = []
        self.files = []
        defaults = {
            "iso": "",
            "out": "",
            "work": "C:\\WinSlim" if IS_WIN else os.path.join(tempfile.gettempdir(), "WinSlim"),
            "osc": "",
            "lang": "it-IT",
            "kbd": "it-IT",
            "tz": "W. Europe Standard Time",
            "user": "Utente",
            "pwd": "",
            "auto": "",
        }
        for name, value in defaults.items():
            setattr(self, "v_" + name, tk.StringVar(value=value))
        for name in (
            "onedrive",
            "recall",
            "defender",
            "bypass",
            "unatt",
            "wipe",
            "autologon",
            "delwork",
        ):
            setattr(self, "v_" + name, tk.BooleanVar(value=False))
        self.appx_vars = {label: tk.BooleanVar(value=False) for label, _, _ in APPX}
        self.cap_vars = {label: tk.BooleanVar(value=False) for label, _, _ in CAPS}
        self.tw_vars = {key: tk.BooleanVar(value=False) for key, _, _, _ in TWEAKS}
        self._ui()
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def selected_editions(self):
        return [self.editions[i] for i in self.lbed.curselection()]

    def refresh_auto(self):
        names = [name for _, name in self.selected_editions()]
        self.cb_auto.configure(values=names)
        if self.v_auto.get() not in names:
            self.v_auto.set(names[0] if names else "")

    def only_home_pro(self):
        self.lbed.selection_clear(0, "end")
        for i, (_, name) in enumerate(self.editions):
            if HOME_PRO_RE.fullmatch(name.strip()):
                self.lbed.selection_set(i)
        self.refresh_auto()
        self.refresh_summary()

    def pick_out(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".iso", filetypes=[("ISO", "*.iso")]
        )
        if path:
            self.v_out.set(path)

    def pick_work(self):
        path = filedialog.askdirectory(parent=self)
        if path:
            self.v_work.set(path)

    def pick_osc(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("oscdimg.exe", "*.exe")])
        if path:
            self.v_osc.set(path)

    def add_reg(self):
        for path in filedialog.askopenfilenames(parent=self, filetypes=[("Registro", "*.reg")]):
            if path not in self.lb.get(0, "end"):
                self.lb.insert("end", path)
        self.refresh_summary()

    def del_reg(self):
        for index in reversed(self.lb.curselection()):
            self.lb.delete(index)
        self.refresh_summary()

    def add_file(self, item=None):
        if item is None:
            FileDialog(self, self.add_file)
            return
        self.files.append(dict(item))
        self.tv.insert(
            "",
            "end",
            iid=str(len(self.files) - 1),
            values=(
                item["src"],
                item["where"],
                item["dest"],
                "Contenuto" if item["contents"] else "File/cartella",
            ),
        )
        self.refresh_summary()

    def del_file(self):
        for index in sorted((int(i) for i in self.tv.selection()), reverse=True):
            self.files.pop(index)
        for item in self.tv.get_children():
            self.tv.delete(item)
        for index, item in enumerate(self.files):
            self.tv.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item["src"],
                    item["where"],
                    item["dest"],
                    "Contenuto" if item["contents"] else "File/cartella",
                ),
            )
        self.refresh_summary()


def elevate_if_needed():
    if not IS_WIN or is_admin():
        return
    if getattr(sys, "frozen", False):
        executable, args = sys.executable, sys.argv[1:]
    else:
        executable, args = sys.executable, ["-m", "winslim", *sys.argv[1:]]
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, subprocess.list2cmdline(args), os.getcwd(), 1
    )
    if rc <= 32:
        messagebox.showerror(
            APP_NAME, "Avvio amministratore annullato o non riuscito. Puoi riaprire il programma."
        )
    raise SystemExit(0 if rc > 32 else 1)
