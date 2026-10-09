"""Persistent catalog cache; only completed inventories of the same ISO/edition."""

import hashlib
import json
import os

from .safety import atomic_json, fingerprint


def scan_identity(iso, index):
    # Include file identity and creation/change time, beyond path/size/mtime.
    return (1, *fingerprint(iso), os.stat(iso).st_ctime_ns, index)


class CatalogCache:
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, folder):
        self.folder = os.fspath(folder)

    def path(self, identity):
        digest = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()
        return os.path.join(self.folder, digest + ".json")

    @staticmethod
    def valid_rows(rows):
        return (
            isinstance(rows, list)
            and len(rows) <= 10000
            and all(
                isinstance(row, (list, tuple))
                and len(row) == 3
                and row[0] in ("features", "capabilities", "packages")
                and isinstance(row[1], str)
                and 0 < len(row[1]) <= 1024
                and isinstance(row[2], str)
                and len(row[2]) <= 160
                and not any(ch in row[1] + row[2] for ch in "\x00\r\n")
                for row in rows
            )
        )

    def load(self, identity):
        try:
            with open(self.path(identity), "rb") as stream:
                payload = stream.read(self.MAX_BYTES + 1)
            if len(payload) > self.MAX_BYTES:
                return None
            data = json.loads(payload)
            if data.get("identity") != list(identity) or not self.valid_rows(data.get("rows")):
                return None
            return [tuple(row) for row in data["rows"]]
        except (OSError, ValueError, AttributeError, TypeError):
            return None

    def save(self, identity, rows):
        if not self.valid_rows(rows):
            return False
        data = {"identity": list(identity), "rows": rows}
        if len(json.dumps(data).encode("utf-8")) > self.MAX_BYTES:
            return False
        try:
            os.makedirs(self.folder, exist_ok=True)
            atomic_json(self.path(identity), data)
            return True
        except OSError:
            return False
