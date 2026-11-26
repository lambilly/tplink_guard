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
        if "#" in line:
            value, comment = line.split("#", 1)
            value = value.strip()
            comment = comment.strip()
        else:
            value = line.strip()
            comment = ""
        if not value:
            continue
        if re.fullmatch(r"[0-9a-fA-F:-]{12,17}", value):
            mac = normalize_mac(value)
            if len(mac) == 12:
                name, ip = _parse_whitelist_note(comment)
                if ip:
                    result.ips.add(ip)
                result.macs.add(mac)
                result.entries[mac] = {
                    "mac": mac,
                    "ip": ip,
                    "hostname": name,
                    "vendor": "",
                    # Keep the full comma-separated note so watchlist
                    # entries can be split into name/nickname/device.
                    "note": comment,
                }
        elif re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value):
            result.ips.add(value)
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
    """Load the monitored-terminal list.

    Each line defines a terminal that gets its own device_tracker entity:
    ``MAC, entity_id, 备注``. The second field decides the entity id and
    name; the third field is a documentation-only note and is ignored.
    A missing entity id is auto-filled later from the router hostname.
    Comment lines start with ``#``.
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
        fields = [field.strip() for field in body.split(",") if field.strip()]
        if not fields:
            continue
        mac = normalize_mac(fields[0])
        if len(mac) != 12:
            _LOGGER.debug("Skipping invalid monitored line: %s", text)
            continue
        if mac in seen:
            continue
        seen.add(mac)
        result.append(
            {
                "mac": mac,
                "entity_id": fields[1] if len(fields) > 1 else "",
                "name": fields[2] if len(fields) > 2 else "",
            }
        )
    return result


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
    """Normalize a router host row into a stable device dictionary."""
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
    }
