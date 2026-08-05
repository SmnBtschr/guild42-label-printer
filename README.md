# Guild42 Self-Service Name Badge Printer

A self-service name badge printing station for community events.  
Attendees scan a QR code, enter their first name, and a label prints in seconds — from any mobile network.

Runs on a Raspberry Pi with a Brother QL-820NWBc label printer connected via USB,  
accessible publicly via Cloudflare Tunnel at `https://printer.guild42.ch`.

Hardware kindly provided by [Zooey.ch](https://zooey.ch).

---

## Features

- Mobile-friendly web UI — no app to install, just scan a QR code
- Accessible from **any network** via `https://printer.guild42.ch` (Cloudflare Tunnel)
- Live label preview before printing
- Multi-event support: Guild42.ch, CH-Open.ch, Workshop-Tage.ch
- Default event configurable via a single `.env` file
- Hidden admin panel (⚙) for on-the-fly event switching
- Automatic WiFi failover: iPhone hotspot (event) → home network (development)
- Fully self-managing: auto-start on boot, auto-restart on crash

---

## Hardware Requirements

| Component | Specification | Owner |
|-----------|--------------|-------|
| Single-board computer | Raspberry Pi 3B+ or 4 (64-bit OS) | Guild42.ch |
| Label printer | Brother QL-820NWBc | Zooey.ch (donated) |
| Label roll | DK-22205 (62mm continuous white paper) | Guild42.ch |
| Connection | USB (printer connected directly to Pi) | — |
| 4G USB Dongle | Brovi E3372-325 (HiLink mode, usb0) | Guild42.ch |
| SIM card | Migros Mobile prepaid | Guild42.ch |

---

## How it works

```
Attendee scans QR code
    │
    ▼
https://printer.guild42.ch
    │
    ▼
Cloudflare Edge (TLS termination)
    │  QUIC tunnel
    ▼
Raspberry Pi — Flask app (:5000)
    │
    ▼
Pillow renders label image
    │
    ▼
brother_ql → /dev/usb/lp0
    │
    ▼
Brother QL-820NWBc prints label
```

---

## Installation

### 1. Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-pil libusb-1.0-0 imagemagick qrencode -y

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
echo 'usblp' | sudo tee /etc/modules-load.d/usblp.conf
sudo systemctl disable ipp-usb 2>/dev/null || true
sudo cp scripts/99-brother-ql.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

### 5. Deploy the web app

```bash
mkdir -p ~/nametag
cp app.py ~/nametag/
cp -r templates ~/nametag/
cp .env.example ~/nametag/.env
```

### 6. Install setup script and systemd service

```bash
sudo cp scripts/brother-setup.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/brother-setup.sh
sudo cp scripts/nametag.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nametag
sudo systemctl start nametag
```

### 7. Install Cloudflare Tunnel

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 \
  -o cloudflared
sudo mv cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared

cloudflared tunnel login
cloudflared tunnel create guild42-badges
```

Configure and install as service:

```bash
mkdir -p ~/.cloudflared
# Create config.yml — see Cloudflare docs for details
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### 8. Configure network failover

The Pi supports three network connections with automatic priority-based failover:

| Network | Priority | Use case |
|---------|----------|---------|
| iPhone hotspot | 50 | Primary at events |
| 4G Dongle (Brovi E3372-325) | 30 | Fallback at events |
| Home WiFi | 10 | Development / storage |

```bash
# Add event hotspot with high priority
sudo nmcli dev wifi connect 'YOUR-HOTSPOT-SSID' password 'YOUR-PASSWORD'
sudo nmcli con modify 'YOUR-HOTSPOT-SSID' connection.autoconnect-priority 50
sudo nmcli con modify 'YOUR-HOTSPOT-SSID' connection.autoconnect yes

# Configure 4G dongle (appears as usb0 in HiLink mode)
# Enter SIM PIN via browser at http://192.168.8.1
sudo nmcli con modify 'Wired connection 1' connection.autoconnect-priority 30
sudo nmcli con modify 'Wired connection 1' connection.autoconnect yes

# Set home network to lower priority
sudo nmcli con modify 'YOUR-HOME-SSID' connection.autoconnect-priority 10
sudo nmcli con modify 'YOUR-HOME-SSID' connection.autoconnect yes
```

**4G Dongle setup:**
The Brovi E3372-325 runs in HiLink mode and appears as a USB ethernet adapter (`usb0`). To enter the SIM PIN or check connection status, open `http://192.168.8.1` in a browser while connected to the Pi's network.

### 9. Generate the QR code

```bash
qrencode -o ~/nametag-qr.png -s 10 "https://printer.guild42.ch"
```

Print this once — the URL never changes.

---

## Changing the Default Event

```bash
echo "DEFAULT_SUBTITLE=CH-Open.ch" > ~/nametag/.env
sudo systemctl restart nametag
```

Available values: `Guild42.ch`, `CH-Open.ch`, `Workshop-Tage.ch`

---

## Troubleshooting

**`/dev/usb/lp0` not found after reboot**
```bash
sudo /usr/local/bin/brother-setup.sh
```

**Print job accepted but nothing prints**
```bash
sudo journalctl -u nametag --no-pager -n 30
```

**Tunnel not reachable**
```bash
sudo journalctl -u cloudflared --no-pager -n 20
```

**Wrong roll type on printer display**  
Use genuine DK-22205 rolls only — sample rolls may not be recognised.

---

## Credits

- Hardware: [Zooey.ch](https://zooey.ch)
- Community: [Guild42.ch](https://guild42.ch)
- Live at: [printer.guild42.ch](https://printer.guild42.ch)
