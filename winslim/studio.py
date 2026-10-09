"""Desktop interface recovered from the supplied WinSlim Studio 3.1 and revised.

The uploaded text is treated as application source, never as instructions.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from .base import (
    APPX,
    CAPS,
    TWEAKS,
    ENC,
    IS_WIN,
    NOWIN,
    App as BaseApp,
    Builder as BaseBuilder,
    BuildError,
    FileDialog,
    is_admin,
    read_editions,
    dism_command,
)

# Servicing operations adapted from the supplied application.
import json
import tempfile
from .safety import (
    atomic_json,
    fingerprint,
    sha256_file,
    validate_build_paths,
    validate_config,
    apply_config,
    within,
    component_records,
    resolve_components,
)
from .catalog import CatalogCache, scan_identity

VERSION = "WinSlim Studio 4.0.7"
PENDING = {}
PROTECTED = {
    "RpcSs",
    "DcomLaunch",
    "RpcEptMapper",
    "PlugPlay",
    "EventLog",
    "Winmgmt",
    "BFE",
    "mpssvc",
    "CryptSvc",
    "Schedule",
    "ProfSvc",
    "UserManager",
    "Power",
    "Dhcp",
    "Dnscache",
    "NlaSvc",
    "nsi",
    "WlanSvc",
    "W32Time",
    "WinDefend",
    "WdNisSvc",
    "SecurityHealthService",
    "wuauserv",
    "BITS",
    "TrustedInstaller",
}
SERVICE_LIST = [
    ("SysMain", "Precaricamento applicazioni"),
    ("WSearch", "Indicizzazione ricerca"),
    ("DiagTrack", "Telemetria"),
    ("MapsBroker", "Mappe scaricate"),
    ("Fax", "Fax"),
    ("Spooler", "Stampa: disabilitando perdi stampanti e PDF"),
    ("RemoteRegistry", "Registro remoto"),
    ("WerSvc", "Segnalazione errori"),
    ("WMPNetworkSvc", "Condivisione Windows Media"),
    ("RetailDemo", "Demo negozio"),
]
MODES = {"Invariato": None, "Automatico": 2, "Manuale": 3, "Disabilitato": 4}
UI_RULES = {
    "transparency": (
        "Disattiva trasparenze",
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "EnableTransparency",
                "REG_DWORD",
                0,
            )
        ],
    ),
    "animations": (
        "Riduci animazioni",
        [
            ("NTUSER", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "REG_SZ", "0"),
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "TaskbarAnimations",
                "REG_DWORD",
                0,
            ),
        ],
    ),
    "extensions": (
        "Mostra estensioni dei file",
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "HideFileExt",
                "REG_DWORD",
                0,
            )
        ],
    ),
    "hidden": (
        "Mostra file nascosti",
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "Hidden",
                "REG_DWORD",
                1,
            )
        ],
    ),
    "dark": (
        "Tema scuro",
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "AppsUseLightTheme",
                "REG_DWORD",
                0,
            ),
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "SystemUsesLightTheme",
                "REG_DWORD",
                0,
            ),
        ],
    ),
    "search": (
        "Nascondi ricerca sulla barra",
        [
            (
                "NTUSER",
                r"Software\Microsoft\Windows\CurrentVersion\Search",
                "SearchboxTaskbarMode",
                "REG_DWORD",
                0,
            )
        ],
    ),
}


def check_config(cfg, app):
    variables = {
        k: (bool if isinstance(v, tk.BooleanVar) else str)
        for k, v in app.__dict__.items()
        if k.startswith("v_") and isinstance(v, tk.Variable) and k != "v_pwd"
    }
    return validate_config(
        cfg,
        variables,
        {"appx": app.appx_vars, "caps": app.cap_vars, "tweaks": app.tw_vars},
        UI_RULES,
        PROTECTED,
    )


def psq(s):
    return "'" + str(s).replace("'", "''") + "'"


def lines(text):
    return [x.strip() for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")]


def validate_extra(e):
    validate_config({"schema": 2, "extra": e}, {}, {}, UI_RULES, PROTECTED)
    if not isinstance(e, dict):
        raise ValueError("Configurazione avanzata non valida.")
    for name in ("drivers", "updates", "features", "capabilities", "packages"):
        if not isinstance(e.get(name, []), list) or any(
            not isinstance(x, str) for x in e.get(name, [])
        ):
            raise ValueError("Lista non valida: " + name)
    for path in e.get("drivers", []):
        if not os.path.isdir(path) and not (os.path.isfile(path) and path.lower().endswith(".inf")):
            raise ValueError("Driver: scegli una cartella o un file INF: " + path)
    for path in e.get("updates", []):
        if not os.path.isfile(path) or not path.lower().endswith((".msu", ".cab")):
            raise ValueError("Aggiornamento CAB/MSU mancante: " + path)
    for kind in ("features", "capabilities", "packages"):
        for name in e.get(kind, []):
            if not re.fullmatch(r"[A-Za-z0-9_.~\-]+", name):
                raise ValueError("Nome componente non valido: " + name)
    services = e.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("Servizi non validi.")
    for name, mode in services.items():
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name) or mode not in (2, 3, 4):
            raise ValueError("Servizio non valido: " + name)
        if name.lower() in {x.lower() for x in PROTECTED}:
            raise ValueError("Servizio essenziale protetto: " + name)
    if any(k not in UI_RULES or not isinstance(v, bool) for k, v in e.get("ui", {}).items()):
        raise ValueError("Opzioni UI non valide.")
    if e.get("power") and (
        not os.path.isfile(e["power"]) or not e["power"].lower().endswith(".pow")
    ):
        raise ValueError("Piano energetico POW mancante.")
    for item in e.get("software", []):
        if (
            not isinstance(item, dict)
            or not os.path.isfile(item.get("path", ""))
            or not item["path"].lower().endswith((".exe", ".msi"))
        ):
            raise ValueError("Installer EXE/MSI mancante.")
        args = item.get("args", [])
        if not isinstance(args, list) or any(
            not isinstance(x, str) or any(c in x for c in "\r\n\x00") for x in args
        ):
            raise ValueError("Gli argomenti devono essere un array JSON di stringhe.")
    return e


def inventory(builder, path):
    result = {}
    for label, args in [
        ("features", ["/Get-Features"]),
        ("capabilities", ["/Get-Capabilities"]),
        ("packages", ["/Get-Packages"]),
        ("drivers", ["/Get-Drivers"]),
        ("appx", ["/Get-ProvisionedAppxPackages"]),
    ]:
        _, out = builder.run(["dism.exe", "/English", "/Image:" + path] + args, quiet=True)
        result[label] = out
    return result


def inventory_diff(a, b):
    result = {}
    for key in sorted(set(a) | set(b)):
        # Pair each identity with its state; localized prose and footer noise are excluded.
        def records(raw):
            d = {}
            name = None
            for line in raw:
                if ":" not in line:
                    continue
                k, v = [x.strip() for x in line.split(":", 1)]
                if k in (
                    "Feature Name",
                    "Capability Identity",
                    "Package Identity",
                    "Published Name",
                    "DisplayName",
                ):
                    name = v
                    d[name] = {}
                elif name and k in (
                    "State",
                    "Original File Name",
                    "Provider Name",
                    "Class Name",
                    "Version",
                    "Date",
                    "PackageName",
                ):
                    d[name][k] = v
            return d

        x, y = records(a.get(key, [])), records(b.get(key, []))
        result[key] = {
            "removed": sorted(set(x) - set(y)),
            "added": sorted(set(y) - set(x)),
            "changed": {
                k: {"before": x[k], "after": y[k]} for k in x.keys() & y.keys() if x[k] != y[k]
            },
        }
    return result


_original_init = BaseBuilder.__init__
_original_preflight = BaseBuilder.preflight
_original_mount = BaseBuilder.mount
_original_dismount = BaseBuilder.dismount
_original_iso = BaseBuilder.step_make_iso


def builder_init(self, cfg, log):
    _original_init(self, cfg, log)
    self.extra = json.loads(json.dumps(cfg.get("extra", PENDING)))
    self.plus_report = {
        "version": VERSION,
        "editions": [],
        "boot_drivers": [],
        "configuration": redact(dict(cfg, extra=self.extra)),
    }
    self.plus_index = None
    self.plus_before = {}
    self.check_cancel = lambda: cancelled(self)
    self.source_fingerprint = None


def redact(cfg):
    out = json.loads(json.dumps(cfg))
    for k in ("pwd", "password"):
        out.pop(k, None)
    for k in ("variables",):
        if k in out:
            out[k].pop("v_pwd", None)
    return out


def extra_preflight(self):
    validate_extra(self.extra)
    validate_build_paths(self.c, self.root)
    src = os.path.normcase(os.path.abspath(self.c["iso"]))
    out = os.path.normcase(os.path.abspath(self.c["out"]))
    if src == out:
        raise BuildError("La ISO di uscita deve essere diversa dalla sorgente.")
    work = os.path.normcase(os.path.abspath(self.root))
    inputs = (
        [self.c["iso"]]
        + self.extra.get("drivers", [])
        + self.extra.get("updates", [])
        + [x["path"] for x in self.extra.get("software", [])]
        + ([self.extra["power"]] if self.extra.get("power") else [])
        + self.c.get("regfiles", [])
        + [x["src"] for x in self.c.get("files", [])]
    )
    for path in inputs + [self.c["out"]]:
        if within(work, path):
            raise BuildError(
                "Sorgenti e uscita devono essere fuori dalla cartella di lavoro: " + path
            )
    _original_preflight(self)
    if self.source_fingerprint is None:
        self.source_fingerprint = fingerprint(self.c["iso"])
    elif fingerprint(self.c["iso"]) != self.source_fingerprint:
        raise BuildError("La ISO sorgente è cambiata: rileggi le edizioni.")


def extended_mount(self, img, idx, path):
    _original_mount(self, img, idx, path)
    if path == self.mnt:
        self.plus_index = idx
        self.plus_before = inventory(self, path)
        # Validate before edits unless updates may introduce the requested components.
        if not self.extra.get("updates"):
            resolve_components(self.extra, self.plus_before, self.log, [p for _, p, _ in CAPS])


def service_changes(self):
    services = self.extra.get("services", {})
    if not services:
        return
    self.load_hives(self.mnt, ["SYSTEM"])
    try:
        # Discover all real control sets rather than assuming ControlSet001.
        data = json.dumps(services)
        script = (
            "$ErrorActionPreference='Stop'; $base='Registry::HKEY_LOCAL_MACHINE\\WS_SYSTEM'; $changes=ConvertFrom-Json "
            + psq(data)
            + "; $sets=@(Get-ChildItem $base | Where-Object {$_.PSChildName -match '^ControlSet[0-9]{3}$'}); if (!$sets.Count) {throw 'ControlSet assente'}; foreach($set in $sets) { $root=$set.PSPath+'\\Services'; foreach($p in $changes.PSObject.Properties) { $target=Join-Path $root $p.Name; if (!(Test-Path $target)) {Write-Output ('Servizio non presente, saltato: '+$p.Name+' ['+$set.PSChildName+']'); continue}; $v=Get-ItemProperty $target; if (($v.Type -band 3) -ne 0) {throw ('Driver protetto: '+$p.Name)}; if ([int]$p.Value -eq 4) { foreach($s in Get-ChildItem $root) {$dep=Get-ItemProperty $s.PSPath; if (($dep.DependOnService -contains $p.Name) -and ($dep.Start -ne 4) -and ($changes.PSObject.Properties.Name -notcontains $s.PSChildName -or $changes.($s.PSChildName) -ne 4)) {throw ('Dipendenza attiva: '+$s.PSChildName+' richiede '+$p.Name)}} }; if (Test-Path ($target+'\\StartOverride')) {throw ('StartOverride presente: '+$p.Name+'; modifica non supportata')}; Set-ItemProperty $target -Name Start -Type DWord -Value ([int]$p.Value) } }"
        )
        self.ps(script)
    finally:
        self.unload_hives()


def software_script(e):
    tasks = []
    for i, item in enumerate(e.get("software", [])):
        rel = "software/%03d%s" % (i, os.path.splitext(item["path"])[1].lower())
        tasks.append(
            {
                "relative": rel,
                "args": item.get("args", []),
                "msi": item["path"].lower().endswith(".msi"),
            }
        )
    data = json.dumps(tasks, ensure_ascii=True)
    return (
        r"""$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
 $p=Start-Process powershell.exe -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"') -Verb RunAs -Wait -PassThru
 exit $p.ExitCode
}
$root=Split-Path -Parent $PSCommandPath
$log=Join-Path $root 'installazione.log'
$done=Join-Path $root 'completed.json'
$state=@{}
if(Test-Path $done) { $obj=Get-Content $done -Raw | ConvertFrom-Json; foreach($p in $obj.PSObject.Properties){$state[$p.Name]=$p.Value} }
function Save-State { $state | ConvertTo-Json | Set-Content $done -Encoding UTF8 }
function Quote-Arg([string]$s) {
 # Windows CommandLineToArgvW escaping, including trailing backslashes.
 return '"'+([regex]::Replace([regex]::Replace($s,'(\\*)"','$1$1\"'),'(\\+)$','$1$1'))+'"'
}
$reboot=$false
try {
Start-Transcript -Path $log -Append | Out-Null
"""
        + ("$tasks=ConvertFrom-Json " + psq(data) + "\n")
        + r"""foreach($t in $tasks) {
 if($state.ContainsKey($t.relative)){continue}
 $installer=Join-Path $root $t.relative
 $args=@($t.args)
 if($t.msi) {$exe='msiexec.exe';$args=@('/i',$installer,'/qn','/norestart')+$args} else {$exe=$installer}
 $params=@{FilePath=$exe;Wait=$true;PassThru=$true}
 if($args.Count){$params.ArgumentList=(@($args | ForEach-Object {Quote-Arg ([string]$_)}) -join ' ')}
 $p=Start-Process @params
 if($p.ExitCode -notin @(0,3010)){throw ('Installer '+$t.relative+': codice '+$p.ExitCode)}
 if($p.ExitCode -eq 3010){$reboot=$true}
 $state[$t.relative]=$p.ExitCode;Save-State
}
"""
        + (
            r"""if(!$state.ContainsKey('power')) {
 $guid='722e02e1-5c45-4b9a-8b7e-72971b86a242'
 & powercfg.exe /import (Join-Path $root 'custom.pow') $guid
 if($LASTEXITCODE -ne 0){throw 'Importazione piano energetico fallita'}
 & powercfg.exe /setactive $guid
 if($LASTEXITCODE -ne 0){throw 'Attivazione piano energetico fallita'}
 $state['power']=0;Save-State
}
"""
            if e.get("power")
            else ""
        )
        + r"""Write-Output ('Completato. Riavvio richiesto: '+$reboot)
Stop-Transcript | Out-Null
exit 0
} catch {
$_ | Out-String | Add-Content $log
$retry='powershell.exe -NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"'
New-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'WinSlimPlus' -Value $retry -PropertyType String -Force | Out-Null
try {Stop-Transcript | Out-Null} catch {}
exit 1
}
"""
    )


def stage_first_login(self):
    e = self.extra
    if not e.get("software") and not e.get("power"):
        return
    root = os.path.join(self.mnt, "ProgramData", "WinSlimPlus")
    os.makedirs(os.path.join(root, "software"), exist_ok=True)
    for i, item in enumerate(e.get("software", [])):
        shutil.copy2(
            item["path"],
            os.path.join(
                root, "software", "%03d%s" % (i, os.path.splitext(item["path"])[1].lower())
            ),
        )
    if e.get("power"):
        shutil.copy2(e["power"], os.path.join(root, "custom.pow"))
    with open(os.path.join(root, "FirstLogon.ps1"), "w", encoding="utf-8-sig") as f:
        f.write(software_script(e))
    self.apply_entries(
        self.mnt,
        [
            (
                "SOFTWARE",
                r"Microsoft\Windows\CurrentVersion\RunOnce",
                "WinSlimPlus",
                "REG_EXPAND_SZ",
                r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ProgramData%\WinSlimPlus\FirstLogon.ps1"',
            )
        ],
        [],
        False,
    )


def extended_dismount(self, path, save=True):
    if save and path == self.mnt:
        e = self.extra
        for driver in e.get("drivers", []):
            args = ["dism.exe", "/English", "/Image:" + path, "/Add-Driver", "/Driver:" + driver]
            if os.path.isdir(driver):
                args.append("/Recurse")
            self.run(args, ok=(0, 3010))
        for package in e.get("updates", []):
            self.run(
                [
                    "dism.exe",
                    "/English",
                    "/Image:" + path,
                    "/Add-Package",
                    "/PackagePath:" + package,
                    "/NoRestart",
                ],
                ok=(0, 3010),
            )
        current = {}
        for field, option in [
            ("features", "/Get-Features"),
            ("capabilities", "/Get-Capabilities"),
            ("packages", "/Get-Packages"),
        ]:
            if e.get(field):
                _, current[field] = self.run(
                    ["dism.exe", "/English", "/Image:" + path, option], quiet=True
                )
        components, decisions = resolve_components(e, current, self.log, [p for _, p, _ in CAPS])
        for name in components["features"]:
            self.run(
                [
                    "dism.exe",
                    "/English",
                    "/Image:" + path,
                    "/Disable-Feature",
                    "/FeatureName:" + name,
                    "/Remove",
                    "/NoRestart",
                ],
                ok=(0, 3010),
            )
        for name in components["capabilities"]:
            self.run(
                [
                    "dism.exe",
                    "/English",
                    "/Image:" + path,
                    "/Remove-Capability",
                    "/CapabilityName:" + name,
                    "/NoRestart",
                ],
                ok=(0, 3010),
            )
        for name in components["packages"]:
            self.run(
                [
                    "dism.exe",
                    "/English",
                    "/Image:" + path,
                    "/Remove-Package",
                    "/PackageName:" + name,
                    "/NoRestart",
                ],
                ok=(0, 3010),
            )
        service_changes(self)
        entries = []
        for key, on in e.get("ui", {}).items():
            if on:
                entries.extend(UI_RULES[key][1])
        if entries:
            self.apply_entries(path, entries, [], False)
        stage_first_login(self)
        after = inventory(self, path)
        self.plus_report["editions"].append(
            {
                "index": self.plus_index,
                "original_index": self.c["indexes"][self.plus_index - 1],
                "name": self.c["names"][self.plus_index - 1],
                "before": self.plus_before,
                "after": after,
                "difference": inventory_diff(self.plus_before, after),
                "component_resolution": decisions,
            }
        )
    _original_dismount(self, path, save)


def boot_drivers(self):
    if not self.extra.get("drivers_boot") or not self.extra.get("drivers"):
        return
    boot = os.path.join(self.isodir, "sources", "boot.wim")
    _, out = self.run(["dism.exe", "/English", "/Get-WimInfo", "/WimFile:" + boot], quiet=True)
    indexes = [int(m.group(1)) for line in out if (m := re.match(r"\s*Index\s*:\s*(\d+)", line))]
    if not indexes:
        raise BuildError("Indici boot.wim non rilevati.")
    for idx in indexes:
        self.mount(boot, idx, self.bmnt)
        try:
            for driver in self.extra["drivers"]:
                args = [
                    "dism.exe",
                    "/English",
                    "/Image:" + self.bmnt,
                    "/Add-Driver",
                    "/Driver:" + driver,
                ]
                if os.path.isdir(driver):
                    args.append("/Recurse")
                self.run(args, ok=(0, 3010))
            self.dismount(self.bmnt, True)
            self.plus_report["boot_drivers"].append(idx)
        except BaseException:
            if self.bmnt in self.mounted:
                self.dismount(self.bmnt, False)
            raise


def extended_iso(self):
    boot_drivers(self)
    source_sha256 = sha256_file(self.c["iso"], getattr(self, "cancel_event", None))
    if self.source_fingerprint != fingerprint(self.c["iso"]):
        raise BuildError("La sorgente è cambiata durante la lavorazione.")
    _original_iso(self)
    report = self.plus_report
    report["iso"] = {
        "source": self.c["iso"],
        "output": self.c["out"],
        "source_bytes": os.path.getsize(self.c["iso"]),
        "output_bytes": os.path.getsize(self.c["out"]),
    }
    report["iso"]["source_sha256"] = source_sha256
    report["iso"]["output_sha256"] = self.output_sha256
    report["iso"]["delta_bytes"] = report["iso"]["output_bytes"] - report["iso"]["source_bytes"]
    try:
        atomic_json(self.c["out"] + ".report.json", report)
    except OSError as e:
        raise BuildError(
            "ISO creata in " + self.c["out"] + ", ma il report non è stato salvato: " + str(e)
        ) from e
    self.log("Report confronto: " + self.c["out"] + ".report.json")


_BaseApp = BaseApp


class ProjectActions(_BaseApp):
    def refresh_list(self, key):
        lb = getattr(self, "plus_lb_" + key)
        lb.delete(0, "end")
        for x in self.plus_lists[key]:
            lb.insert("end", x if isinstance(x, str) else x["path"] + "  " + json.dumps(x["args"]))

    def component_requested(self, kind, name):
        if name in self.component_choices[kind]:
            return True
        return kind == "capabilities" and any(
            name.split("~", 1)[0].casefold() == prefix.casefold() and self.cap_vars[label].get()
            for label, prefix, _ in CAPS
        )

    def set_component_choice(self, remove=True):
        selected = [self.scan_tree.item(item, "values")[:2] for item in self.scan_tree.selection()]
        if not selected:
            self.status_var.set("Seleziona uno o più componenti nel catalogo")
            return
        changes = [
            (kind, name)
            for kind, name in selected
            if kind in self.component_choices and self.component_requested(kind, name) != remove
        ]
        if not changes:
            return
        self.checkpoint()
        for kind, name in changes:
            if remove:
                self.component_choices[kind].append(name)
            else:
                if name in self.component_choices[kind]:
                    self.component_choices[kind].remove(name)
                if kind == "capabilities":
                    for label, prefix, _ in CAPS:
                        if name.split("~", 1)[0].casefold() == prefix.casefold():
                            self.cap_vars[label].set(False)
        self.filter_components()
        self.refresh_summary()
        self.status_var.set("Scelte aggiornate nel catalogo")

    def component_shortcut(self, remove):
        self.action(lambda: self.set_component_choice(remove))
        return "break"

    def extra_config(self):
        services = {
            n: MODES[v.get()] for n, v in self.service_vars.items() if MODES[v.get()] is not None
        }
        for row in lines(self.plus_text["services"].get("1.0", "end")):
            n, sep, mode = row.partition("=")
            if not sep or mode.strip() not in MODES or MODES[mode.strip()] is None:
                raise ValueError("Servizio: usa Nome=Manuale / Automatico / Disabilitato.")
            services[n.strip()] = MODES[mode.strip()]
        return dict(
            self.plus_lists,
            services=services,
            ui={k: v.get() for k, v in self.ui_vars.items()},
            power=self.v_power.get().strip(),
            drivers_boot=self.v_bootdrivers.get(),
            **{
                k: list(self.component_choices[k]) for k in ("features", "capabilities", "packages")
            },
        )

    def config(self):
        variables = {
            k: v.get()
            for k, v in self.__dict__.items()
            if k.startswith("v_") and isinstance(v, tk.Variable) and k != "v_pwd"
        }
        return {
            "schema": 2,
            "version": VERSION,
            "variables": variables,
            "editions": self.editions,
            "selected_indexes": [e[0] for e in self.selected_editions()],
            "appx": {k: v.get() for k, v in self.appx_vars.items()},
            "caps": {k: v.get() for k, v in self.cap_vars.items()},
            "tweaks": {k: v.get() for k, v in self.tw_vars.items()},
            "regfiles": list(self.lb.get(0, "end")),
            "files": self.files,
            "extra": self.extra_config(),
        }

    def export_config(self):
        cfg = self.config()
        p = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile="WinSlim-config.json",
            filetypes=[("JSON", "*.json")],
        )
        if p:
            atomic_json(p, cfg)
            self.log("Configurazione salvata: " + p)

    def import_config(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Configurazione", "*.json")])
        if not p:
            return
        if os.path.getsize(p) > 4 * 1024 * 1024:
            raise ValueError("Configurazione troppo grande (massimo 4 MB).")
        with open(p, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        check_config(cfg, self)
        self.checkpoint()
        apply_config(self, cfg)
        self.log("Configurazione importata. Rileggi le edizioni; password esclusa.")


# PowerShell 5.1: statements separated by ';' cannot be used inside an
# ordinary parenthesized boolean operand. Enumerate explicitly instead.
def remove_caps_fixed(self):
    prefixes = [prefix.casefold() for label, prefix, _ in CAPS if label in self.c.get("caps", [])]
    if not prefixes:
        self.log("   (nessun componente selezionato)")
        return
    _, output = self.run(
        ["dism.exe", "/English", "/Image:" + self.mnt, "/Get-Capabilities"], quiet=True
    )
    records = component_records(output, "capabilities")
    found = 0
    for name, state in records.items():
        if state.casefold() != "installed":
            continue
        if not any(
            name.casefold() == prefix or name.casefold().startswith(prefix + "~")
            for prefix in prefixes
        ):
            continue
        self.run(
            [
                "dism.exe",
                "/English",
                "/Image:" + self.mnt,
                "/Remove-Capability",
                "/CapabilityName:" + name,
                "/NoRestart",
            ],
            ok=(0, 3010),
        )
        self.log("Componente standard rimosso: " + name)
        found += 1
    if not found:
        self.log(
            "Componenti standard selezionati già assenti o non disponibili in questa edizione."
        )


"""WinSlim Studio: desktop interface and observable, cancellable workflows."""
VERSION = "WinSlim Studio 4.0.7"
UI_FONT = "Segoe UI" if IS_WIN else "Helvetica"
MONO_FONT = "Consolas" if IS_WIN else "Courier"
SCRIPT_FONT = "Segoe Script" if IS_WIN else "URW Chancery L"
COLORS = {
    "bg": "#17181D",
    "side": "#131419",
    "card": "#22232B",
    "field": "#1B1C23",
    "line": "#343540",
    "text": "#EEEFF5",
    "muted": "#A2A4B5",
    "accent": "#91BFFF",
    "accent_bg": "#283B56",
    "green": "#97D9BC",
    "amber": "#E7C393",
    "red": "#ED9DAA",
}
PAGES = [
    ("source", "01", "Sorgente", "Scegli la ISO e le edizioni da mantenere."),
    ("apps", "02", "App e componenti", "Rimuovi solo quello che hai scelto."),
    ("services", "03", "Servizi", "Configura l’avvio dei servizi nell’immagine."),
    ("integrations", "04", "Integrazioni", "Driver, aggiornamenti e software in un unico spazio."),
    ("personal", "05", "Personalizzazione", "Impostazioni Windows, registro e piano energetico."),
    ("install", "06", "Installazione", "Account, lingua e file da includere."),
    ("review", "07", "Riepilogo", "Controlla tutte le modifiche prima di creare."),
    ("build", "08", "Creazione", "Segui il lavoro e consulta la diagnostica."),
]


def rounded_shape(canvas, x, y, w, h, r, **options):
    r = min(r, w / 2, h / 2)
    return canvas.create_polygon(
        x + r,
        y,
        x + w - r,
        y,
        x + w,
        y,
        x + w,
        y + r,
        x + w,
        y + h - r,
        x + w,
        y + h,
        x + w - r,
        y + h,
        x + r,
        y + h,
        x,
        y + h,
        x,
        y + h - r,
        x,
        y + r,
        x,
        y,
        smooth=True,
        splinesteps=24,
        **options,
    )


class SoftEntry(ttk.Entry):
    def __init__(self, parent, **kw):
        try:
            bg = parent.cget("background")
        except tk.TclError:
            bg = (
                ttk.Style(parent).lookup(parent.cget("style") or "TFrame", "background")
                or COLORS["card"]
            )
        self.shell = tk.Canvas(parent, height=40, width=180, bg=bg, bd=0, highlightthickness=0)
        super().__init__(self.shell, **kw)
        self.window = self.shell.create_window(12, 20, window=self, anchor="w")
        self.shell.bind("<Configure>", lambda e: self.redraw())
        self.bind("<FocusIn>", lambda e: self.redraw())
        self.bind("<FocusOut>", lambda e: self.redraw())

    def redraw(self):
        w = max(1, self.shell.winfo_width())
        self.shell.itemconfigure(self.window, width=max(1, w - 24))
        self.shell.delete("surface")
        rounded_shape(
            self.shell,
            1,
            1,
            w - 2,
            38,
            16,
            fill=COLORS["field"],
            outline=COLORS["accent"] if self.focus_get() == self else COLORS["line"],
            tags="surface",
        )
        self.shell.tag_lower("surface")

    def pack(self, *args, **kw):
        return self.shell.pack(*args, **kw)

    def grid(self, *args, **kw):
        return self.shell.grid(*args, **kw)


class SoftButton(tk.Canvas):
    def __init__(self, parent, text, command, accent=False, fill=None, foreground=None, left=False):
        self.caption = text
        self.command = command
        self.accent = accent
        self.left = left
        self.fill = fill or (COLORS["accent"] if accent else COLORS["line"])
        self.foreground = foreground or ("#201A30" if accent else COLORS["text"])
        self.disabled = False
        self.hover = False
        self.pressed = False
        try:
            background = parent.cget("background")
        except tk.TclError:
            background = (
                ttk.Style(parent).lookup(parent.cget("style") or "TFrame", "background")
                or COLORS["card"]
            )
        width = int(parent.tk.call("font", "measure", "TkDefaultFont", text)) + 36
        super().__init__(
            parent,
            width=width,
            height=40,
            bg=background,
            bd=0,
            highlightthickness=0,
            takefocus=True,
            cursor="hand2",
        )
        self.bind("<Configure>", lambda e: self.paint())
        self.bind("<Enter>", lambda e: self.interact(hover=True))
        self.bind("<Leave>", lambda e: self.interact(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self.interact(pressed=True))
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<FocusIn>", lambda e: self.paint())
        self.bind("<FocusOut>", lambda e: self.paint())
        self.paint()

    def interact(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)
        self.paint()

    def release(self, event):
        active = self.pressed
        self.pressed = False
        self.paint()
        if active and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.focus_set()
            self.invoke()

    def invoke(self):
        if not self.disabled and self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        for key, attr in [
            ("state", "disabled"),
            ("text", "caption"),
            ("command", "command"),
            ("bg", "fill"),
            ("fg", "foreground"),
        ]:
            if key in kw:
                value = kw.pop(key)
                setattr(self, attr, value == "disabled" if key == "state" else value)
        result = super().configure(**kw) if kw else None
        if hasattr(self, "disabled"):
            self.paint()
        return result

    config = configure

    def paint(self):
        self.delete("all")
        w = max(self.winfo_width(), int(self.cget("width")))
        h = int(self.cget("height"))
        fill = self.fill
        fg = self.foreground
        if self.disabled:
            fill = COLORS["field"]
            fg = "#686A7B"
        elif self.pressed:
            fill = COLORS["accent_bg"] if not self.accent else "#9C87DF"
        elif self.hover:
            fill = "#C8B8FF" if self.accent else "#454452"
        outline = COLORS["accent"] if self.focus_get() == self and not self.disabled else fill
        rounded_shape(self, 1, 1, w - 2, h - 2, 20, fill=fill, outline=outline, width=1)
        self.create_text(
            16 if self.left else w / 2,
            h / 2,
            text=self.caption,
            anchor="w" if self.left else "center",
            fill=fg,
            font=(UI_FONT, 10, "bold" if self.accent else "normal"),
        )


class SoftPanel(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=COLORS["bg"], height=1, bd=0, highlightthickness=0)
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.window = self.create_window(22, 20, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.fit())
        self.bind("<Configure>", lambda e: self.fit())

    def fit(self):
        w = max(self.winfo_width(), 80)
        h = self.body.winfo_reqheight() + 40
        self.itemconfigure(self.window, width=max(1, w - 44))
        if int(self.cget("height")) != h:
            self.configure(height=h)
        self.delete("surface")
        rounded_shape(self, 0, 0, w, h, 32, fill=COLORS["card"], outline="", tags="surface")
        self.tag_lower("surface")


class BuildCancelled(BuildError):
    pass


def cancelled(builder):
    event = getattr(builder, "cancel_event", None)
    if event and event.is_set() and not getattr(builder, "_cleaning", False):
        raise BuildCancelled("Operazione annullata. Le immagini montate verranno scartate.")


def run_observable(self, cmd, ok=(0,), quiet=False):
    cancelled(self)
    cmd = list(cmd)
    import ntpath

    is_dism = ntpath.basename(cmd[0]).casefold() == "dism.exe"
    if is_dism:
        cmd[0] = self.dism
        if self.dism_log and not any(a.casefold().startswith("/logpath:") for a in cmd[1:]):
            cmd.append("/LogPath:" + self.dism_log)
        if IS_WIN:
            cmd = dism_command(cmd, self.dism)
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding=("utf-8" if "powershell" in os.path.basename(cmd[0]).lower() else ENC),
        errors="replace",
        creationflags=NOWIN,
    )
    output = []
    for raw in p.stdout:
        line = raw.rstrip()
        if not line:
            continue
        output.append(line)
        if not quiet:
            self.log("   " + line)
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*%", line)
        if m and hasattr(self, "on_unit_progress"):
            self.on_unit_progress(min(100, float(m.group(1))))
    p.stdout.close()
    rc = p.wait()
    if ok is not None and rc not in ok:
        detail = "\n".join(output[-18:])
        if quiet and detail:
            self.log(detail)
        if is_dism and self.dism_log:
            self.log("Log DISM della lavorazione: " + self.dism_log)
            try:
                with open(self.dism_log, "rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(0)
                    header = stream.read(2)
                    stream.seek(max(0, size - 128 * 1024))
                    tail = stream.read()
                encoding = (
                    "utf-16-le"
                    if header == b"\xff\xfe"
                    else "utf-16-be"
                    if header == b"\xfe\xff"
                    else "utf-8"
                )
                for line in tail.decode(encoding, errors="replace").splitlines()[-40:]:
                    self.log("DISM: " + line)
            except OSError as error:
                self.log("Log DISM non disponibile: " + str(error))
        raise BuildError(
            "Comando fallito (codice %s): %s\n%s"
            % (rc, subprocess.list2cmdline(list(map(str, cmd))), detail)
        )
    return rc, output


def cleanup_observable(self):
    self._cleaning = True
    try:
        self.log("Pulizia delle immagini di questa lavorazione…")
        try:
            self.unload_hives()
        except Exception as e:
            self.log("Scaricamento hive non riuscito: " + str(e))
        for mount_path in list(self.mounted):
            try:
                self.dismount(mount_path, save=False)
            except Exception as e:
                self.log(
                    "Smontaggio non riuscito: "
                    + mount_path
                    + " • "
                    + str(e)
                    + " • conserva la cartella di lavoro per il recupero."
                )
    finally:
        self._cleaning = False


# Log extra work before each image commit, where the extension is applied.
_old_extended_dismount = extended_dismount


def dismount_observable(self, path, save=True):
    if save and path == self.mnt:
        self.log("   Integrazioni e impostazioni avanzate, poi salvataggio immagine…")
    return _old_extended_dismount(self, path, save)


class Builder(BaseBuilder):
    """The complete Windows backend, extending the independently testable core."""

    __init__ = builder_init
    preflight = extra_preflight
    mount = extended_mount
    dismount = dismount_observable
    step_make_iso = extended_iso
    step_remove_caps = remove_caps_fixed
    run = run_observable
    cleanup_on_error = cleanup_observable


def configuration_summary(cfg):
    e = cfg.get("extra", {})
    v = cfg.get("variables", {})
    rows = []

    def row(area, name, action):
        rows.append((area, str(name), str(action)))

    for ed in cfg.get("editions", []):
        if ed[0] in cfg.get("selected_indexes", []):
            row("Edizione", ed[1], "Mantieni e personalizza")
    for key, area in [("appx", "App"), ("caps", "Componente"), ("tweaks", "Impostazione")]:
        for name, on in cfg.get(key, {}).items():
            display = (
                next((x[1] for x in TWEAKS if x[0] == name), name) if key == "tweaks" else name
            )
            if on:
                row(area, display, "Rimuovi" if key != "tweaks" else "Applica")
    for k, label in [
        ("v_onedrive", "OneDrive"),
        ("v_recall", "Recall"),
        ("v_defender", "Windows Defender"),
    ]:
        if v.get(k):
            row("Sistema", label, "Rimuovi")
    if v.get("v_bypass"):
        row("Installazione", "Requisiti hardware Windows 11", "Bypass")
    for path in e.get("drivers", []):
        row("Driver", path, "Integra" + (" anche in boot.wim" if e.get("drivers_boot") else ""))
    for path in e.get("updates", []):
        row("Aggiornamento", path, "Integra nell’ordine indicato")
    for key, area in [
        ("features", "Feature"),
        ("capabilities", "Capability"),
        ("packages", "Pacchetto"),
    ]:
        for name in e.get(key, []):
            row(area, name, "Rimuovi")
    for name, mode in e.get("services", {}).items():
        row("Servizio", name, {2: "Automatico", 3: "Manuale", 4: "Disabilitato"}.get(mode, mode))
    for name, on in e.get("ui", {}).items():
        if on:
            row("Interfaccia", UI_RULES[name][0], "Applica ai nuovi utenti")
    for item in e.get("software", []):
        row(
            "Software",
            item["path"],
            "Primo accesso admin: " + json.dumps(item["args"], ensure_ascii=False),
        )
    if e.get("power"):
        row("Energia", e["power"], "Importa e attiva al primo accesso admin")
    for path in cfg.get("regfiles", []):
        row("Registro", path, "Importa nell’immagine")
    for item in cfg.get("files", []):
        row("File extra", item["src"], item["where"] + ": " + item["dest"])
    if v.get("v_unatt"):
        row("Installazione", "Account e lingua", "Automatica • password esclusa dal report")
    if v.get("v_wipe") and v.get("v_unatt"):
        row("Disco 0", "Partizionamento automatico", "Cancella tutto sul PC di destinazione")
    return rows


def human_size(n):
    return "%.1f GB" % (n / 1073741824) if n >= 1073741824 else "%.0f MB" % (n / 1048576)


def describe_error(err):
    s = str(err)
    low = s.lower()
    if "oscdimg" in low and ("non trovato" in low or "not found" in low):
        return (
            "Manca lo strumento per creare la ISO",
            "Installa “Strumenti di distribuzione” del Windows ADK, oppure scegli oscdimg.exe nella pagina Sorgente.",
        )
    if "componente non presente nel catalogo" in low or "nome componente ambiguo" in low:
        return (
            "Selezione componenti da aggiornare",
            "Il JSON contiene un nome non valido per questa edizione. Riscansiona la ISO e sostituisci la voce indicata usando il nome completo del catalogo.",
        )
    if "windows capability name was not recognized" in low:
        return (
            "DISM non riconosce il componente",
            "Controlla il CapabilityName completo nel log. Se è nel catalogo, usa una versione di Windows/Windows ADK compatibile con la build della ISO.",
        )
    if "/mount-image" in low:
        return (
            "Montaggio immagine non riuscito",
            "Esporta il log operativo: include il comando completo e gli ultimi dettagli del log DISM della lavorazione. La ISO sorgente è invariata; usa una nuova cartella di lavoro per riprovare.",
        )
    if "missingendparenthesis" in low or "parsererror" in low:
        return (
            "Errore nello script PowerShell",
            "Il log contiene la riga esatta. Esporta il log diagnostico per correggere il comando.",
        )
    if "spazio" in low or "disk space" in low:
        return (
            "Spazio disponibile insufficiente",
            "Scegli un disco con spazio per la ISO, le immagini montate e l’uscita.",
        )
    if "0x800f081f" in low:
        return (
            "File sorgente del componente mancanti",
            "Il componente richiesto non è disponibile nell’immagine. Controlla build e sorgenti.",
        )
    if "0x80070005" in low or "access is denied" in low:
        return (
            "Accesso negato",
            "Avvia come amministratore e verifica le autorizzazioni della cartella di lavoro.",
        )
    if isinstance(err, BuildCancelled):
        return "Creazione annullata", "La richiesta è stata gestita al termine del comando attivo."
    return (
        "Operazione non completata",
        "Consulta il dettaglio nel log. La ISO sorgente rimane invariata.",
    )


class App(ProjectActions):
    def __init__(self):
        self.operation = None
        self.cancel_event = threading.Event()
        self.pending_close = False
        self.scan_cache = {}
        self.catalog_cache = CatalogCache(
            os.path.join(
                os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "WinSlim", "Cache", "Catalog"
            )
        )
        self.scan_records = []
        self.logs = []
        self.operation_started = None
        self.current_page = "source"
        self.last_output = ""
        self.last_report = ""
        self.last_editions_iso = ""
        self.loading_editions = False
        self.editions_fingerprint = None
        self.undo_stack = []
        self.default_config = None
        self.validation_errors = []
        self.operation_warning = ""
        self.done_phases = 0
        self.total_phases = 0
        self.session_log = None
        self.log_file = None
        _BaseApp.__init__(self)
        self.title(VERSION + "  |  Windows ISO Builder")
        self.app_icon = tk.PhotoImage(width=64, height=64)
        self.app_icon.put(COLORS["side"], to=(0, 0, 64, 64))
        for y in range(16, 48):
            for x in range(12, 52):
                if (
                    abs(x - (13 + (y - 16) * 0.3)) < 3
                    or abs(x - (32 - (y - 16) * 0.3)) < 3
                    or abs(x - (32 + (y - 16) * 0.3)) < 3
                    or abs(x - (51 - (y - 16) * 0.3)) < 3
                ):
                    self.app_icon.put(COLORS["accent"], to=(x, y, x + 1, y + 1))
        self.iconphoto(True, self.app_icon)
        self.geometry(
            "%dx%d"
            % (min(1280, self.winfo_screenwidth() - 60), min(920, self.winfo_screenheight() - 80))
        )
        self.minsize(
            min(1050, self.winfo_screenwidth() - 60), min(720, self.winfo_screenheight() - 80)
        )
        self.apply_preset("Stock", initial=True)
        self.default_config = json.loads(json.dumps(self.config()))
        try:
            base = os.path.join(
                os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "WinSlim", "Logs"
            )
            os.makedirs(base, exist_ok=True)
            self.log_file = os.path.join(base, time.strftime("WinSlim_%Y%m%d_%H%M%S") + ".log")
            self.session_log = open(self.log_file, "a", encoding="utf-8", buffering=1)
        except OSError:
            pass
        self.bind("<Control-s>", lambda e: self.action(self.export_config))
        self.bind("<Control-o>", lambda e: self.action(self.import_config))
        self.bind("<Control-z>", lambda e: self.action(self.undo_config))
        self.bind("<F5>", lambda e: self.action(self.validate_ui))
        self.v_iso.trace_add("write", self.iso_changed)
        self.after(100, self.refresh_summary)
        self.log(
            "WinSlim Studio pronto. Configura una ISO, controlla il riepilogo e avvia la creazione."
        )

    def checkpoint(self):
        self.undo_stack.append(json.loads(json.dumps(self.config())))
        del self.undo_stack[:-10]

    def undo_config(self):
        if not self.undo_stack:
            self.status_var.set("Nessuna configurazione precedente")
            return
        apply_config(self, self.undo_stack.pop())
        self.profile_var.set("Scelte ripristinate · rileggi le edizioni")
        self.status_var.set("Ultima configurazione ripristinata")

    def hash_source(self):
        if self.operation:
            raise ValueError("Attendi la fine dell’operazione attiva.")
        iso = self.v_iso.get()
        if not os.path.isfile(iso):
            raise ValueError("Seleziona una ISO locale.")
        self.operation = "hash"
        self.cancel_event = threading.Event()
        self.operation_started = time.monotonic()
        self.cancel_btn.configure(state="normal")
        self.status_var.set("Calcolo SHA-256 in corso")
        self.phase_var.set("Integrità ISO")
        event = self.cancel_event

        def worker():
            try:
                self.q.put(
                    (
                        "hash_ready",
                        iso
                        + "\n\nSHA-256: "
                        + sha256_file(iso, event)
                        + "\n\nConfronta il valore con quello pubblicato dal fornitore della ISO.",
                    )
                )
            except Exception as e:
                self.q.put(("hash_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def diagnostics(self):
        if self.operation:
            raise ValueError("Attendi la fine dell’operazione attiva.")
        rows = [
            "WinSlim Studio 4.0",
            "Sistema: " + sys.platform,
            "Python: " + sys.version.split()[0],
            "Amministratore: " + str(is_admin()),
        ]
        for exe in ("dism.exe", "powershell.exe", "reg.exe", "robocopy.exe"):
            rows.append(exe + ": " + (shutil.which(exe) or "non disponibile"))
        try:
            rows.append(
                "oscdimg: "
                + Builder(
                    {"work": self.v_work.get(), "oscdimg": self.v_osc.get()}, self.log
                ).find_oscdimg()
            )
        except Exception as e:
            rows.append(str(e))
        if os.path.isdir(self.v_work.get()):
            rows.append(
                "Spazio libero lavoro: " + human_size(shutil.disk_usage(self.v_work.get()).free)
            )
        if IS_WIN:
            rows.append(
                "Immagini montate: esegui dism /Get-MountedImageInfo da un terminale amministratore. Recupera soltanto il MountDir della tua lavorazione; non usare Cleanup-Wim globale."
            )
        rows.append("Non sono stati modificati dischi, servizi o registro del sistema in uso.")
        self.show_text("Diagnostica ambiente", "\n".join(rows))

    def theme(self):
        self.configure(background=COLORS["bg"])
        self.tk.call("font", "configure", "TkDefaultFont", "-family", UI_FONT, "-size", 10)
        self.option_add("*TCombobox*Listbox.background", COLORS["field"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=COLORS["card"], foreground=COLORS["text"], font=(UI_FONT, 10))
        st.configure(
            "TLabelframe",
            background=COLORS["bg"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
        )
        st.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["muted"])
        st.configure("TFrame", background=COLORS["bg"])
        st.configure("Card.TFrame", background=COLORS["card"])
        st.configure("TLabel", background=COLORS["card"], foreground=COLORS["text"])
        st.configure("Muted.TLabel", foreground=COLORS["muted"])
        st.configure("Page.TLabel", background=COLORS["bg"])
        st.configure("Title.TLabel", background=COLORS["bg"], font=(UI_FONT, 24, "bold"))
        st.configure("Heading.TLabel", font=(UI_FONT, 12, "bold"))
        st.configure(
            "TButton", background=COLORS["line"], borderwidth=0, padding=(12, 8), relief="flat"
        )
        st.map(
            "TButton",
            background=[("active", "#394252"), ("disabled", COLORS["card"])],
            foreground=[("disabled", "#667082")],
        )
        st.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#111827",
            font=(UI_FONT, 10, "bold"),
        )
        st.map(
            "Accent.TButton",
            background=[("active", "#a1bdff"), ("disabled", COLORS["line"])],
            foreground=[("disabled", COLORS["muted"])],
        )
        st.configure(
            "TEntry",
            fieldbackground=COLORS["field"],
            foreground=COLORS["text"],
            borderwidth=0,
            relief="flat",
            bordercolor=COLORS["field"],
            lightcolor=COLORS["field"],
            darkcolor=COLORS["field"],
            insertcolor=COLORS["text"],
            padding=4,
        )
        st.configure(
            "TCombobox",
            fieldbackground=COLORS["field"],
            background=COLORS["line"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            padding=6,
        )
        st.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["field"])],
            foreground=[("readonly", COLORS["text"])],
            selectbackground=[("readonly", COLORS["field"])],
        )
        st.configure(
            "TCheckbutton",
            background=COLORS["card"],
            foreground=COLORS["text"],
            padding=(0, 4),
            indicatorbackground=COLORS["field"],
        )
        st.map(
            "TCheckbutton",
            background=[("active", COLORS["card"])],
            indicatorbackground=[("selected", COLORS["accent"])],
        )
        self.check_images = []
        for selected in [False, True]:
            img = tk.PhotoImage(width=20, height=20)
            img.put(COLORS["card"], to=(0, 0, 20, 20))
            for y in range(2, 18):
                for x in range(2, 18):
                    cx = min(max(x, 6), 13)
                    cy = min(max(y, 6), 13)
                    if (x - cx) ** 2 + (y - cy) ** 2 <= 16:
                        img.put(COLORS["accent"] if selected else COLORS["line"], to=(x, y))
            if selected:
                for x, y in [
                    (6, 10),
                    (7, 11),
                    (8, 12),
                    (9, 11),
                    (10, 10),
                    (11, 9),
                    (12, 8),
                    (13, 7),
                ]:
                    img.put("#211B30", to=(x, y, x + 2, y + 2))
            self.check_images.append(img)
        st.element_create(
            "SoftCheck.indicator",
            "image",
            self.check_images[0],
            ("selected", self.check_images[1]),
            sticky="",
        )
        st.layout(
            "TCheckbutton",
            [
                (
                    "Checkbutton.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("SoftCheck.indicator", {"side": "left", "sticky": ""}),
                            (
                                "Checkbutton.focus",
                                {
                                    "side": "left",
                                    "sticky": "w",
                                    "children": [("Checkbutton.label", {"sticky": "nswe"})],
                                },
                            ),
                        ],
                    },
                )
            ],
        )
        st.configure(
            "Treeview",
            background=COLORS["field"],
            fieldbackground=COLORS["field"],
            foreground=COLORS["text"],
            rowheight=32,
            borderwidth=0,
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
        )
        st.map(
            "Treeview",
            background=[("selected", COLORS["accent_bg"])],
            foreground=[("selected", COLORS["text"])],
        )
        st.configure(
            "Treeview.Heading",
            background=COLORS["line"],
            foreground=COLORS["muted"],
            font=(UI_FONT, 9, "bold"),
            padding=(8, 9),
            relief="flat",
        )
        st.map("Treeview.Heading", background=[("active", COLORS["line"])])
        st.configure(
            "Horizontal.TProgressbar",
            background=COLORS["accent"],
            troughcolor=COLORS["line"],
            borderwidth=0,
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
        )
        st.configure(
            "Horizontal.TScrollbar",
            background=COLORS["line"],
            troughcolor=COLORS["bg"],
            borderwidth=0,
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["bg"],
            lightcolor=COLORS["bg"],
            darkcolor=COLORS["bg"],
        )
        st.configure(
            "Vertical.TScrollbar",
            background=COLORS["line"],
            troughcolor=COLORS["bg"],
            borderwidth=0,
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["bg"],
            lightcolor=COLORS["bg"],
            darkcolor=COLORS["bg"],
        )

    def label(self, parent, text, style="TLabel", **kw):
        w = ttk.Label(parent, text=text, style=style, **kw)
        if kw.get("wraplength"):
            limit = kw["wraplength"]
            parent.bind(
                "<Configure>",
                lambda e, label=w, m=limit: label.configure(
                    wraplength=min(m, max(280, e.width - 40))
                ),
                add="+",
            )
        return w

    def button(self, parent, text, fn, accent=False):
        return SoftButton(parent, text, fn, accent)

    def card(self, parent, title=None, hint=None):
        panel = SoftPanel(parent)
        panel.pack(fill="x", pady=(0, 16))
        f = panel.body
        if title:
            self.label(f, title, "Heading.TLabel").pack(anchor="w", pady=(0, 4))
        if hint:
            self.label(f, hint, "Muted.TLabel", wraplength=820).pack(anchor="w", pady=(0, 14))
        return f

    def text_box(self, parent, height=4):
        t = tk.Text(
            parent,
            height=height,
            bg=COLORS["field"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_bg"],
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            font=(MONO_FONT, 10),
            wrap="word",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent"],
        )
        return t

    def listbox(self, parent, height=5):
        return tk.Listbox(
            parent,
            height=height,
            bg=COLORS["field"],
            fg=COLORS["text"],
            selectbackground=COLORS["accent_bg"],
            selectforeground=COLORS["text"],
            highlightthickness=0,
            bd=0,
            relief="flat",
            selectmode="extended",
            exportselection=False,
            font=(UI_FONT, 10),
        )

    def tree(self, parent, columns, height=7):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.pack(fill="both", expand=True, pady=8)
        t = ttk.Treeview(
            box,
            columns=[x[0] for x in columns],
            show="headings",
            height=height,
            selectmode="extended",
        )
        t.sort_labels = {key: label for key, label, _ in columns}
        t.sort_column = None
        t.sort_descending = False
        for key, label, width in columns:
            t.heading(key, text=label, command=lambda c=key: self.sort_tree(t, c))
            t.column(key, width=width, minwidth=60, anchor="w")
        sy = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        sx = ttk.Scrollbar(box, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        t.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        return t

    def sort_tree(self, t, column, repeat=False):
        descending = (
            t.sort_descending
            if repeat
            else (not t.sort_descending if t.sort_column == column else False)
        )
        items = sorted(
            t.get_children(),
            key=lambda item: tuple(
                int(part) if part.isdigit() else part.casefold()
                for part in re.split(r"(\d+)", str(t.set(item, column)))
            ),
            reverse=descending,
        )
        for i, item in enumerate(items):
            t.move(item, "", i)
        t.sort_column = column
        t.sort_descending = descending
        for key, label in t.sort_labels.items():
            t.heading(key, text=label + (" ▼" if descending else " ▲") if key == column else label)
        if t is getattr(self, "scan_tree", None):
            self.prioritize_component_state()
            self.update_state_heading()

    def pathrow(self, parent, label, var, fn, hint=""):
        self.label(parent, label, "Heading.TLabel").pack(anchor="w", pady=(10, 4))
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x")
        SoftEntry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        self.button(row, "Sfoglia", lambda: self.action(fn)).pack(side="left", padx=(10, 0))
        if hint:
            self.label(parent, hint, "Muted.TLabel", wraplength=820).pack(anchor="w", pady=(5, 2))

    def _ui(self):
        self.theme()
        self.plus_lists = {"drivers": [], "updates": [], "software": []}
        self.plus_text = {}
        self.component_choices = {k: [] for k in ("features", "capabilities", "packages")}
        self.plus_busy = False
        self.service_vars = {n: tk.StringVar(value="Invariato") for n, _ in SERVICE_LIST}
        self.ui_vars = {k: tk.BooleanVar(value=False) for k in UI_RULES}
        self.v_bootdrivers = tk.BooleanVar(value=False)
        self.v_power = tk.StringVar()
        self.profile_var = tk.StringVar(value="Stock")
        self.search_apps = tk.StringVar()
        self.search_components = tk.StringVar()
        self.component_kind = tk.StringVar(value="Tutti")
        self.component_state = tk.StringVar(value="Tutti gli stati")
        self.component_priority = ""
        self.scan_state_var = tk.StringVar(value="Nessuna scansione eseguita")
        self.status_var = tk.StringVar(value="Pronto")
        self.phase_var = tk.StringVar(value="In attesa")
        self.timer_var = tk.StringVar(value="00:00")
        self.stage_var = tk.StringVar(value="0 fasi completate")
        self.unit_var = tk.StringVar(value="")
        self.note_var = tk.StringVar(value="La durata dipende da edizioni, componenti e disco.")
        self.feedback_var = tk.StringVar(value="")
        self.stats_var = tk.StringVar(value="Nessuna ISO selezionata")
        self.nav_buttons = {}
        self.page_frames = {}
        self.page_canvases = {}
        side = tk.Frame(self, bg=COLORS["side"], width=216)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(
            side,
            text="W",
            font=(UI_FONT, 26, "bold"),
            bg=COLORS["accent_bg"],
            fg=COLORS["accent"],
            width=3,
        ).pack(anchor="w", padx=22, pady=(26, 8))
        tk.Label(
            side, text="WinSlim", font=(UI_FONT, 22, "bold"), bg=COLORS["side"], fg=COLORS["text"]
        ).pack(anchor="w", padx=22)
        tk.Label(
            side, text="STUDIO  /  4.0", font=(UI_FONT, 9), bg=COLORS["side"], fg=COLORS["muted"]
        ).pack(anchor="w", padx=24, pady=(1, 24))
        for key, num, title, _ in PAGES:
            b = SoftButton(
                side,
                num + "   " + title,
                lambda k=key: self.navigate(k),
                fill=COLORS["side"],
                foreground=COLORS["muted"],
                left=True,
            )
            b.pack(fill="x", padx=14, pady=3)
            self.nav_buttons[key] = b
        foot = tk.Frame(side, bg=COLORS["side"])
        foot.pack(side="bottom", fill="x", padx=22, pady=25)
        tk.Label(
            foot,
            text="offline · x64",
            font=(SCRIPT_FONT, 24, "italic"),
            bg=COLORS["side"],
            fg="#596778",
        ).pack(anchor="w")
        tk.Label(
            foot,
            text="WINDOWS ISO BUILDER",
            font=(UI_FONT, 8),
            bg=COLORS["side"],
            fg=COLORS["muted"],
        ).pack(anchor="w", pady=(5, 0))
        root = ttk.Frame(self)
        root.pack(side="left", fill="both", expand=True, padx=26, pady=22)
        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 20))
        self.title_label = self.label(top, "Sorgente", "Title.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle_label = self.label(top, "", "Page.TLabel", foreground=COLORS["muted"])
        self.subtitle_label.pack(anchor="w", pady=(4, 0))
        bottom = ttk.Frame(root)
        bottom.pack(side="bottom", fill="x", pady=(14, 0))
        self.label(
            bottom, "", "Page.TLabel", textvariable=self.status_var, foreground=COLORS["muted"]
        ).pack(side="left", fill="x", expand=True)
        self.button(bottom, "Indietro", self.previous_page).pack(side="left", padx=8)
        self.next_btn = self.button(bottom, "Continua", self.next_page)
        self.next_btn.pack(side="left", padx=(0, 8))
        self.btn = self.button(bottom, "Riepilogo e crea", lambda: self.navigate("review"), True)
        self.btn.pack(side="left")
        self.page_host = ttk.Frame(root)
        self.page_host.pack(fill="both", expand=True)
        for key, _, _, _ in PAGES:
            frame = ttk.Frame(self.page_host)
            self.page_frames[key] = frame
            canvas = tk.Canvas(frame, bg=COLORS["bg"], highlightthickness=0, bd=0)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            content = ttk.Frame(canvas)
            wid = canvas.create_window((0, 0), window=content, anchor="nw")
            content.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda e, c=canvas, w=wid: c.itemconfigure(w, width=e.width))
            self.page_canvases[key] = canvas
            getattr(self, "page_" + key)(content)
        self.bind_all("<MouseWheel>", self.wheel, add="+")
        self.bind_all("<Button-4>", self.wheel, add="+")
        self.bind_all("<Button-5>", self.wheel, add="+")
        self.navigate("source")

    def wheel(self, e):
        w = e.widget
        if isinstance(w, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
            return
        try:
            if w.winfo_toplevel() != self:
                return
        except tk.TclError:
            return
        c = self.page_canvases.get(self.current_page)
        if c and c.yview() != (0.0, 1.0):
            c.yview_scroll(
                -1 if getattr(e, "num", None) == 4 or getattr(e, "delta", 0) > 0 else 1, "units"
            )

    def navigate(self, key):
        for f in self.page_frames.values():
            f.pack_forget()
        self.current_page = key
        self.page_frames[key].pack(fill="both", expand=True)
        _, _, title, hint = next(p for p in PAGES if p[0] == key)
        self.title_label.configure(text=title)
        self.subtitle_label.configure(text=hint)
        for k, b in self.nav_buttons.items():
            b.configure(
                bg=COLORS["accent_bg"] if k == key else COLORS["side"],
                fg=COLORS["text"] if k == key else COLORS["muted"],
            )
        self.next_btn.configure(state="disabled" if key == "build" else "normal")
        if key == "review":
            self.refresh_summary()

    def next_page(self):
        keys = [p[0] for p in PAGES]
        self.navigate(keys[min(len(keys) - 1, keys.index(self.current_page) + 1)])

    def previous_page(self):
        keys = [p[0] for p in PAGES]
        self.navigate(keys[max(0, keys.index(self.current_page) - 1)])

    def page_source(self, parent):
        f = self.card(
            parent,
            "Il tuo progetto",
            "Parti da una ISO locale. Le edizioni non selezionate saranno escluse dalla nuova ISO.",
        )
        self.pathrow(f, "ISO originale", self.v_iso, self.pick_iso)
        self.pathrow(f, "ISO di uscita", self.v_out, self.pick_out)
        self.pathrow(
            f,
            "Cartella di lavoro",
            self.v_work,
            self.pick_work,
            "Usa un SSD con almeno 20 GB liberi. Le sorgenti devono stare fuori da WinSlim_work.",
        )
        f = self.card(
            parent,
            "Edizioni Windows",
            "Leggi le edizioni e seleziona solo quelle che utilizzerai: ogni edizione aggiuntiva aumenta il lavoro.",
        )
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 12))
        self.edition_btn = self.button(
            bar, "Leggi edizioni", lambda: self.action(self.load_editions), True
        )
        self.edition_btn.pack(side="left")
        self.button(bar, "Solo Pro", lambda: self.action(lambda: self.select_editions("pro"))).pack(
            side="left", padx=8
        )
        self.button(bar, "Home e Pro", lambda: self.action(self.only_home_pro)).pack(side="left")
        self.lbed = self.listbox(f, 7)
        self.lbed.pack(fill="x")
        self.lbed.bind("<<ListboxSelect>>", lambda e: (self.refresh_auto(), self.refresh_summary()))
        self.label(f, "", "Muted.TLabel", textvariable=self.stats_var).pack(
            anchor="w", pady=(10, 0)
        )
        f = self.card(parent, "Strumenti e spazio")
        self.pathrow(
            f,
            "oscdimg.exe (facoltativo se rilevato automaticamente)",
            self.v_osc,
            self.pick_osc,
            "Serve per creare la ISO. È incluso negli Strumenti di distribuzione del Windows ADK.",
        )
        ttk.Checkbutton(
            f, text="Elimina la cartella di lavoro al completamento", variable=self.v_delwork
        ).pack(anchor="w", pady=10)
        self.button(f, "Verifica configurazione", lambda: self.action(self.validate_ui)).pack(
            anchor="w"
        )
        tools = ttk.Frame(f, style="Card.TFrame")
        tools.pack(fill="x", pady=(12, 0))
        self.button(tools, "SHA-256 sorgente", lambda: self.action(self.hash_source)).pack(
            side="left"
        )
        self.button(tools, "Diagnostica Windows", lambda: self.action(self.diagnostics)).pack(
            side="left", padx=8
        )

    def page_apps(self, parent):
        f = self.card(
            parent,
            "Profilo di partenza",
            "Stock mantiene tutte le app. Essenziale rimuove un piccolo gruppo di app accessorie. Puoi sempre modificare le singole scelte.",
        )
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x")
        for name in ["Stock", "Essenziale", "Privacy"]:
            self.button(bar, name, lambda n=name: self.action(lambda: self.apply_preset(n))).pack(
                side="left", padx=(0, 8)
            )
        self.button(bar, "Ripristina scelte", lambda: self.action(self.undo_config)).pack(
            side="left", padx=8
        )
        self.label(bar, "", "Muted.TLabel", textvariable=self.profile_var).pack(side="left", padx=8)
        f = self.card(
            parent,
            "Applicazioni provisionate",
            "Una spunta indica una richiesta di rimozione, applicata solo alle edizioni selezionate.",
        )
        self.label(f, "Cerca un’app per nome", "Muted.TLabel").pack(anchor="w", pady=(0, 5))
        SoftEntry(f, textvariable=self.search_apps).pack(fill="x", pady=(0, 10))
        self.app_grid = ttk.Frame(f, style="Card.TFrame")
        self.app_grid.pack(fill="x")
        self.app_rows = []
        for label, _, _ in APPX:
            w = ttk.Checkbutton(self.app_grid, text=label, variable=self.appx_vars[label])
            self.app_rows.append((label, w))
        self.app_grid.bind("<Configure>", lambda event: self.filter_apps())
        self.filter_apps()
        self.search_apps.trace_add("write", lambda *args: self.filter_apps())
        f = self.card(parent, "Componenti opzionali standard")
        grid = ttk.Frame(f, style="Card.TFrame")
        grid.pack(fill="x")
        for col in range(2):
            grid.columnconfigure(col, weight=1, uniform="caps")
        for i, (label, _, _) in enumerate(CAPS):
            ttk.Checkbutton(grid, text="Rimuovi " + label, variable=self.cap_vars[label]).grid(
                row=i % 3, column=i // 3, sticky="w", padx=(0, 12), pady=2
            )
        f = self.card(
            parent,
            "Funzioni di sistema",
            "Queste scelte hanno un impatto maggiore. OneDrive e Recall sono opzioni esplicite; Defender viene preservato.",
        )
        for text, var in [
            ("Rimuovi OneDrive", self.v_onedrive),
            ("Rimuovi Recall se presente", self.v_recall),
        ]:
            ttk.Checkbutton(f, text=text, variable=var).pack(anchor="w")
        self.label(
            f,
            "Windows Defender viene preservato: la sua rimozione non è verificabile su tutte le build.",
            "Muted.TLabel",
            wraplength=800,
        ).pack(anchor="w")
        f = self.card(
            parent,
            "Catalogo componenti dell’immagine",
            "Le immagini WIM vengono lette direttamente; quelle ESD richiedono una conversione. Il catalogo viene salvato e riutilizzato anche alla riapertura per ISO ed edizione invariate. Riscansiona forza una nuova lettura.",
        )
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x")
        self.scan_btn = self.button(
            bar, "Scansiona componenti", lambda: self.action(self.scan_iso), True
        )
        self.scan_btn.pack(side="left")
        self.rescan_btn = self.button(
            bar, "Riscansiona", lambda: self.action(lambda: self.scan_iso(force=True))
        )
        self.rescan_btn.pack(side="left", padx=(8, 0))
        self.button(
            bar, "Disattiva / rimuovi [D]", lambda: self.action(self.set_component_choice)
        ).pack(side="left", padx=8)
        self.button(
            bar, "Annulla scelta [R]", lambda: self.action(lambda: self.set_component_choice(False))
        ).pack(side="left")
        self.label(
            f,
            "D: disabilita le feature e rimuove i relativi file; rimuove capabilities e pacchetti. R: annulla la scelta. Le modifiche si applicano durante la creazione della ISO.",
            "Muted.TLabel",
            wraplength=850,
        ).pack(anchor="w", pady=(10, 0))
        self.scan_progress = ttk.Progressbar(f, mode="indeterminate")
        self.scan_progress.pack(fill="x", pady=(12, 6))
        self.label(f, "", "Muted.TLabel", textvariable=self.scan_state_var, wraplength=850).pack(
            anchor="w"
        )
        self.label(f, "Filtra il catalogo per nome o tipo", "Muted.TLabel").pack(
            anchor="w", pady=(10, 0)
        )
        filters = ttk.Frame(f, style="Card.TFrame")
        filters.pack(fill="x", pady=(12, 0))
        SoftEntry(filters, textvariable=self.search_components).pack(
            side="left", fill="x", expand=True
        )
        ttk.Combobox(
            filters,
            textvariable=self.component_kind,
            values=["Tutti", "features", "capabilities", "packages"],
            state="readonly",
            width=15,
        ).pack(side="left", padx=(8, 0))
        self.search_components.trace_add("write", lambda *a: self.filter_components())
        self.component_kind.trace_add("write", lambda *a: self.filter_components())
        self.component_state_box = ttk.Combobox(
            filters,
            textvariable=self.component_state,
            values=["Tutti gli stati"],
            state="readonly",
            width=22,
        )
        self.component_state_box.pack(side="left", padx=(8, 0))
        self.component_state.trace_add("write", lambda *a: self.filter_components())
        self.scan_tree = self.tree(
            f,
            [
                ("kind", "TIPO", 100),
                ("name", "NOME ESATTO", 390),
                ("state", "STATO ISO", 160),
                ("choice", "SCELTA", 165),
            ],
            12,
        )
        self.scan_tree.tag_configure("pending", foreground=COLORS["accent"])
        self.scan_tree.heading("state", command=self.cycle_component_state)
        self.label(
            f,
            "Clicca Stato ISO per portare in cima Enabled, Installed e gli altri gruppi, mantenendo tutte le righe.",
            "Muted.TLabel",
            wraplength=850,
        ).pack(anchor="w", pady=(8, 0))
        for key in ("d", "D"):
            self.scan_tree.bind("<KeyPress-" + key + ">", lambda e: self.component_shortcut(True))
        for key in ("r", "R"):
            self.scan_tree.bind("<KeyPress-" + key + ">", lambda e: self.component_shortcut(False))
        for var in self.cap_vars.values():
            var.trace_add("write", lambda *args: self.filter_components())

    def filter_apps(self):
        query = self.search_apps.get().lower()
        visible = [(label, w) for label, w in self.app_rows if query in label.lower()]
        columns = max(1, min(3, self.app_grid.winfo_width() // 290))
        rows = max(1, (len(visible) + columns - 1) // columns)
        for col in range(3):
            self.app_grid.columnconfigure(
                col, weight=1 if col < columns else 0, uniform="apps" if col < columns else ""
            )
        for _, w in self.app_rows:
            w.grid_remove()
        for i, (_, w) in enumerate(visible):
            w.grid(row=i % rows, column=i // rows, sticky="w", padx=(0, 12), pady=2)

    def page_services(self, parent):
        f = self.card(
            parent,
            "Avvio dei servizi",
            "Le modifiche riguardano l’immagine offline. Dipendenze attive e servizi essenziali vengono verificati; i servizi non vengono eliminati.",
        )
        for name, hint in SERVICE_LIST:
            row = ttk.Frame(f, style="Card.TFrame")
            row.pack(fill="x", pady=7)
            ttk.Combobox(
                row,
                textvariable=self.service_vars[name],
                values=list(MODES),
                state="readonly",
                width=17,
            ).pack(side="right", padx=(12, 0))
            self.label(row, name, "Heading.TLabel").pack(anchor="w")
            self.label(row, hint, "Muted.TLabel").pack(anchor="w", pady=(2, 0))
        f = self.card(
            parent,
            "Servizi aggiuntivi",
            "Formato: NomeServizio=Manuale, Automatico o Disabilitato. Un servizio per riga.",
        )
        t = self.text_box(f, 5)
        t.pack(fill="x")
        self.plus_text["services"] = t

    def page_integrations(self, parent):
        for key, title, hint in [
            (
                "drivers",
                "Driver",
                "Scegli un INF o una cartella completa. Mantieni insieme INF, SYS, CAT e gli altri file del pacchetto.",
            ),
            (
                "updates",
                "Aggiornamenti Windows",
                "CAB/MSU locali. Inserisci i prerequisiti prima dei cumulativi; usa Su/Giù per cambiare ordine.",
            ),
            (
                "software",
                "Software al primo accesso",
                "Installer locali EXE/MSI. Installazione al primo accesso amministratore con richiesta UAC.",
            ),
        ]:
            f = self.card(parent, title, hint)
            self.make_paths_card(f, key)
        f = self.card(
            parent,
            "File aggiuntivi",
            "Puoi includere file o cartelle nella ISO oppure nel Windows installato.",
        )
        self.tv = self.tree(
            f,
            [("src", "SORGENTE", 450), ("where", "DESTINAZIONE", 140), ("dest", "PERCORSO", 180)],
            4,
        )
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x")
        self.button(
            bar, "Aggiungi", lambda: self.action(lambda: FileDialog(self, self.add_file))
        ).pack(side="left")
        self.button(bar, "Rimuovi selezionati", lambda: self.action(self.del_file)).pack(
            side="left", padx=8
        )

    def make_paths_card(self, f, key):
        lb = self.listbox(f, 4)
        lb.pack(fill="x", pady=(0, 12))
        setattr(self, "plus_lb_" + key, lb)
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x")
        self.button(bar, "Aggiungi file", lambda: self.action(lambda: self.add_path(key))).pack(
            side="left"
        )
        if key == "drivers":
            self.button(bar, "Aggiungi cartella", lambda: self.action(self.add_driver_folder)).pack(
                side="left", padx=8
            )
        self.button(bar, "Rimuovi", lambda: self.action(lambda: self.remove_path(key))).pack(
            side="left", padx=8
        )
        for label, delta in [("Su", -1), ("Giù", 1)]:
            self.button(
                bar, label, lambda d=delta: self.action(lambda: self.move_path(key, d))
            ).pack(side="left", padx=(0, 6))
        if key == "software":
            self.button(bar, "Argomenti", lambda: self.action(self.edit_args)).pack(side="left")
        if key == "drivers":
            ttk.Checkbutton(
                f,
                text="Integra i driver anche in tutti gli indici boot.wim",
                variable=self.v_bootdrivers,
            ).pack(anchor="w", pady=(12, 0))
        if key == "software":
            self.label(
                f,
                "MSI: /qn /norestart predefiniti. Per EXE specifica i parametri silent del produttore.",
                "Muted.TLabel",
                wraplength=800,
            ).pack(anchor="w", pady=(12, 0))

    def add_path(self, key):
        types = {
            "drivers": [("Driver INF", "*.inf")],
            "updates": [("CAB / MSU", "*.cab *.msu")],
            "software": [("Installer", "*.exe *.msi")],
        }
        selected = filedialog.askopenfilename(parent=self, filetypes=types[key])
        paths = [selected] if selected else []
        for path in paths:
            if key == "software":
                if not any(item["path"] == path for item in self.plus_lists[key]):
                    self.plus_lists[key].append({"path": path, "args": []})
            elif path not in self.plus_lists[key]:
                self.plus_lists[key].append(path)
        self.refresh_list(key)
        self.refresh_summary()

    def add_driver_folder(self):
        path = filedialog.askdirectory(parent=self)
        if path and path not in self.plus_lists["drivers"]:
            self.plus_lists["drivers"].append(path)
            self.refresh_list("drivers")
            self.refresh_summary()

    def remove_path(self, key):
        for i in reversed(getattr(self, "plus_lb_" + key).curselection()):
            self.plus_lists[key].pop(i)
        self.refresh_list(key)
        self.refresh_summary()

    def move_path(self, key, delta):
        lb = getattr(self, "plus_lb_" + key)
        sel = lb.curselection()
        if len(sel) != 1:
            return
        i = sel[0]
        j = i + delta
        arr = self.plus_lists[key]
        if 0 <= j < len(arr):
            arr[i], arr[j] = arr[j], arr[i]
            self.refresh_list(key)
            lb.selection_set(j)

    def edit_args(self):
        sel = self.plus_lb_software.curselection()
        if len(sel) != 1:
            return
        item = self.plus_lists["software"][sel[0]]
        args = self.ask_args(item["path"], item["args"])
        if args is not None:
            item["args"] = args
            self.refresh_list("software")

    def page_personal(self, parent):
        f = self.card(
            parent,
            "Aspetto di Windows",
            "Applicato al profilo Default per i nuovi utenti del sistema installato.",
        )
        for key, (label, _) in UI_RULES.items():
            ttk.Checkbutton(f, text=label, variable=self.ui_vars[key]).pack(anchor="w")
        f = self.card(
            parent,
            "Impostazioni Windows",
            "Ogni opzione corrisponde a modifiche definite nel registro. Non rappresenta un guadagno di FPS misurato.",
        )
        for key, label, _, _ in TWEAKS:
            ttk.Checkbutton(f, text=label, variable=self.tw_vars[key]).pack(anchor="w")
        f = self.card(
            parent,
            "Piano energetico",
            "Scegli un file .pow esportato con powercfg. Importazione e attivazione al primo accesso amministratore.",
        )

        def choose():
            path = filedialog.askopenfilename(
                parent=self, filetypes=[("Piano energetico", "*.pow")]
            )
            if path:
                self.v_power.set(path)

        self.pathrow(f, "File del piano", self.v_power, choose)
        self.button(f, "Rimuovi piano", lambda: self.action(lambda: self.v_power.set(""))).pack(
            anchor="w", pady=(12, 0)
        )
        f = self.card(
            parent,
            "Importazione registro",
            "I file REG vengono adattati agli hive offline dell’immagine.",
        )
        self.lb = self.listbox(f, 4)
        self.lb.pack(fill="x", pady=(0, 12))
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x")
        self.button(bar, "Aggiungi .reg", lambda: self.action(self.add_reg)).pack(side="left")
        self.button(bar, "Rimuovi selezionati", lambda: self.action(self.del_reg)).pack(
            side="left", padx=8
        )

    def page_install(self, parent):
        f = self.card(
            parent,
            "Installazione automatica",
            "Configura lingua e account. La password è scritta nell’installazione automatica sulla ISO, ma esclusa da configurazioni JSON e report.",
        )
        ttk.Checkbutton(f, text="Abilita installazione automatica", variable=self.v_unatt).pack(
            anchor="w", pady=(0, 8)
        )
        self.label(f, "Edizione da installare", "Heading.TLabel").pack(anchor="w")
        self.cb_auto = ttk.Combobox(f, textvariable=self.v_auto, state="readonly")
        self.cb_auto.pack(fill="x", pady=8)
        for label, var in [
            ("Lingua", self.v_lang),
            ("Layout tastiera", self.v_kbd),
            ("Fuso orario", self.v_tz),
            ("Nome utente", self.v_user),
            ("Password", self.v_pwd),
        ]:
            row = ttk.Frame(f, style="Card.TFrame")
            row.pack(fill="x", pady=5)
            self.label(row, label, width=18).pack(side="left")
            SoftEntry(row, textvariable=var, show="•" if label == "Password" else "").pack(
                side="left", fill="x", expand=True
            )
        ttk.Checkbutton(
            f, text="Accesso automatico all’account configurato", variable=self.v_autologon
        ).pack(anchor="w", pady=(10, 0))
        f = self.card(
            parent,
            "Opzioni avanzate",
            "Il partizionamento automatico cancella tutto il disco 0 sul computer su cui installerai questa ISO.",
        )
        ttk.Checkbutton(
            f,
            text="Partiziona automaticamente il disco 0 — cancella tutti i dati",
            variable=self.v_wipe,
        ).pack(anchor="w")
        ttk.Checkbutton(
            f, text="Bypass requisiti hardware di Windows 11", variable=self.v_bypass
        ).pack(anchor="w", pady=(12, 0))

    def page_review(self, parent):
        f = self.card(
            parent,
            "Configurazione pronta per il controllo",
            "Verifica le scelte e gli strumenti prima di avviare. L’anteprima mostra le modifiche richieste; il report finale mostra il risultato.",
        )
        row = ttk.Frame(f, style="Card.TFrame")
        row.pack(fill="x")
        for label, fn in [
            ("Esporta JSON", self.export_config),
            ("Importa JSON", self.import_config),
            ("Controlla", self.validate_ui),
        ]:
            self.button(row, label, lambda f=fn: self.action(f)).pack(side="left", padx=(0, 8))
        self.label(f, "", "Muted.TLabel", textvariable=self.feedback_var, wraplength=820).pack(
            anchor="w", pady=(14, 0)
        )
        f = self.card(parent, "Modifiche selezionate")
        self.summary_tree = self.tree(
            f, [("area", "AREA", 140), ("name", "ELEMENTO", 480), ("action", "AZIONE", 240)], 11
        )
        self.summary_count = self.label(f, "", "Muted.TLabel")
        self.summary_count.pack(anchor="w")
        self.create_btn = self.button(f, "Controlla e crea ISO", self.start, True)
        self.create_btn.pack(anchor="e", pady=(16, 0))

    def page_build(self, parent):
        f = self.card(parent, "Stato della lavorazione")
        header = ttk.Frame(f, style="Card.TFrame")
        header.pack(fill="x")
        self.label(header, "", "Heading.TLabel", textvariable=self.phase_var, wraplength=650).pack(
            side="left", fill="x", expand=True
        )
        self.label(header, "", "Heading.TLabel", textvariable=self.timer_var).pack(side="right")
        self.prog = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(20, 10))
        self.label(f, "", "Muted.TLabel", textvariable=self.stage_var).pack(anchor="w")
        self.label(f, "", "Muted.TLabel", textvariable=self.unit_var).pack(anchor="w", pady=(4, 0))
        self.label(f, "", "Muted.TLabel", textvariable=self.note_var, wraplength=820).pack(
            anchor="w", pady=(10, 12)
        )
        self.cancel_btn = self.button(f, "Annulla al termine del comando", self.cancel_operation)
        self.cancel_btn.pack(anchor="w")
        self.cancel_btn.configure(state="disabled")
        f = self.card(
            parent,
            "Log operativo",
            "Il dettaglio completo resta disponibile anche quando si verifica un errore.",
        )
        self.txt = self.text_box(f, 13)
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x", pady=(14, 0))
        self.button(bar, "Esporta log", self.export_log).pack(side="left")
        self.button(bar, "Copia log", self.copy_log).pack(side="left", padx=8)
        self.button(bar, "Apri cartella risultato", self.open_output).pack(side="left")
        self.button(bar, "Apri confronto", lambda: self.action(self.open_report)).pack(
            side="left", padx=8
        )
        f = self.card(parent, "Cronologia delle fasi")
        self.history_tree = self.tree(
            f, [("num", "FASE", 80), ("name", "OPERAZIONE", 620), ("state", "STATO", 130)], 5
        )

    def guard(self):
        if self.running:
            raise ValueError(
                "È in corso una creazione. Puoi consultare le pagine o richiedere l’annullamento."
            )
        if self.loading_editions:
            raise ValueError("Attendi la lettura delle edizioni.")
        # Scanning owns an isolated temporary image and a snapshot of the ISO.
        # Editing options and exporting config remain available while it runs.

    def action(self, fn):
        try:
            self.guard()
            fn()
        except Exception as e:
            self.notify("Impossibile completare", str(e), error=True)

    def notify(self, title, text, error=False):
        w = tk.Toplevel(self)
        w.title(title)
        w.configure(bg=COLORS["bg"])
        w.transient(self)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=24)
        f.pack(fill="both", expand=True)
        self.label(f, title, "Title.TLabel", font=(UI_FONT, 16, "bold"), wraplength=640).pack(
            anchor="w"
        )
        self.label(
            f,
            text,
            "Page.TLabel",
            wraplength=640,
            justify="left",
            foreground=COLORS["red"] if error else COLORS["muted"],
        ).pack(anchor="w", pady=(12, 20))
        self.button(f, "Chiudi", w.destroy, True).pack(anchor="e")
        w.grab_set()
        self.place_dialog(w)

    def confirm(self, title, text):
        w = tk.Toplevel(self)
        w.title(title)
        w.configure(bg=COLORS["bg"])
        w.transient(self)
        w.resizable(False, False)
        answer = []
        f = ttk.Frame(w, padding=24)
        f.pack(fill="both", expand=True)
        self.label(f, title, "Title.TLabel", font=(UI_FONT, 16, "bold"), wraplength=680).pack(
            anchor="w"
        )
        self.label(
            f, text, "Page.TLabel", wraplength=680, justify="left", foreground=COLORS["muted"]
        ).pack(anchor="w", pady=(14, 22))
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.button(bar, "Indietro", w.destroy).pack(side="right")
        self.button(bar, "Conferma", lambda: (answer.append(True), w.destroy()), True).pack(
            side="right", padx=8
        )
        w.grab_set()
        self.place_dialog(w)
        self.wait_window(w)
        return bool(answer)

    def place_dialog(self, w):
        w.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - w.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - w.winfo_reqheight()) // 2
        w.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def apply_preset(self, name, initial=False):
        if not initial and not self.confirm(
            "Applica profilo " + name,
            "Il profilo sostituisce le scelte di rimozione app, componenti standard e tweak. Driver, software, servizi e file personalizzati restano come sono.",
        ):
            return
        if not initial:
            self.checkpoint()
        safe_apps = {
            "Notizie (Bing News)",
            "Meteo",
            "Solitaire",
            "Clipchamp",
            "Microsoft Teams",
            "App Xbox",
            "Get Help",
            "Get Started",
        }
        for label, v in self.appx_vars.items():
            v.set(
                name == "Essenziale"
                and (
                    label in safe_apps
                    or any(
                        x in label.lower()
                        for x in ("solitaire", "clipchamp", "bing news", "meteo", "get started")
                    )
                )
            )
        for v in self.cap_vars.values():
            v.set(False)
        for key, v in self.tw_vars.items():
            v.set(
                name == "Essenziale"
                and key in ("consumer", "adid", "bingsearch")
                or name == "Privacy"
                and key
                in ("telemetry", "consumer", "adid", "copilot", "recall", "bingsearch", "widgets")
            )
        if initial:
            for v in (
                self.v_onedrive,
                self.v_recall,
                self.v_defender,
                self.v_bypass,
                self.v_unatt,
                self.v_wipe,
                self.v_autologon,
            ):
                v.set(False)
        self.profile_var.set("Profilo: " + name)
        self.refresh_summary()

    def refresh_summary(self):
        if not hasattr(self, "summary_tree"):
            return
        try:
            rows = configuration_summary(self.config())
            for item in self.summary_tree.get_children():
                self.summary_tree.delete(item)
            for row in rows:
                self.summary_tree.insert("", "end", values=row)
            self.summary_count.configure(text=str(len(rows)) + " elementi nel riepilogo")
            sel = self.selected_editions()
            src = self.v_iso.get()
            size = (
                human_size(os.path.getsize(src)) if os.path.isfile(src) else "ISO non disponibile"
            )
            self.stats_var.set(
                "%d edizioni selezionate su %d  •  %s" % (len(sel), len(self.editions), size)
            )
        except Exception as e:
            self.feedback_var.set(str(e))

    def iso_changed(self, *args):
        self.editions_fingerprint = None
        self.last_editions_iso = ""
        self.editions = []
        self.lbed.delete(0, "end")
        self.refresh_auto()
        self.scan_records = []
        self.filter_components()
        self.scan_state_var.set("ISO cambiata: rileggi le edizioni.")
        self.feedback_var.set("")
        self.refresh_summary()

    def pick_iso(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("ISO Windows", "*.iso")])
        if not path:
            return
        self.v_iso.set(os.path.normpath(path))
        if not self.v_out.get():
            self.v_out.set(os.path.splitext(path)[0] + "_WinSlim.iso")
        if not self.loading_editions and not self.running and not self.operation:
            self.load_editions()

    def select_editions(self, kind):
        self.lbed.selection_clear(0, "end")
        for i, (_, name) in enumerate(self.editions):
            if re.fullmatch(r"Windows\s+\d+\s+Pro", name.strip(), re.I):
                self.lbed.selection_set(i)
        self.refresh_auto()
        self.refresh_summary()

    def load_editions(self):
        if self.operation:
            raise ValueError("Attendi la fine della scansione prima di rileggere le edizioni.")
        if self.loading_editions:
            return
        if not IS_WIN or not is_admin():
            raise ValueError("La lettura delle ISO richiede Windows e diritti di amministratore.")
        iso = self.v_iso.get()
        if not os.path.isfile(iso):
            raise ValueError("Scegli una ISO locale valida.")
        initial_fingerprint = fingerprint(iso)
        self.loading_editions = True
        self.edition_btn.configure(state="disabled")
        self.status_var.set("Lettura edizioni in corso…")

        def work():
            try:
                result = read_editions(iso, self.log)
                if fingerprint(iso) != initial_fingerprint:
                    raise ValueError("La ISO è cambiata durante la lettura. Rileggila.")
                self.q.put(("studio_editions", (iso, result, initial_fingerprint)))
            except Exception as e:
                self.q.put(("studio_editions_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def import_config(self):
        ProjectActions.import_config(self)
        self.last_editions_iso = ""
        self.refresh_summary()
        self.profile_var.set("Configurazione importata · rileggi le edizioni")

    def collect_build(self):
        # Legacy JSONs can request a feature no longer exposed by the interface.
        # Normalize it here too so stale UI state cannot block a supported build.
        self.v_defender.set(False)
        selected = self.selected_editions()
        return {
            "iso": self.v_iso.get().strip(),
            "out": self.v_out.get().strip(),
            "work": self.v_work.get().strip(),
            "oscdimg": self.v_osc.get().strip(),
            "indexes": [x[0] for x in selected],
            "names": [x[1] for x in selected],
            "appx": [k for k, v in self.appx_vars.items() if v.get()],
            "caps": [k for k, v in self.cap_vars.items() if v.get()],
            "tweaks": [k for k, v in self.tw_vars.items() if v.get()],
            "regfiles": list(self.lb.get(0, "end")),
            "files": json.loads(json.dumps(self.files)),
            "onedrive": self.v_onedrive.get(),
            "recall": self.v_recall.get(),
            "defender": self.v_defender.get(),
            "bypass": self.v_bypass.get(),
            "delete_work": self.v_delwork.get(),
            "unattended": self.v_unatt.get(),
            "auto_name": self.v_auto.get(),
            "lang": self.v_lang.get() or "en-US",
            "kbd": self.v_kbd.get() or self.v_lang.get() or "en-US",
            "tz": self.v_tz.get() or "UTC",
            "user": self.v_user.get(),
            "pwd": self.v_pwd.get(),
            "autologon": self.v_autologon.get(),
            "wipe": self.v_wipe.get(),
            "extra": self.extra_config(),
        }

    def validate_project(self, cfg):
        validate_extra(cfg["extra"])
        if not os.path.isfile(cfg["iso"]):
            raise ValueError("Seleziona una ISO locale valida.")
        if not cfg["indexes"]:
            raise ValueError("Leggi le edizioni e selezionane almeno una.")
        if self.last_editions_iso != self.v_iso.get() or self.editions_fingerprint != fingerprint(
            cfg["iso"]
        ):
            raise ValueError("Rileggi le edizioni della ISO selezionata.")
        if not cfg["out"] or not cfg["out"].lower().endswith(".iso"):
            raise ValueError("Scegli un file di uscita con estensione .iso.")
        if not cfg["work"]:
            raise ValueError("Seleziona una cartella di lavoro.")
        if not IS_WIN or not is_admin():
            raise ValueError(
                "Per creare la ISO, esegui il programma su Windows come amministratore."
            )
        os.makedirs(cfg["work"], exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(cfg["out"])), exist_ok=True)
        builder = Builder(cfg, self.log)
        builder.source_fingerprint = self.editions_fingerprint
        builder.preflight()
        # Verify local driver dependencies before an expensive export.
        for path in cfg["extra"]["drivers"]:
            infs = (
                [path]
                if os.path.isfile(path)
                else [
                    os.path.join(root, f)
                    for root, _, files in os.walk(path)
                    for f in files
                    if f.lower().endswith(".inf")
                ]
            )
            if not infs:
                raise ValueError("Nessun INF nella cartella driver: " + path)
            for inf in infs:
                validate_inf_files(inf)
        return builder

    def validate_ui(self):
        self.refresh_summary()
        try:
            self.validate_project(self.collect_build())
            self.feedback_var.set(
                "Controlli preliminari superati. Applicabilità dei pacchetti e dipendenze saranno verificate da DISM."
            )
            self.status_var.set("Configurazione verificata")
        except Exception as e:
            self.feedback_var.set(str(e))
            self.status_var.set("Configurazione da completare")
            self.navigate("review")

    def start(self):
        if self.running or self.plus_busy or self.loading_editions:
            self.notify(
                "Operazione in corso",
                "Attendi la scansione o la lettura della ISO prima di creare. Puoi continuare a configurare le opzioni.",
            )
            return
        self.refresh_summary()
        try:
            cfg = self.collect_build()
            builder = self.validate_project(cfg)
        except Exception as e:
            self.feedback_var.set(str(e))
            self.navigate("review")
            self.notify("Controllo preliminare", str(e), True)
            return
        warnings = []
        if cfg["defender"]:
            warnings.append(
                "Windows Defender verrà rimosso: il sistema non avrà la sua protezione antivirus."
            )
        if cfg["unattended"] and cfg["wipe"]:
            warnings.append(
                "Questa ISO cancellerà tutti i dati sul disco 0 del PC di destinazione durante l’installazione."
            )
        if cfg["extra"]["packages"]:
            warnings.append(
                "La rimozione manuale dei pacchetti può compromettere componenti e aggiornamenti."
            )
        if any(
            x["path"].lower().endswith(".exe") and not x["args"] for x in cfg["extra"]["software"]
        ):
            warnings.append(
                "Alcuni EXE non hanno parametri silent e potrebbero aprire un installer interattivo."
            )
        if os.path.isfile(cfg["out"]):
            warnings.append("La ISO di uscita esistente verrà sostituita.")
        text = (
            "Edizioni: "
            + ", ".join(cfg["names"])
            + "\n\nUscita: "
            + cfg["out"]
            + "\n\nVerrà creata una cartella WinSlim_work dedicata; le lavorazioni esistenti vengono preservate."
        )
        if warnings:
            text += "\n\n" + "\n\n".join(warnings)
        if not self.confirm("Crea la ISO personalizzata", text):
            return
        self.operation = "build"
        self.running = True
        self.cancel_event = threading.Event()
        builder.cancel_event = self.cancel_event
        builder.on_unit_progress = lambda p: self.q.put(("unit_progress", p))
        self.operation_started = time.monotonic()
        self.done_phases = 0
        self.total_phases = 0
        self.prog["value"] = 0
        self.last_output = cfg["out"]
        self.last_report = ""
        self.operation_warning = ""
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        self.phase_var.set("Preparazione della lavorazione")
        self.note_var.set("Annullamento disponibile: il comando attivo verrà lasciato terminare.")
        self.cancel_btn.configure(state="normal")
        self.btn.configure(state="disabled")
        self.create_btn.configure(state="disabled")
        self.navigate("build")
        self.log("Avvio creazione con configurazione congelata.")

        def worker():
            try:
                builder.build()
                self.q.put(
                    (
                        "studio_done",
                        {
                            "out": cfg["out"],
                            "report": cfg["out"] + ".report.json",
                            "warnings": builder.warnings,
                        },
                    )
                )
            except BuildCancelled as e:
                self.q.put(("studio_cancelled", str(e)))
            except Exception as e:
                self.q.put(("studio_build_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_operation(self):
        if not self.operation:
            return
        self.cancel_event.set()
        self.cancel_btn.configure(state="disabled")
        self.note_var.set(
            "Annullamento richiesto. Attendo la fine del comando e smonto le immagini."
        )
        self.status_var.set("Annullamento richiesto")
        self.log("Richiesto annullamento al termine del comando attivo.")

    def finish_operation(self):
        self.running = False
        self.plus_busy = False
        self.operation = None
        self.scan_progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.btn.configure(state="normal")
        self.create_btn.configure(state="normal")
        self.scan_btn.configure(state="normal")
        self.rescan_btn.configure(state="normal")
        if self.pending_close:
            self.after(100, self._close)

    def log(self, msg):
        self.q.put(("log", str(msg)))

    def append_log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        entry = "[" + stamp + "] " + msg
        self.logs.append(entry)
        if self.session_log:
            try:
                self.session_log.write(entry + "\n")
            except OSError:
                pass
        self.txt.configure(state="normal")
        self.txt.insert("end", entry + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")
        # Keep the visible widget bounded; full diagnostic logs remain on disk/in memory.
        if len(self.logs) % 500 == 0 and len(self.logs) > 3000:
            self.txt.configure(state="normal")
            self.txt.delete("1.0", "501.0")
            self.txt.configure(state="disabled")
        m = re.search(r"=== \[(\d+)/(\d+)\] (.*?) ===", msg)
        if m:
            num, total, title = int(m.group(1)), int(m.group(2)), m.group(3)
            self.done_phases = num - 1
            self.total_phases = total
            self.phase_var.set(title)
            self.prog["value"] = 100 * (num - 1) / total
            self.unit_var.set("")
            for item in self.history_tree.get_children():
                values = self.history_tree.item(item, "values")
                if values[-1] == "In corso":
                    self.history_tree.set(item, "state", "Completata")
            item = self.history_tree.insert("", "end", values=(num, title, "In corso"))
            self.history_tree.see(item)
            self.stage_var.set(
                "%d di %d fasi completate • la barra indica le fasi, non il tempo residuo"
                % (num - 1, total)
            )

    def _pump(self):
        try:
            for _ in range(200):
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.append_log(val)
                elif kind == "studio_editions":
                    self.loading_editions = False
                    self.edition_btn.configure(state="normal")
                    iso, result, source_fingerprint = val
                    if iso == self.v_iso.get():
                        self.editions, lang = result
                        self.last_editions_iso = iso
                        self.editions_fingerprint = source_fingerprint
                        self.lbed.delete(0, "end")
                        for idx, name in self.editions:
                            self.lbed.insert("end", "%02d   %s" % (idx, name))
                        self.v_lang.set(lang)
                        self.v_kbd.set(lang)
                        self.select_editions("pro")
                        if not self.selected_editions() and self.editions:
                            self.lbed.selection_set(0)
                            self.refresh_auto()
                        self.status_var.set("Edizioni caricate")
                        self.refresh_summary()
                elif kind == "studio_editions_error":
                    self.loading_editions = False
                    self.edition_btn.configure(state="normal")
                    self.status_var.set("Lettura edizioni fallita")
                    self.append_log(val)
                    self.notify("Lettura ISO fallita", val, True)
                elif kind == "hash_ready":
                    self.finish_operation()
                    self.status_var.set("SHA-256 calcolato")
                    self.show_text("Integrità ISO", val)
                elif kind == "hash_error":
                    self.finish_operation()
                    self.status_var.set("Calcolo SHA-256 interrotto")
                    self.append_log(val)
                elif kind == "scan_status":
                    self.scan_state_var.set(val)
                    self.status_var.set(val)
                    self.phase_var.set(val)
                    match = re.match(r"(\d+)/5", val)
                    if match:
                        self.prog["value"] = 20 * (int(match.group(1)) - 1)
                elif kind == "scan_ready":
                    key, iso, data = val
                    self.scan_cache[key] = data
                    if iso == self.v_iso.get():
                        self.scan_records = data
                        self.filter_components()
                        self.scan_state_var.set(
                            "%d componenti letti • seleziona le righe e premi D o R" % len(data)
                        )
                    else:
                        self.scan_state_var.set(
                            "Scansione completata per una ISO precedente. Riscansiona la ISO attuale."
                        )
                    self.status_var.set("Scansione completata")
                    self.phase_var.set("Scansione completata")
                    self.prog["value"] = 100
                    self.stage_var.set("Scansione completata • 5/5 fasi")
                    self.finish_operation()
                elif kind == "scan_error":
                    self.append_log(val)
                    self.scan_state_var.set("Scansione non completata: consulta il log.")
                    self.status_var.set("Scansione fallita")
                    self.finish_operation()
                    self.notify("Scansione non completata", val, True)
                elif kind == "scan_cancelled":
                    self.scan_state_var.set("Scansione annullata")
                    self.status_var.set("Scansione annullata")
                    self.finish_operation()
                elif kind == "unit_progress":
                    self.unit_var.set("Comando attivo: %.0f%%" % val)
                elif kind == "studio_done":
                    self.finish_operation()
                    self.prog["value"] = 100
                    self.done_phases = self.total_phases
                    self.operation_warning = "\n".join(val.get("warnings", []))
                    title = "ISO creata con avvisi" if self.operation_warning else "ISO creata"
                    self.phase_var.set(title)
                    self.stage_var.set(
                        "%d di %d fasi completate" % (self.total_phases, self.total_phases)
                    )
                    self.note_var.set(
                        val["out"]
                        + ("\n" + self.operation_warning if self.operation_warning else "")
                    )
                    self.status_var.set(title)
                    self.last_report = val["report"]
                    for item in self.history_tree.get_children():
                        self.history_tree.set(item, "state", "Completata")
                elif kind in ("studio_build_error", "studio_cancelled"):
                    self.append_log(val)
                    self.finish_operation()
                    title, hint = describe_error(
                        BuildCancelled(val) if kind == "studio_cancelled" else val
                    )
                    self.phase_var.set(title)
                    self.note_var.set(hint)
                    self.status_var.set(title)
                    for item in self.history_tree.get_children():
                        if self.history_tree.set(item, "state") == "In corso":
                            self.history_tree.set(
                                item,
                                "state",
                                "Annullata" if kind == "studio_cancelled" else "Fallita",
                            )
                    if kind != "studio_cancelled":
                        self.notify(title, hint + "\n\n" + str(val)[-1700:], True)
        except queue.Empty:
            pass
        except Exception as e:
            try:
                self.append_log("Errore interfaccia: " + str(e))
            except Exception:
                pass
        if self.operation and self.operation_started:
            seconds = int(time.monotonic() - self.operation_started)
            self.timer_var.set("%02d:%02d" % (seconds // 60, seconds % 60))
        self.after(100, self._pump)

    def prioritize_component_state(self):
        if self.component_priority:
            items = sorted(
                self.scan_tree.get_children(),
                key=lambda item: self.scan_tree.set(item, "state") != self.component_priority,
            )
            for position, item in enumerate(items):
                self.scan_tree.move(item, "", position)

    def update_state_heading(self):
        label = "STATO ISO" + (" · " + self.component_priority if self.component_priority else "")
        self.scan_tree.heading("state", text=label)

    def cycle_component_state(self):
        kind_filter = self.component_kind.get()
        available = {
            state
            for kind, _, state in self.scan_records
            if kind_filter == "Tutti" or kind == kind_filter
        }
        scanned = {(kind, name) for kind, name, _ in self.scan_records}
        if any(
            (kind, name) not in scanned
            for kind, names in self.component_choices.items()
            if kind_filter == "Tutti" or kind == kind_filter
            for name in names
        ):
            available.add("Da verificare")
        preferred = [
            state for state in ("Enabled", "Installed", "Disabled", "Staged") if state in available
        ]
        states = preferred + sorted(available.difference(preferred), key=str.casefold) + [""]
        current = self.component_priority
        self.component_priority = (
            states[(states.index(current) + 1) % len(states)] if current in states else states[0]
        )
        self.component_state.set("Tutti gli stati")
        self.filter_components()
        self.scan_tree.yview_moveto(0)

    def filter_components(self):
        if not hasattr(self, "scan_tree"):
            return
        selected = self.scan_tree.selection()
        focus = self.scan_tree.focus()
        top = self.scan_tree.yview()[0]
        query = self.search_components.get().casefold()
        kind_filter = self.component_kind.get()
        rows = {(kind, name): state for kind, name, state in self.scan_records}
        for kind, names in self.component_choices.items():
            for name in names:
                rows.setdefault((kind, name), "Da verificare")
        self.component_state_box.configure(
            values=["Tutti gli stati"] + sorted(set(rows.values()), key=str.casefold)
        )
        state_filter = self.component_state.get()
        for item in self.scan_tree.get_children():
            self.scan_tree.delete(item)
        for (kind, name), state in rows.items():
            chosen = self.component_requested(kind, name)
            choice = (
                ("Da disattivare" if kind == "features" else "Da rimuovere")
                if chosen
                else "Mantieni"
            )
            values = (kind, name, state, choice)
            if (
                (kind_filter == "Tutti" or kind == kind_filter)
                and (state_filter == "Tutti gli stati" or state == state_filter)
                and query in " ".join(values).casefold()
            ):
                self.scan_tree.insert(
                    "",
                    "end",
                    iid=kind + ":" + name,
                    values=values,
                    tags=("pending",) if chosen else (),
                )
        if self.scan_tree.sort_column:
            self.sort_tree(self.scan_tree, self.scan_tree.sort_column, repeat=True)
        self.prioritize_component_state()
        self.scan_tree.selection_set([item for item in selected if self.scan_tree.exists(item)])
        if self.scan_tree.exists(focus):
            self.scan_tree.focus(focus)
        self.scan_tree.yview_moveto(top)
        self.update_state_heading()

    def scan_iso(self, force=False):
        if self.operation or self.loading_editions:
            raise ValueError("È già in corso un’operazione.")
        iso = self.v_iso.get()
        sel = self.selected_editions()
        if not IS_WIN or not is_admin():
            raise ValueError("Scansione disponibile su Windows come amministratore.")
        if not os.path.isfile(iso) or not sel:
            raise ValueError("Leggi le edizioni e selezionane una.")
        if self.last_editions_iso != iso or self.editions_fingerprint != fingerprint(iso):
            raise ValueError("Rileggi le edizioni della ISO corrente.")
        key = scan_identity(iso, sel[0][0])
        cached = None if force else self.scan_cache.get(key)
        if cached is None and not force:
            cached = self.catalog_cache.load(key)
        if cached is not None:
            self.scan_cache[key] = cached
            self.scan_records = cached
            self.filter_components()
            self.scan_state_var.set(
                "%d componenti • catalogo riutilizzato, nessuna scansione necessaria." % len(cached)
            )
            self.status_var.set("Catalogo caricato")
            return
        self.prog["value"] = 0
        self.stage_var.set("Scansione catalogo • 5 fasi")
        self.operation = "scan"
        self.plus_busy = True
        self.cancel_event = threading.Event()
        self.operation_started = time.monotonic()
        self.scan_progress.start(12)
        self.scan_btn.configure(state="disabled")
        self.rescan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.note_var.set(
            "Scansione in sola lettura. Puoi continuare a configurare le altre opzioni."
        )
        idx = sel[0][0]
        event = self.cancel_event

        def worker():
            temp = tempfile.mkdtemp(prefix="WinSlimScan_")
            mnt = os.path.join(temp, "mount")
            wim = os.path.join(temp, "scan.wim")
            out = os.path.join(temp, "inventory.json")
            cancel_file = os.path.join(temp, "cancel")
            os.makedirs(mnt)
            script = scan_script(iso, idx, wim, mnt, out, cancel_file)
            script_path = os.path.join(temp, "scan.ps1")
            log_path = os.path.join(temp, "scan.log")
            try:
                with open(script_path, "w", encoding="utf-8-sig") as f:
                    f.write(script)
                with open(log_path, "w", encoding="utf-8") as logfile:
                    p = subprocess.Popen(
                        [
                            "powershell.exe",
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            script_path,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=NOWIN,
                    )

                    # A monitor writes the cancellation marker; DISM commands are never killed.
                    def monitor():
                        while p.poll() is None:
                            if event.wait(0.2):
                                try:
                                    open(cancel_file, "a").close()
                                except OSError:
                                    pass
                                return

                    threading.Thread(target=monitor, daemon=True).start()
                    output = []
                    for raw in p.stdout:
                        line = raw.rstrip()
                        output.append(line)
                        logfile.write(line + "\n")
                        if line.startswith("STUDIO:"):
                            self.q.put(("scan_status", line[7:]))
                        elif line:
                            self.log("Scansione: " + line)
                    p.stdout.close()
                    rc = p.wait()
                if rc and event.is_set() and os.path.isfile(out + ".cleaned"):
                    self.q.put(("scan_cancelled", None))
                    shutil.rmtree(temp, ignore_errors=True)
                    return
                if rc:
                    raise ValueError("\n".join(output[-16:]) + "\nDiagnostica: " + temp)
                with open(out, encoding="utf-8-sig") as f:
                    data = json.load(f)
                rows = []
                for kind, items in data.items():
                    for item in items or []:
                        rows.append((kind, item.get("Name", ""), item.get("State", "")))
                if scan_identity(iso, idx) != key:
                    raise ValueError("La ISO è cambiata durante la scansione: rileggi le edizioni.")
                if not self.catalog_cache.save(key, rows):
                    self.log(
                        "Catalogo letto; cache su disco non disponibile, riutilizzo limitato alla sessione."
                    )
                self.q.put(("scan_ready", (key, iso, rows)))
                shutil.rmtree(temp, ignore_errors=True)
            except Exception as e:
                self.q.put(("scan_error", str(e) + "\nCartella temporanea: " + temp))

        threading.Thread(target=worker, daemon=True).start()

    def export_log(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            initialfile="WinSlim-diagnostica.log",
            defaultextension=".log",
            filetypes=[("Log", "*.log")],
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(self.logs) + "\n")
                self.status_var.set("Log esportato")
            except OSError as e:
                self.notify("Esportazione fallita", str(e), True)

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.logs))
        self.status_var.set("Log copiato")

    def open_output(self):
        path = os.path.dirname(self.last_output or self.v_out.get())
        if IS_WIN and path and os.path.isdir(path):
            os.startfile(path)
        else:
            self.notify("Cartella risultato", "Scegli prima il percorso della ISO di uscita.")

    def open_report(self):
        if self.last_report and os.path.isfile(self.last_report):
            with open(self.last_report, encoding="utf-8-sig") as f:
                data = json.load(f)
        else:
            path = filedialog.askopenfilename(
                parent=self, filetypes=[("Report confronto", "*.report.json *.json")]
            )
            if not path:
                return
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        w = tk.Toplevel(self)
        w.title("Confronto ISO")
        w.geometry("1040x700")
        w.configure(bg=COLORS["bg"])
        f = ttk.Frame(w, padding=24)
        f.pack(fill="both", expand=True)
        self.label(f, "Confronto originale / modificata", "Title.TLabel").pack(anchor="w")
        iso = data.get("iso", {})
        self.label(
            f,
            "Sorgente: %s   →   Uscita: %s"
            % (human_size(iso.get("source_bytes", 0)), human_size(iso.get("output_bytes", 0))),
            "Page.TLabel",
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(10, 20))
        tree = self.tree(
            f,
            [
                ("edition", "EDIZIONE", 160),
                ("area", "AREA", 130),
                ("element", "ELEMENTO", 460),
                ("change", "MODIFICA", 180),
            ],
            13,
        )
        for ed in data.get("editions", []):
            for area, diff in ed.get("difference", {}).items():
                for kind, label in [
                    ("added", "Aggiunto"),
                    ("removed", "Rimosso"),
                    ("changed", "Stato modificato"),
                ]:
                    for name in diff.get(kind, []):
                        tree.insert(
                            "", "end", values=(ed.get("name", ed.get("index")), area, name, label)
                        )
        self.button(
            f,
            "Dettaglio JSON",
            lambda: self.show_text(
                "Report completo", json.dumps(data, ensure_ascii=False, indent=2)
            ),
        ).pack(anchor="e", pady=12)

    def show_text(self, title, text):
        w = tk.Toplevel(self)
        w.title(title)
        w.configure(bg=COLORS["bg"])
        w.geometry("1000x700")
        f = ttk.Frame(w, padding=24)
        f.pack(fill="both", expand=True)
        self.label(f, title, "Title.TLabel", font=(UI_FONT, 18, "bold")).pack(
            anchor="w", pady=(0, 16)
        )
        t = self.text_box(f, 24)
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.configure(state="disabled")
        self.button(f, "Copia", lambda: (self.clipboard_clear(), self.clipboard_append(text))).pack(
            anchor="e", pady=(12, 0)
        )

    def ask_args(self, path, args):
        w = tk.Toplevel(self)
        w.title("Parametri installer")
        w.configure(bg=COLORS["bg"])
        w.transient(self)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=24)
        f.pack(fill="both", expand=True)
        self.label(
            f, os.path.basename(path), "Title.TLabel", font=(UI_FONT, 16, "bold"), wraplength=660
        ).pack(anchor="w")
        self.label(
            f,
            'Array JSON di argomenti. Esempio: ["/S"] oppure ["/VERYSILENT", "/NORESTART"].\nMSI: /qn e /norestart sono già aggiunti. Verifica i parametri del produttore.',
            "Page.TLabel",
            wraplength=660,
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(14, 12))
        var = tk.StringVar(value=json.dumps(args))
        SoftEntry(f, textvariable=var, width=75).pack(fill="x")
        error = tk.StringVar()
        self.label(
            f, "", "Page.TLabel", textvariable=error, foreground=COLORS["red"], wraplength=660
        ).pack(anchor="w", pady=10)
        answer = []

        def ok():
            try:
                value = json.loads(var.get())
                if not isinstance(value, list) or any(
                    not isinstance(x, str) or any(c in x for c in "\r\n\x00") for x in value
                ):
                    raise ValueError("Inserisci un array di stringhe JSON valido.")
                answer.append(value)
                w.destroy()
            except Exception as e:
                error.set(str(e))

        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(10, 0))
        self.button(bar, "Annulla", w.destroy).pack(side="right")
        self.button(bar, "Salva parametri", ok, True).pack(side="right", padx=8)
        w.grab_set()
        self.place_dialog(w)
        self.wait_window(w)
        return answer[0] if answer else None

    def _close(self):
        if self.operation or self.loading_editions:
            if self.loading_editions:
                self.notify("Lettura ISO in corso", "Attendi la lettura delle edizioni.")
                return
            if self.confirm(
                "Annulla e chiudi",
                "Attenderò la fine del comando attivo e lo smontaggio delle immagini prima di chiudere.",
            ):
                self.pending_close = True
                self.cancel_operation()
            return
        if self.session_log:
            try:
                self.session_log.close()
            except OSError:
                pass
        self.destroy()


def scan_script(iso, idx, wim, mnt, out, cancel_file):
    return (
        r"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$disk=$null;$owned=$false;$mounted=$false
function Check-Cancel { if(Test-Path """
        + psq(cancel_file)
        + r""") { throw 'Scansione annullata' } }
try {
 Write-Output 'STUDIO:1/5 • Apertura della ISO'
 $disk=Get-DiskImage -ImagePath """
        + psq(iso)
        + r"""
 if(!$disk.Attached){$disk=Mount-DiskImage -ImagePath """
        + psq(iso)
        + r""" -PassThru;$owned=$true}
 $vol=$disk|Get-Volume
 if(!$vol.DriveLetter){throw 'Lettera ISO non disponibile'}
 Check-Cancel
 $src=([string]$vol.DriveLetter)+':\sources\install.wim'
 if(!(Test-Path $src)){$src=([string]$vol.DriveLetter)+':\sources\install.esd'}
 if(!(Test-Path $src)){throw 'install.wim o install.esd non trovato nella ISO'}
 $image=$src
 $sourceIndex="""
        + str(idx)
        + r"""
 if($src.EndsWith('.esd',[StringComparison]::OrdinalIgnoreCase)) {
  Write-Output 'STUDIO:2/5 • Conversione ESD • può richiedere alcuni minuti'
  Export-WindowsImage -SourceImagePath $src -SourceIndex $sourceIndex -DestinationImagePath """
        + psq(wim)
        + r""" -CompressionType Fast | Out-Null
  $image="""
        + psq(wim)
        + r"""
  $sourceIndex=1
 } else {
  Write-Output 'STUDIO:2/5 • WIM disponibile • esportazione non necessaria'
 }
 Check-Cancel
 Write-Output 'STUDIO:3/5 • Montaggio immagine in sola lettura'
 Mount-WindowsImage -ImagePath $image -Index $sourceIndex -Path """
        + psq(mnt)
        + r""" -ReadOnly -Optimize | Out-Null
 $mounted=$true
 Check-Cancel
 $p="""
        + psq(mnt)
        + r"""
 Write-Output 'STUDIO:4/5 • Lettura delle feature'
 $features=@(Get-WindowsOptionalFeature -Path $p | ForEach-Object {@{Name=$_.FeatureName;State=[string]$_.State}})
 Check-Cancel
 Write-Output 'STUDIO:4/5 • Lettura delle capabilities'
 $capabilities=@(Get-WindowsCapability -Path $p | ForEach-Object {@{Name=$_.Name;State=[string]$_.State}})
 Check-Cancel
 Write-Output 'STUDIO:4/5 • Lettura dei pacchetti'
 $packages=@(Get-WindowsPackage -Path $p | ForEach-Object {@{Name=$_.PackageName;State=[string]$_.PackageState}})
 @{features=$features;capabilities=$capabilities;packages=$packages} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 """
        + psq(out)
        + r"""
} finally {
 Write-Output 'STUDIO:5/5 • Smontaggio e pulizia'
 $cleanupErrors=@()
 if($mounted){try {Dismount-WindowsImage -Path """
        + psq(mnt)
        + r""" -Discard | Out-Null} catch {$cleanupErrors+=($_ | Out-String)}}
 if($owned){try {Dismount-DiskImage -ImagePath """
        + psq(iso)
        + r""" | Out-Null} catch {$cleanupErrors+=($_ | Out-String)}}
 if($cleanupErrors.Count){throw ($cleanupErrors -join '\n')}
 Set-Content -Path """
        + psq(out + ".cleaned")
        + r""" -Value 'clean'
}
"""
    )


def validate_inf_files(path):

    with open(path, "rb") as inf_file:
        raw = inf_file.read()
    text = (
        raw.decode("utf-16")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        else raw.decode("utf-8-sig", errors="replace")
    )
    root = os.path.dirname(path)
    files = {f.lower() for f in os.listdir(root)}
    cats = re.findall(r"^\s*CatalogFile(?:\.[^=\s]+)?\s*=\s*([^;\r\n]+)", text, re.M | re.I)
    for value in cats:
        name = value.strip().strip('"')
        if "%" not in name and name.lower() not in files:
            raise ValueError("Catalogo mancante accanto a " + os.path.basename(path) + ": " + name)
    # Keep subdirectories from SourceDisksFiles; values can use substitutions.
    for section in re.finditer(
        r"^\[SourceDisksFiles(?:\.[^\]]+)?\]\s*\n(.*?)(?=^\[|\Z)", text, re.M | re.S | re.I
    ):
        for row in section.group(1).splitlines():
            row = row.split(";", 1)[0].strip()
            if "=" not in row:
                continue
            name, value = [x.strip() for x in row.split("=", 1)]
            name = name.strip('"')
            parts = [x.strip().strip('"') for x in value.split(",")]
            sub = parts[1] if len(parts) > 1 else ""
            if "%" in name or "%" in sub:
                continue
            candidate = os.path.join(root, sub.replace("\\", os.sep), name)
            # Windows file systems are case insensitive; handle local QA on Linux.
            directory = os.path.dirname(candidate)
            found = (
                os.path.isfile(candidate)
                or os.path.isdir(directory)
                and os.path.basename(candidate).lower()
                in {f.lower() for f in os.listdir(directory)}
            )
            if not found:
                raise ValueError("File driver mancante: " + name + " (INF: " + path + ")")
    return True
