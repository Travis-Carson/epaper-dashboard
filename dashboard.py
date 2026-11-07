#!/usr/bin/env python3
"""Pi-hole, Unbound, connectivity, and guest Wi-Fi e-paper dashboard."""

import argparse
import getpass
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

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
    from PIL import ImageFont

    font_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    font_path = Path("/usr/share/fonts/truetype/dejavu") / font_name
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError:
        return ImageFont.load_default()


def draw_homunculus(draw, mood, animation_frame):
    draw.polygon([(10, 81), (52, 81), (65, 119), (2, 119)], fill=0)
    draw.polygon([(19, 88), (43, 88), (50, 116), (14, 116)], fill=1)
    draw.line((31, 91, 31, 116), fill=0, width=1)

    draw.ellipse((8, 46, 59, 91), fill=1, outline=0, width=2)
    draw.ellipse((12, 39, 33, 63), fill=1, outline=0, width=2)
    draw.ellipse((35, 39, 56, 63), fill=1, outline=0, width=2)

    draw.polygon([(5, 45), (34, 20), (63, 45)], fill=0)
    draw.ellipse((4, 41, 64, 50), fill=0)
    draw.polygon([(34, 31), (37, 36), (42, 37), (38, 40), (39, 45), (34, 42), (29, 45), (30, 40), (26, 37), (31, 36)], fill=1)

    pupil_shift = animation_frame % 2
    draw.ellipse((20 + pupil_shift, 49 + pupil_shift, 26 + pupil_shift, 56 + pupil_shift), fill=0)
    draw.ellipse((43 + pupil_shift, 49 + pupil_shift, 49 + pupil_shift, 56 + pupil_shift), fill=0)

    if mood == "CHEERFUL":
        draw.arc((22, 64, 46, 81), 0, 180, fill=0, width=2)
    elif mood == "WORRIED":
        draw.line((19, 47, 29, 50), fill=0, width=2)
        draw.line((39, 50, 50, 47), fill=0, width=2)
        draw.arc((23, 69, 45, 83), 180, 360, fill=0, width=2)
    else:
        draw.ellipse((28, 66, 39, 79), fill=0)

    wand_tip = (61, 58) if animation_frame % 2 else (59, 69)
    draw.ellipse((47, 86, 55, 94), fill=1, outline=0, width=1)
    draw.line((51, 89, wand_tip[0], wand_tip[1]), fill=0, width=2)
    draw.ellipse((wand_tip[0] - 2, wand_tip[1] - 2, wand_tip[0] + 2, wand_tip[1] + 2), fill=0)
    if animation_frame % 2 == 0:
        sparkle_x, sparkle_y = 63, 56
        draw.polygon(
            [
                (sparkle_x, sparkle_y - 6),
                (sparkle_x + 2, sparkle_y - 2),
                (sparkle_x + 6, sparkle_y),
                (sparkle_x + 2, sparkle_y + 2),
                (sparkle_x, sparkle_y + 6),
                (sparkle_x - 2, sparkle_y + 2),
                (sparkle_x - 6, sparkle_y),
                (sparkle_x - 2, sparkle_y - 2),
            ],
            fill=0,
        )


def make_dashboard(config, animation_frame=0):
    from PIL import Image, ImageDraw, ImageFont
    import qrcode

    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    title_font = load_font(10, bold=True)
    body_font = load_font(8, bold=True)
    small_font = load_font(7)

    ip_address = network_details()
    wan_up = internet_reachable()
    pihole_up = service_active(config.get("pihole_service", "pihole-FTL"))
    unbound_up = service_active(config.get("unbound_service", "unbound"))
    dns_up = dns_resolves()

    if not ip_address or not wan_up:
        mood = "ALERT"
    elif not (pihole_up and unbound_up and dns_up):
        mood = "WORRIED"
    else:
        mood = "CHEERFUL"

    draw.text((5, 3), "HOMONCULUS", font=title_font, fill=0)
    mood_width = draw.textbbox((0, 0), mood, font=small_font)[2]
    draw.text((247 - mood_width, 5), mood, font=small_font, fill=0)
    draw.line((4, 17, WIDTH - 4, 17), fill=0, width=1)

    draw_homunculus(draw, mood, animation_frame)

    draw.text((70, 27), f"IP {ip_address or 'unavailable'}", font=small_font, fill=0)
    statuses = [
        ("LAN", "UP" if ip_address else "DOWN"),
        ("WAN", "UP" if wan_up else "DOWN"),
        ("PI-HOLE", "UP" if pihole_up else "DOWN"),
        ("UNBOUND", "UP" if unbound_up else "DOWN"),
        ("DNS", "OK" if dns_up else "FAIL"),
    ]
    for row, (label, value) in enumerate(statuses):
        y = 42 + row * 13
        draw.text((70, y), label, font=small_font, fill=0)
        value_width = draw.textbbox((0, 0), value, font=body_font)[2]
        draw.text((155 - value_width, y), value, font=body_font, fill=0)

    draw.text((177, 19), "GUEST WI-FI", font=small_font, fill=0)
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=1,
    )
    qr.add_data(wifi_qr_data(config["guest_wifi"]))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("1")
    qr_image.thumbnail((88, 90), Image.Resampling.NEAREST)
    image.paste(qr_image, (158 + (88 - qr_image.width) // 2, 29))

    draw.text((70, 111), datetime.now().strftime("Updated %H:%M"), font=small_font, fill=0)
    return image


def load_display_driver():
    library_paths = []
    configured_path = os.environ.get("WAVESHARE_EPAPER_LIB")
    if configured_path:
        library_paths.append(Path(configured_path))

    library_paths.append(Path.home() / "e-Paper/RaspberryPi_JetsonNano/python/lib")
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd

            library_paths.append(
                Path(pwd.getpwnam(sudo_user).pw_dir)
                / "e-Paper/RaspberryPi_JetsonNano/python/lib"
            )
        except (ImportError, KeyError):
            pass
    library_paths.append(
        Path("/opt/guest-epaper/waveshare-e-Paper/RaspberryPi_JetsonNano/python/lib")
    )

    for library_path in dict.fromkeys(library_paths):
        if (library_path / "waveshare_epd").is_dir():
            sys.path.insert(0, str(library_path))
            break
    try:
        from waveshare_epd import epd2in13_V4
    except ImportError as error:
        raise RuntimeError(
            "Cannot import epd2in13_V4. Searched: "
            + ", ".join(str(path) for path in library_paths)
            + ". Set WAVESHARE_EPAPER_LIB to the Waveshare Python lib directory."
        ) from error
    return epd2in13_V4.EPD()


def display_loop(config, once=False):
    epd = load_display_driver()
    interval = max(30, int(config.get("refresh_seconds", 60)))
    animation_frame = 0
    try:
        epd.init()
        while True:
            image = make_dashboard(config, animation_frame)
            epd.display(epd.getbuffer(image))
            LOG.info("Display refreshed")
            if once:
                break
            animation_frame += 1
            time.sleep(interval)
    finally:
        epd.sleep()


def install_dashboard():
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise RuntimeError("Run this on Raspberry Pi OS as: sudo python3 dashboard.py --install")

    import pwd

    service_user = os.environ.get("SUDO_USER")
    if not service_user or service_user == "root":
        raise RuntimeError("Run the installer with sudo from your normal Raspberry Pi user account")
    account = pwd.getpwnam(service_user)

    install_dir = Path("/opt/guest-epaper")
    driver_dir = install_dir / "waveshare-e-Paper"
    driver_lib = driver_dir / "RaspberryPi_JetsonNano/python/lib"
    config_path = install_dir / "config.json"
    service_path = Path("/etc/systemd/system/guest-epaper.service")

    LOG.info("Installing required Raspberry Pi packages")
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(
        [
            "apt-get",
            "install",
            "-y",
            "git",
            "python3-venv",
            "python3-pip",
            "python3-pil",
            "python3-numpy",
            "python3-spidev",
            "python3-rpi.gpio",
            "python3-lgpio",
        ],
        check=True,
    )
    subprocess.run(["usermod", "-aG", "spi,gpio", service_user], check=True)

    spi_device = Path("/dev/spidev0.0")
    spi_reboot_needed = False
    if not spi_device.exists() and shutil.which("raspi-config"):
        result = subprocess.run(
            ["raspi-config", "nonint", "do_spi", "0"],
            check=False,
        )
        spi_reboot_needed = result.returncode == 0

    install_dir.mkdir(parents=True, exist_ok=True)
    source_script = Path(__file__).resolve()
    installed_script = install_dir / "dashboard.py"
    if source_script != installed_script:
        shutil.copy2(source_script, installed_script)

    if not (driver_lib / "waveshare_epd/epd2in13_V4.py").is_file():
        if driver_dir.exists():
            raise RuntimeError(f"Waveshare checkout exists but driver is missing: {driver_lib}")
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "https://github.com/waveshareteam/e-Paper.git",
                str(driver_dir),
            ],
            check=True,
        )

    venv_dir = install_dir / ".venv"
    if not (venv_dir / "bin/python").is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
            check=True,
        )
    venv_python = venv_dir / "bin/python"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "Pillow>=9.1", "qrcode>=7.0"],
        check=True,
    )

    if not config_path.exists():
        print("\nEnter the guest Wi-Fi details. The password is not echoed or logged.")
        ssid = input("Guest network SSID: ")
        if not ssid:
            raise ValueError("The guest network SSID cannot be empty")
        security = input("Security [WPA/OPEN] (default WPA): ").strip().upper() or "WPA"
        if security not in ("WPA", "OPEN"):
            raise ValueError("Security must be WPA or OPEN")
        password = "" if security == "OPEN" else getpass.getpass("Guest network password: ")
        if security != "OPEN" and not password:
            raise ValueError("The guest network password cannot be empty")
        config = {
            "guest_wifi": {"ssid": ssid, "password": password, "security": security},
            "refresh_seconds": 60,
            "pihole_service": "pihole-FTL",
            "unbound_service": "unbound",
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chown(config_path, account.pw_uid, account.pw_gid)
        config_path.chmod(0o600)

    service_text = f"""[Unit]
Description=Pi-hole and guest Wi-Fi e-paper dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={service_user}
StateDirectory=guest-epaper
WorkingDirectory=/var/lib/guest-epaper
Environment=WAVESHARE_EPAPER_LIB={driver_lib}
Environment=GPIOZERO_PIN_FACTORY=lgpio
ExecStart={venv_python} {installed_script} --config {config_path}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    service_path.write_text(service_text, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "guest-epaper.service"], check=True)
    if spi_device.exists():
        subprocess.run(["systemctl", "start", "guest-epaper.service"], check=True)
        LOG.info("Installed and started guest-epaper.service")
    elif spi_reboot_needed:
        LOG.warning("SPI was enabled. Reboot the Pi to start the dashboard automatically.")
    else:
        LOG.warning("SPI is not active. Enable SPI in raspi-config and reboot the Pi.")
    LOG.info("Check status with: sudo systemctl status guest-epaper.service")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--once", action="store_true", help="Refresh once, then exit")
    parser.add_argument("--install", action="store_true", help="Install dependencies and start on boot (Raspberry Pi OS only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        if args.install:
            install_dashboard()
            return 0
        display_loop(load_config(args.config), once=args.once)
    except KeyboardInterrupt:
        LOG.info("Stopped")
    except Exception:
        LOG.exception("Dashboard failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())