# Guild42 Self-Service Name Badge Printer

A self-service name badge printing station for community events.  
Attendees scan a QR code, enter their first name, and a label prints in seconds.

Runs on a Raspberry Pi with a Brother QL-820NWBc label printer connected via USB.  
Hardware kindly provided by [Zooey.ch](https://zooey.ch).

---

## Features

- Mobile-friendly web UI — no app to install, just scan a QR code
- Live label preview before printing
- Multi-event support: Guild42.ch, CH-Open.ch, Workshop-Tage.ch
- Default event configurable via a single `.env` file
- Hidden admin panel (⚙) for on-the-fly event switching
- Runs fully offline — no cloud, no external dependencies

---

## Hardware Requirements

| Component | Specification |
|-----------|--------------|
| Single-board computer | Raspberry Pi 3B+ or 4 (64-bit OS) |
| Label printer | Brother QL-820NWBc |
| Label roll | DK-22205 (62mm continuous white paper) |
| Connection | USB (printer connected directly to Pi) |

---

## Software Requirements

- Raspberry Pi OS Lite 64-bit (Bookworm)
- Python 3.11+
- See `requirements.txt` for Python dependencies

---

## Installation

### 1. Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-pil libusb-1.0-0 imagemagick -y

# 32-bit ARM compatibility (required for Brother binary filter)
sudo dpkg --add-architecture armhf
sudo apt update
sudo apt install libc6:armhf -y
```

### 2. Install Python dependencies

```bash
pip3 install flask brother_ql Pillow --break-system-packages
```

### 3. Install Brother printer driver

Download the ARM driver from Brother's support site:  
`ql820nwbpdrv-2.1.4-0.armhf.deb`

```bash
sudo dpkg -i ql820nwbpdrv-2.1.4-0.armhf.deb
sudo apt --fix-broken install
```

Fix path bugs in the Brother driver scripts:

```bash
sudo python3 -c "
content = open('/opt/brother/PTouch/ql820nwb/cupswrapper/brother_lpdwrapper_ql820nwb').read()
content = content.replace('my \$basedir = \`readlink \$0\`;', 'my \$basedir = \"/opt/brother/PTouch/ql820nwb/\";')
content = content.replace('\$basedir =~ s/\$PRINTER', '#\$basedir =~ s/\$PRINTER')
open('/opt/brother/PTouch/ql820nwb/cupswrapper/brother_lpdwrapper_ql820nwb', 'w').write(content)
"

sudo python3 -c "
content = open('/opt/brother/PTouch/ql820nwb/lpd/filter_ql820nwb').read()
content = content.replace('my \$BR_PRT_PATH = Cwd::realpath (\$0);', 'my \$BR_PRT_PATH = \"/opt/brother/PTouch/ql820nwb\";')
content = content.replace('\$BR_PRT_PATH =~ s/\$PRINTER', '#\$BR_PRT_PATH =~ s/\$PRINTER')
open('/opt/brother/PTouch/ql820nwb/lpd/filter_ql820nwb', 'w').write(content)
"
```

### 4. Configure USB kernel module

```bash
# Load usblp on boot
echo 'usblp' | sudo tee /etc/modules-load.d/usblp.conf

# Disable ipp-usb (it conflicts with direct USB access)
sudo systemctl disable ipp-usb 2>/dev/null || true
```

Install udev rule:

```bash
sudo cp scripts/99-brother-ql.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

### 5. Deploy the web app

```bash
mkdir -p ~/nametag
cp app.py ~/nametag/
cp -r templates ~/nametag/
cp .env.example ~/nametag/.env
# Edit .env to set DEFAULT_SUBTITLE if needed
```

### 6. Install the setup script and systemd service

```bash
sudo cp scripts/brother-setup.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/brother-setup.sh

sudo cp scripts/nametag.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nametag
sudo systemctl start nametag
```

### 7. Generate the QR code

Replace the IP with your Pi's actual IP address:

```bash
sudo apt install qrencode -y
qrencode -o ~/nametag-qr.png -s 10 "http://192.168.178.179:5000"
```

Print the QR code and place it at the check-in desk.

---

## Changing the Default Event

Edit the `.env` file and restart the service:

```bash
echo "DEFAULT_SUBTITLE=CH-Open.ch" > ~/nametag/.env
sudo systemctl restart nametag
```

Available values: `Guild42.ch`, `CH-Open.ch`, `Workshop-Tage.ch`

---

## Troubleshooting

**`/dev/usb/lp0` not found after reboot**  
Run the setup script manually:
```bash
sudo /usr/local/bin/brother-setup.sh
```

**Print job accepted but nothing prints**  
Check the service log:
```bash
sudo journalctl -u nametag --no-pager -n 30
```

**Wrong roll type error on printer display**  
- Ensure a genuine DK-22205 roll is inserted (sample rolls may not be recognised)
- Power cycle the printer and restart ipp-usb:
  ```bash
  sudo systemctl restart nametag
  ```

---

## Credits

- Hardware: [Zooey.ch](https://zooey.ch)
- Community: [Guild42.ch](https://guild42.ch)
