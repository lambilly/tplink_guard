"""Whitelist loading helpers for TPLink Guard."""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

_MAC_CLEAN = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(value: str | None) -> str:
    """Normalize a MAC address to 12 uppercase hex digits."""
    if not value:
        return ""
    cleaned = _MAC_CLEAN.sub("", str(value)).upper()
    return cleaned if len(cleaned) == 12 else cleaned


@dataclass
class Whitelist:
    """Known devices indexed by MAC and IP."""

    macs: set[str] = field(default_factory=set)
    ips: set[str] = field(default_factory=set)
    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return max(len(self.macs), len(self.entries))

    def is_known(self, mac: str, ip: str, match_by: str) -> bool:
        normalized_mac = normalize_mac(mac)
        if match_by == "mac":
            return bool(normalized_mac) and normalized_mac in self.macs
        if match_by == "ip":
            return bool(ip) and ip in self.ips
        return (bool(normalized_mac) and normalized_mac in self.macs) or (
            bool(ip) and ip in self.ips
        )

    def display_name(self, mac: str) -> str | None:
        entry = self.entries.get(normalize_mac(mac))
        if not entry:
            return None
        return entry.get("hostname") or entry.get("note") or None


def _read_csv(path: Path) -> Whitelist:
    result = Whitelist()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return result
        headers = {
            str(name).strip().lower(): name
            for name in reader.fieldnames
            if name is not None
        }

        def column(*names: str) -> str | None:
            for name in names:
                if name in headers:
                    return headers[name]
            return None

        mac_col = column("mac", "macaddr", "mac_address")
        ip_col = column("ip", "ipaddr", "ip_address")
        hostname_col = column("hostname", "host", "name")
        vendor_col = column("vendor", "manufacturer")
        note_col = column("note", "remark", "备注")

        for row in reader:
            mac = normalize_mac(row.get(mac_col) if mac_col else None)
            ip = (row.get(ip_col) or "").strip() if ip_col else ""
            if not mac and not ip:
                continue
            if mac:
                result.macs.add(mac)
            if ip:
                result.ips.add(ip)
            entry: dict[str, str] = {
                "mac": mac,
                "ip": ip,
                "hostname": (row.get(hostname_col) or "").strip() if hostname_col else "",
                "vendor": (row.get(vendor_col) or "").strip() if vendor_col else "",
                "note": (row.get(note_col) or "").strip() if note_col else "",
            }
            if mac:
                result.entries[mac] = entry
    return result


_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def _parse_whitelist_note(comment: str) -> tuple[str, str]:
    """Parse a whitelist note into ``(name, ip)``.

    Unified format uses comma-separated fields: ``名称,IP``. The legacy
    ``名称 (IP)`` form is still accepted. The IP is extracted wherever it
    appears so IP-based matching keeps working.
    """
    text = (comment or "").strip()
    if not text:
        return "", ""
    ip_match = _IPV4.search(text)
    ip = ip_match.group(0) if ip_match else ""
    name = text.split(",")[0].strip()
    name = name.split("(")[0].strip()
    return name, ip


def _read_plain(path: Path) -> Whitelist:
    result = Whitelist()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Current format: ``MAC, IP, 名称`` (whitelist) or
        # ``MAC, IP, 姓名, 昵称`` (guestphone) — no ``#`` at all.
        # Legacy formats: ``MAC, IP, #名称`` and ``MAC # 名称,IP``.
        if "#" in line:
            value, comment = line.split("#", 1)
            value = value.strip()
            comment = comment.strip()
        else:
            value = stripped
            comment = ""
        if not value:
            continue

        fields = [field.strip() for field in value.split(",")]
        mac_value = fields[0] if fields else ""
        ip = ""
        if len(fields) >= 2 and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", fields[1]):
            ip = fields[1]

        # Names live in the trailing columns of the current format; legacy
        # notes keep them after ``#``. Empty IP columns (``MAC,,名称``) are
        # preserved by splitting without dropping empty fields.
        note = comment or (",".join(fields[2:]) if len(fields) >= 3 else "")

        if re.fullmatch(r"[0-9a-fA-F:-]{12,17}", mac_value):
            mac = normalize_mac(mac_value)
            if len(mac) == 12:
                name, legacy_ip = _parse_whitelist_note(note)
                ip = ip or legacy_ip
                if ip:
                    result.ips.add(ip)
                result.macs.add(mac)
                result.entries[mac] = {
                    "mac": mac,
                    "ip": ip,
                    "hostname": name,
                    "vendor": "",
                    # Keep the comma-separated name/nickname/device note so
                    # watchlist entries can be split by split_watch_note().
                    "note": note,
                }
        elif re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", mac_value):
            result.ips.add(mac_value)
    return result


def split_watch_note(note: str | None) -> tuple[str, str, str]:
    """Split a watchlist note into ``(name, nickname, device)``.

    Unified format uses comma-separated fields: ``姓名,昵称,设备名``. The
    legacy ``姓名|昵称|设备名`` and ``姓名 设备名`` forms are also accepted,
    with the nickname falling back to the name.
    """
    value = (note or "").strip()
    if not value:
        return "", "", ""
    if "," in value:
        separator = ","
    elif "|" in value:
        separator = "|"
    else:
        separator = None
    if separator:
        parts = [part.strip() for part in value.split(separator)]
        parts += ["", "", ""]
        name = parts[0]
        nickname = parts[1] or name
        device = parts[2]
    else:
        words = value.split()
        name = words[0] if words else ""
        nickname = name
        device = " ".join(words[1:]) if len(words) > 1 else ""
    return name, nickname, device


def load_whitelist(path: str | os.PathLike[str] | None) -> Whitelist:
    """Load a whitelist from CSV or a plain MAC/IP-per-line text file."""
    if not path:
        return Whitelist()
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("Whitelist file not found: %s", file_path)
        return Whitelist()
    try:
        if file_path.suffix.lower() == ".csv":
            return _read_csv(file_path)
        return _read_plain(file_path)
    except (OSError, UnicodeError, csv.Error) as err:
        _LOGGER.error("Failed to read whitelist %s: %s", file_path, err)
        return Whitelist()


def load_monitored(path: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """Load the device-terminal list (``lists/device.txt``).

    Each line is ``MAC, IP, 显示名, 实体名``. Column 2 (IP) is an optional
    direct-probe address: when present, a reachable IP marks the terminal
    online even if the router mislabels it offline (``MAC,,显示名,实体名``
    keeps the MAC-only behaviour). Column 3 is the friendly display name,
    column 4 the entity id used for ``device_tracker.tplink_guard_<id>``.
    The legacy ``MAC,实体名,显示名`` 3-column form is still accepted.
    A missing entity id is auto-filled later from the router hostname.
    Comment lines start with ``#``. The function name is kept for API
    stability; the user-facing file name is now ``device.txt``.
    """
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("Monitored list file not found: %s", file_path)
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        lines = file_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as err:
        _LOGGER.error("Failed to read monitored list %s: %s", file_path, err)
        return []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        body = text.split("#", 1)[0]
        # Keep empty fields so the reserved IP column (MAC,,name,id) does
        # not shift the display name / entity id into the wrong slots.
        fields = [field.strip() for field in body.split(",")]
        if not fields or not any(fields):
            continue
        mac = normalize_mac(fields[0])
        if len(mac) != 12:
            _LOGGER.debug("Skipping invalid monitored line: %s", text)
            continue
        if mac in seen:
            continue
        seen.add(mac)
        # New 4-column form: MAC, IP, 显示名, 实体名. The IP is the LAN
        # fallback probe target; empty columns keep MAC-only matching.
        # New 3-column form: MAC, IP, 显示名.
        # Legacy 3-column form: MAC, 实体名, 显示名 (no IP in column 2).
        ip = ""
        if len(fields) >= 3 and re.fullmatch(
            r"\d{1,3}(?:\.\d{1,3}){3}", fields[1]
        ):
            ip = fields[1]
        if len(fields) >= 4:
            name = fields[2]
            entity_id = fields[3]
        elif ip:
            name = fields[2]
            entity_id = ""
        else:
            entity_id = fields[1] if len(fields) > 1 else ""
            name = fields[2] if len(fields) > 2 else ""
        result.append(
            {"mac": mac, "ip": ip, "entity_id": entity_id, "name": name}
        )
    return result


def load_aps(path: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """Load the AP list.

    New format is ``MAC, IP, 名称``: the probe target is the IP (column 2)
    and the display name is column 3. An empty IP column (``MAC,,名称``)
    is skipped. The legacy ``IP, 名称`` form is still accepted.

    APs are probed directly over the LAN because AC-managed APs never
    appear in the router's host_info table.
    """
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("AP list file not found: %s", file_path)
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        lines = file_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as err:
        _LOGGER.error("Failed to read AP list %s: %s", file_path, err)
        return []
    for line in lines:
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = [part.strip() for part in body.split(",")]
        # New 3-column form: MAC, IP, 名称 — probe IP is column 2, name is 3.
        # Legacy 2-column form: IP, 名称 — probe IP is column 1, name is 2.
        first = parts[0] if parts else ""
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", first):
            ip = first
            name = parts[1] if len(parts) > 1 else ""
        elif len(parts) >= 2 and re.fullmatch(
            r"\d{1,3}(?:\.\d{1,3}){3}", parts[1]
        ):
            ip = parts[1]
            name = parts[2] if len(parts) > 2 else ""
        else:
            # Empty IP column (MAC,,name) or invalid row: skip entirely.
            _LOGGER.debug("Skipping invalid/empty-IP AP line: %s", line)
            continue
        if not ip or ip in seen:
            continue
        seen.add(ip)
        result.append({"ip": ip, "name": name or ip})
    return result


def load_checklist(path: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """Load a generic online-check list (gateway / ASR / server / router).

    Two line forms: ``IP, name`` probes the IP directly, and
    ``entity_id, name, rule, expected`` reads an HA entity state where
    ``rule`` is ``eq`` (equals, the default) or ``ne`` (differs from).
    Direct probes avoid depending on third-party device_tracker entities.
    """
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("Checklist file not found: %s", file_path)
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        lines = file_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as err:
        _LOGGER.error("Failed to read checklist %s: %s", file_path, err)
        return []
    for line in lines:
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = [part.strip() for part in body.split(",")]
        key = parts[0]
        if not key or key in seen:
            _LOGGER.debug("Skipping invalid checklist line: %s", line)
            continue
        seen.add(key)
        name = parts[1] if len(parts) > 1 and parts[1] else key
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", key):
            # Direct probe: no HA entity needed, works after removing
            # third-party device_tracker integrations.
            result.append(
                {
                    "kind": "probe",
                    "target": key,
                    "entity_id": key,
                    "name": name,
                    "op": "eq",
                    "expected": "",
                }
            )
            continue
        if "." not in key:
            _LOGGER.debug("Skipping invalid checklist line: %s", line)
            continue
        op = (parts[2] if len(parts) > 2 else "eq").lower()
        if op not in ("eq", "ne", "equal", "not_equal"):
            op = "eq"
        result.append(
            {
                "kind": "state",
                "target": key,
                "entity_id": key,
                "name": name,
                "op": "ne" if op in ("ne", "not_equal") else "eq",
                "expected": parts[3] if len(parts) > 3 else "",
            }
        )
    return result


def load_checklist_by_mac(path: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """Load an online-check list whose columns are MAC, IP, 中文名, 英文名.

    Every data line is ``MAC,IP,中文名,英文名`` (``#`` starts a comment):

    * ``MAC`` is matched against the router's online host table (the
      reliable path used for ASR, NAS, gateways, etc.);
    * ``IP`` is an optional direct-probe address. Dynamic/absent IPs use an
      empty field (``MAC,,中文名,英文名``). When present, a live IP probe
      also resolves the device even if the router mislabels its state.
    * the legacy 3-column form ``标识,中文名,英文名`` is still accepted
      (``标识`` being a MAC or an IP).

    No HA entity state is consulted. Duplicate keys are ignored.
    """
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("MAC checklist file not found: %s", file_path)
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        lines = file_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as err:
        _LOGGER.error("Failed to read MAC checklist %s: %s", file_path, err)
        return []
    for line in lines:
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = [part.strip() for part in body.split(",")]
        key = parts[0]
        if not key or key in seen:
            continue

        # New 4-column form: MAC, IP, 中文名, 英文名. When the second column
        # is an IPv4 (or the row has 4 columns with an empty IP), names start
        # at column 3; otherwise fall back to the legacy 3-column layout.
        ip = ""
        if len(parts) >= 4:
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", parts[1]):
                ip = parts[1]
            name_cn = parts[2] or key
            name_en = parts[3]
        else:
            name_cn = parts[1] if len(parts) > 1 and parts[1] else key
            name_en = parts[2] if len(parts) > 2 and parts[2] else ""

        mac = normalize_mac(key)
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", key):
            item = {
                "kind": "probe",
                "target": key,
                "entity_id": key,
                "name_cn": name_cn,
                "name_en": name_en,
                "probe_ip": key,
                "op": "eq",
                "expected": "",
            }
        elif len(mac) == 12 and mac.upper() == key.replace("-", "").replace(":", "").upper():
            item = {
                "kind": "mac",
                "target": mac,
                "mac": mac,
                "entity_id": name_en or mac,
                "name_cn": name_cn,
                "name_en": name_en,
                "probe_ip": ip,
                "op": "eq",
                "expected": "",
            }
        else:
            # Lists are IP/MAC only; entity-id rows are no longer accepted.
            _LOGGER.debug("Skipping non-IP/MAC checklist line: %s", line)
            continue
        seen.add(key)
        result.append(item)
    return result


def load_gateways(path: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """Backward-compatible alias for the gateway list."""
    return load_checklist(path)


def whitelist_mtime(path: str | os.PathLike[str] | None) -> float:
    """Return file mtime so changes can be picked up without a reload."""
    if not path:
        return 0.0
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def summarize_device(
    host: dict[str, Any], known: bool, display_name: str | None
) -> dict[str, Any]:
    """Normalize a router host row into a stable device dictionary.

    Keeps the router's LAN context (SSID / AP / first-seen time / signal)
    so notifications about unknown terminals can say more than an IP.
    """
    mac = normalize_mac(host.get("mac") or host.get("macaddr") or "")
    return {
        "ip": str(host.get("ip") or ""),
        "mac": mac,
        "hostname": str(host.get("hostname") or ""),
        "display_name": display_name or str(host.get("hostname") or "") or mac,
        "vendor": str(host.get("vendor") or ""),
        "state": str(host.get("state") or ""),
        "type": str(host.get("type") or ""),
        "known": known,
        # LAN context from host_info — used by unknown_online_detail.
        "ssid": str(host.get("ssid") or ""),
        "ap_name": str(host.get("ap_name") or ""),
        "connect_date": str(host.get("connect_date") or ""),
        "connect_time": str(host.get("connect_time") or ""),
        "rssi": str(host.get("rssi") or ""),
        "freq_name": str(host.get("freq_name") or ""),
        "interface": str(host.get("interface") or ""),
        "dev_state": str(host.get("dev_state") or ""),
    }
