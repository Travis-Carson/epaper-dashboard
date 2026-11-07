#!/usr/bin/env python3
"""Pi-hole, Unbound, connectivity, and guest Wi-Fi e-paper dashboard."""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import qrcode


WIDTH = 250
HEIGHT = 122
LOG = logging.getLogger("guest_epaper")


def load_config(path):
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)

    wifi = config["guest_wifi"]
    if not wifi.get("ssid"):
        raise ValueError("guest_wifi.ssid must not be empty")
    if wifi.get("security", "WPA").upper() != "OPEN" and not wifi.get("password"):
        raise ValueError("Set guest_wifi.password, or set security to OPEN")
    return config


def service_active(service):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def network_details():
    try:
        result = subprocess.run(
            ["ip", "-4", "route", "get", "1.1.1.1"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        route = result.stdout.split()
        source_index = route.index("src") + 1
        return route[source_index]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def internet_reachable():
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            return True
    except OSError:
        return False


def dns_resolves():
    try:
        result = subprocess.run(
            ["getent", "ahostsv4", "example.com"],
            check=False,
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0 and bool(result.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return False


def wifi_qr_data(wifi):
    def escape(value):
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace(":", "\\:")
            .replace('"', '\\"')
        )

    ssid = escape(wifi["ssid"])
    security = wifi.get("security", "WPA").upper()
    if security == "OPEN":
        return f"WIFI:T:nopass;S:{ssid};;"
    if security not in ("WPA", "WEP"):
        raise ValueError("guest_wifi.security must be WPA, WEP, or OPEN")
    return f"WIFI:T:{security};S:{ssid};P:{escape(wifi['password'])};;"


def load_font(size, bold=False):
    font_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    font_path = Path("/usr/share/fonts/truetype/dejavu") / font_name
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError:
        return ImageFont.load_default()


def make_dashboard(config):
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    title_font = load_font(11, bold=True)
    body_font = load_font(10)
    small_font = load_font(8)

    draw.text((5, 3), "NETWORK STATUS", font=title_font, fill=0)
    draw.line((5, 19, WIDTH - 5, 19), fill=0, width=1)

    ip_address = network_details()
    states = [
        ("LAN", ip_address or "OFFLINE"),
        ("WAN", "UP" if internet_reachable() else "DOWN"),
        ("PI-HOLE", "UP" if service_active(config.get("pihole_service", "pihole-FTL")) else "DOWN"),
        ("UNBOUND", "UP" if service_active(config.get("unbound_service", "unbound")) else "DOWN"),
        ("DNS", "OK" if dns_resolves() else "FAIL"),
    ]
    y = 26
    for label, value in states:
        draw.text((5, y), f"{label:<8} {value}", font=body_font, fill=0)
        y += 15

    draw.text((164, 23), "GUEST WI-FI", font=small_font, fill=0)
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=1,
    )
    qr.add_data(wifi_qr_data(config["guest_wifi"]))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("1")
    qr_image.thumbnail((96, 91), Image.Resampling.NEAREST)
    image.paste(qr_image, (149 + (96 - qr_image.width) // 2, 30))

    draw.text((5, 106), datetime.now().strftime("Updated %H:%M"), font=small_font, fill=0)
    return image


def load_display_driver():
    library_path = Path(
        os.environ.get(
            "WAVESHARE_EPAPER_LIB",
            str(Path.home() / "e-Paper/RaspberryPi_JetsonNano/python/lib"),
        )
    )
    sys.path.insert(0, str(library_path))
    try:
        from waveshare_epd import epd2in13_V4
    except ImportError as error:
        raise RuntimeError(
            "Cannot import epd2in13_V4. Set WAVESHARE_EPAPER_LIB to the Waveshare "
            "Python lib directory."
        ) from error
    return epd2in13_V4.EPD()


def display_loop(config, once=False):
    epd = load_display_driver()
    interval = max(30, int(config.get("refresh_seconds", 60)))
    try:
        epd.init()
        while True:
            image = make_dashboard(config)
            epd.display(epd.getbuffer(image))
            LOG.info("Display refreshed")
            if once:
                break
            time.sleep(interval)
    finally:
        epd.sleep()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--once", action="store_true", help="Refresh once, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        display_loop(load_config(args.config), once=args.once)
    except KeyboardInterrupt:
        LOG.info("Stopped")
    except Exception:
        LOG.exception("Dashboard failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())