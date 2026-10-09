"""Platform independent project validation, persistence and integrity checks."""

import hashlib
import json
import os
import re
import tempfile
from .base import BuildError, safe_dest


def atomic_json(path, value):
    path = os.fspath(path)
    folder = os.path.dirname(os.path.abspath(path))
    handle, pending = tempfile.mkstemp(prefix=".winslim-", suffix=".json", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def fingerprint(path):
    stat = os.stat(path)
    return (
        os.path.normcase(os.path.realpath(path)),
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ino,
        stat.st_dev,
    )


def sha256_file(path, cancel=None):
    before = fingerprint(path)
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            if cancel and cancel.is_set():
                raise BuildError("Calcolo SHA-256 annullato.")
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    if fingerprint(path) != before:
        raise BuildError("Il file è cambiato durante il calcolo SHA-256.")
    return digest.hexdigest()


def within(root, path):
    root = os.path.normcase(os.path.realpath(root))
    path = os.path.normcase(os.path.realpath(path))
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def same_file(a, b):
    if os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except (FileNotFoundError, OSError):
        return False


def component_records(lines, field):
    """Read canonical identities from DISM /English inventory output."""
    identity_key = {
        "features": "Feature Name",
        "capabilities": "Capability Identity",
        "packages": "Package Identity",
    }[field]
    records = {}
    current = None
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key.strip() == identity_key:
            current = value.strip()
            records[current] = ""
        elif current and key.strip() == "State":
            records[current] = value.strip()
    return records


def resolve_components(extra, inventory, log, optional_capabilities=()):
    """Resolve names against this edition; never pass arbitrary names to DISM."""
    resolved = {field: [] for field in ("features", "capabilities", "packages")}
    decisions = []
    for field in resolved:
        available = component_records(inventory.get(field, []), field)
        for requested in extra.get(field, []):
            candidates = [n for n in available if n.casefold() == requested.casefold()]
            if field == "capabilities" and not candidates:
                if "~" not in requested:
                    candidates = [
                        n for n in available if n.casefold().startswith(requested.casefold() + "~")
                    ]
                elif re.fullmatch(r"\d+(?:\.\d+){3}", requested.rsplit("~", 1)[-1]):
                    identity = requested.rsplit("~", 1)[0].casefold()
                    candidates = [
                        n for n in available if n.rsplit("~", 1)[0].casefold() == identity
                    ]
            if len(candidates) > 1:
                raise BuildError(
                    "Nome componente ambiguo: "
                    + requested
                    + ". Scegli il nome completo dal catalogo: "
                    + ", ".join(candidates)
                )
            if not candidates:
                base = requested.split("~", 1)[0].casefold()
                if field == "capabilities" and base in {
                    n.casefold() for n in optional_capabilities
                }:
                    log(
                        "Componente standard non disponibile in questa edizione, nessuna rimozione: "
                        + requested
                    )
                    decisions.append(
                        {
                            "area": field,
                            "requested": requested,
                            "resolved": None,
                            "status": "unavailable",
                        }
                    )
                    continue
                raise BuildError(
                    "Componente non presente nel catalogo di questa edizione ("
                    + field
                    + "): "
                    + requested
                    + ". Riscansiona la ISO e seleziona il nome completo; controlla anche la versione di DISM/Windows ADK."
                )
            name = candidates[0]
            state = available[name]
            if field == "capabilities" and not state:
                raise BuildError("Stato del componente non rilevato: " + name)
            if field == "capabilities" and state.replace(" ", "").casefold() == "notpresent":
                log("Componente già assente, nessuna rimozione: " + name)
                decisions.append(
                    {
                        "area": field,
                        "requested": requested,
                        "resolved": name,
                        "status": "already_absent",
                    }
                )
                continue
            if name != requested:
                log("Nome componente risolto dal catalogo: " + requested + " → " + name)
            if name not in resolved[field]:
                resolved[field].append(name)
            decisions.append(
                {"area": field, "requested": requested, "resolved": name, "status": "selected"}
            )
    return resolved, decisions


def validate_build_paths(cfg, root):
    inputs = [cfg["iso"]] + cfg.get("regfiles", [])
    extra = cfg.get("extra", {})
    inputs += extra.get("drivers", []) + extra.get("updates", [])
    inputs += [i["path"] for i in extra.get("software", [])]
    inputs += [i["src"] for i in cfg.get("files", [])]
    if extra.get("power"):
        inputs.append(extra["power"])
    for path in inputs:
        if within(root, path):
            raise BuildError("Una sorgente è nella cartella di lavoro: " + path)
        if same_file(path, cfg["out"]):
            raise BuildError("La ISO di uscita coincide con una sorgente: " + path)
        if os.path.isdir(path) and within(path, root):
            raise BuildError("La cartella sorgente contiene la cartella di lavoro: " + path)
    if within(root, cfg["out"]):
        raise BuildError("La ISO di uscita deve essere fuori dalla cartella di lavoro.")
    if not os.path.basename(cfg["out"]) or os.path.isdir(cfg["out"]):
        raise BuildError("Il percorso di uscita deve essere un file.")
    for item in cfg.get("files", []):
        safe_dest(root, item["dest"])


def validate_config(cfg, variables, catalogs, ui_rules, protected):
    """Validate every field before changing any Tk controls. No path access here."""
    if not isinstance(cfg, dict) or type(cfg.get("schema")) is not int or cfg["schema"] != 2:
        raise ValueError("Schema configurazione non supportato (richiesto 2).")
    allowed = {
        "schema",
        "version",
        "variables",
        "editions",
        "selected_indexes",
        "appx",
        "caps",
        "tweaks",
        "regfiles",
        "files",
        "extra",
    }
    if set(cfg) - allowed:
        raise ValueError(
            "Campi di configurazione sconosciuti: " + ", ".join(sorted(set(cfg) - allowed))
        )
    vals = cfg.get("variables", {})
    if not isinstance(vals, dict):
        raise ValueError("Variabili non valide.")
    for key, val in vals.items():
        if key not in variables or key == "v_pwd" or type(val) is not variables[key]:
            raise ValueError("Variabile o tipo non valido: " + key)
        if isinstance(val, str) and any(ch in val for ch in "\x00\r\n"):
            raise ValueError("Caratteri di controllo non ammessi: " + key)
    for field, names in catalogs.items():
        opts = cfg.get(field, {})
        if not isinstance(opts, dict) or any(
            k not in names or type(v) is not bool for k, v in opts.items()
        ):
            raise ValueError("Opzioni non valide: " + field)
    editions = cfg.get("editions", [])
    if not isinstance(editions, list) or any(
        not isinstance(e, (list, tuple))
        or len(e) != 2
        or type(e[0]) is not int
        or e[0] < 1
        or not isinstance(e[1], str)
        or not e[1]
        for e in editions
    ):
        raise ValueError("Edizioni non valide.")
    indexes = [e[0] for e in editions]
    if len(indexes) != len(set(indexes)) or len({e[1] for e in editions}) != len(editions):
        raise ValueError("Edizioni duplicate.")
    selected = cfg.get("selected_indexes", [])
    if (
        not isinstance(selected, list)
        or any(type(i) is not int or i not in indexes for i in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError("Selezione edizioni non valida.")

    def strings(values, field):
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v or any(c in v for c in "\x00\r\n") for v in values
        ):
            raise ValueError("Lista non valida: " + field)

    strings(cfg.get("regfiles", []), "regfiles")
    files = cfg.get("files", [])
    if not isinstance(files, list):
        raise ValueError("Lista file non valida.")
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"src", "where", "dest", "contents"}
            or any(not isinstance(item[k], str) for k in ("src", "where", "dest"))
            or not item["src"]
            or item["where"] not in ("win", "iso")
            or type(item["contents"]) is not bool
        ):
            raise ValueError("File aggiuntivo non valido.")
        safe_dest(os.getcwd(), item["dest"])
    extra = cfg.get("extra", {})
    if not isinstance(extra, dict) or set(extra) - {
        "drivers",
        "updates",
        "software",
        "services",
        "ui",
        "power",
        "drivers_boot",
        "features",
        "capabilities",
        "packages",
    }:
        raise ValueError("Configurazione avanzata non valida.")
    for field in ("drivers", "updates", "features", "capabilities", "packages"):
        strings(extra.get(field, []), field)
    for field in ("features", "capabilities", "packages"):
        if any(not re.fullmatch(r"[A-Za-z0-9_.~\-]+", n) for n in extra.get(field, [])):
            raise ValueError("Nome componente non valido: " + field)
    services = extra.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("Servizi non validi.")
    for name, mode in services.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_.\-]+", name)
            or type(mode) is not int
            or mode not in (2, 3, 4)
            or name.casefold() in {p.casefold() for p in protected}
        ):
            raise ValueError("Servizio non valido o protetto: " + str(name))
    ui = extra.get("ui", {})
    if not isinstance(ui, dict) or any(
        k not in ui_rules or type(v) is not bool for k, v in ui.items()
    ):
        raise ValueError("Opzioni UI non valide.")
    if (
        not isinstance(extra.get("power", ""), str)
        or type(extra.get("drivers_boot", False)) is not bool
    ):
        raise ValueError("Piano energetico o driver boot non validi.")
    software = extra.get("software", [])
    if not isinstance(software, list):
        raise ValueError("Lista software non valida.")
    for item in software:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "args"}
            or not isinstance(item["path"], str)
            or not item["path"]
        ):
            raise ValueError("Installer non valido.")
        args = item["args"]
        if not isinstance(args, list) or any(
            not isinstance(v, str) or any(c in v for c in "\x00\r\n") for v in args
        ):
            raise ValueError("Argomenti installer non validi.")
    return cfg


def apply_config(app, cfg):
    """Called only after validation; reset omitted choices, never restore passwords."""
    for key, value in app.default_config["variables"].items():
        getattr(app, key).set(value)
    for key, value in cfg.get("variables", {}).items():
        getattr(app, key).set(value)
    if cfg.get("variables", {}).get("v_defender"):
        app.log(
            "Configurazione precedente: richiesta di rimozione Defender ignorata; protezione preservata."
        )
    app.v_defender.set(False)
    app.v_pwd.set("")
    for field, mapping in [
        ("appx", app.appx_vars),
        ("caps", app.cap_vars),
        ("tweaks", app.tw_vars),
    ]:
        for key, var in mapping.items():
            var.set(cfg.get(field, {}).get(key, False))
    app.editions = [tuple(e) for e in cfg.get("editions", [])]
    app.lbed.delete(0, "end")
    for i, (index, name) in enumerate(app.editions):
        app.lbed.insert("end", "%02d   %s" % (index, name))
        if index in cfg.get("selected_indexes", []):
            app.lbed.selection_set(i)
    app.refresh_auto()
    app.lb.delete(0, "end")
    for path in cfg.get("regfiles", []):
        app.lb.insert("end", path)
    app.files = []
    for item in app.tv.get_children():
        app.tv.delete(item)
    for item in cfg.get("files", []):
        app.add_file(item)
    extra = cfg.get("extra", {})
    for field in app.plus_lists:
        app.plus_lists[field] = json.loads(json.dumps(extra.get(field, [])))
        app.refresh_list(field)
    for box in app.plus_text.values():
        box.delete("1.0", "end")
    inverse = {2: "Automatico", 3: "Manuale", 4: "Disabilitato"}
    for name, var in app.service_vars.items():
        var.set(inverse.get(extra.get("services", {}).get(name), "Invariato"))
    for name, mode in extra.get("services", {}).items():
        if name not in app.service_vars:
            app.plus_text["services"].insert("end", name + "=" + inverse[mode] + "\n")
    for field in ("features", "capabilities", "packages"):
        app.component_choices[field] = list(dict.fromkeys(extra.get(field, [])))
    for name, var in app.ui_vars.items():
        var.set(extra.get("ui", {}).get(name, False))
    app.v_power.set(extra.get("power", ""))
    app.v_bootdrivers.set(extra.get("drivers_boot", False))
    app.last_editions_iso = ""
    app.editions_fingerprint = None
    app.scan_records = []
    app.search_components.set("")
    app.component_kind.set("Tutti")
    app.component_state.set("Tutti gli stati")
    app.filter_components()
    app.refresh_summary()
