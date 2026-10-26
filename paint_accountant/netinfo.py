"""This machine's address in the local network."""

from __future__ import annotations

import socket


def lan_ip() -> str | None:
    """Connecting a UDP socket sends no packets; it only makes the OS pick
    the outgoing interface, whose address we then read."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def lan_url(port: int) -> str | None:
    ip = lan_ip()
    return f"http://{ip}:{port}" if ip else None
