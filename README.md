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

| Component | Specification |
|-----------|--------------|
| Single-board computer | Raspberry Pi 3B+ or 4 (64-bit OS) |
| Label printer | Brother QL-820NWBc |
| Label roll | DK-22205 (62mm continuous white paper) |
| Connection | USB (printer connected directly to Pi) |

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

### 8. Configure WiFi failover

```bash
# Add event hotspot with high priority
sudo nmcli dev wifi connect 'YOUR-HOTSPOT-SSID' password 'YOUR-PASSWORD'
sudo nmcli con modify 'YOUR-HOTSPOT-SSID' connection.autoconnect-priority 50
sudo nmcli con modify 'YOUR-HOTSPOT-SSID' connection.autoconnect yes

# Set home network to lower priority
sudo nmcli con modify 'YOUR-HOME-SSID' connection.autoconnect-priority 10
sudo nmcli con modify 'YOUR-HOME-SSID' connection.autoconnect yes
```

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

## REST API (optional)

Besides the kiosk page, the printer can be triggered from another system — for example an event
check-in that prints a badge as soon as someone signs in.

**The API is off until you switch it on.** It only exists once an `api_tokens.txt` file is
present; without that file every `/api/v1/...` route answers `404` and the application behaves
exactly as it did before. Nothing about the kiosk page changes either way.

### Switching it on

```bash
cd /opt/nametag                      # wherever the app lives
cp api_tokens.txt.example api_tokens.txt

# Create a token. It is shown ONCE - store it where the calling system can read it.
python3 -c "import secrets,hashlib; t=secrets.token_urlsafe(32); \
  print('token:', t); print('hash :', hashlib.sha256(t.encode()).hexdigest())"
```

Put the **hash** into `api_tokens.txt`, one token per line, with a label of your choosing:

```
checkin-desk:3f8a...c21b
```

The label never leaves the machine except in the log, where it lets you tell callers apart. The
token itself is never written to disk and never logged.

No restart is needed — the file is read per request.

### Revoking a token

Delete its line. It stops working with the next request; no restart, no downtime, and other
tokens are unaffected. This is why each caller should get its own line.

### Endpoints

Both require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/print` | Print a badge. Body: `{"name": "Anna Muster", "subtitle": "Guild42.ch"}` — `subtitle` is optional and falls back to `DEFAULT_SUBTITLE` from `.env`. |
| `GET` | `/api/v1/health` | Check whether the printer device can be opened, before promising a user anything. |
| `POST` | `/api/v1/session` | **Onboarding** — claim the printer for one caller. Returns the session token once. |
| `DELETE` | `/api/v1/session` | **Offboarding** — release it again. Requires the session token. |
| `GET` | `/api/v1/session` | Is a session running, and how many badges has it printed? |

```bash
curl -X POST https://printer.example.ch/api/v1/print \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Anna Muster"}'
```

### Exclusive session (onboarding / offboarding)

One caller can claim the printer, so that a running check-in desk is not disturbed by a second
system printing into the same queue.

```bash
# Onboarding - the token comes back exactly once, keep it
curl -X POST https://printer.example.ch/api/v1/session -H "Authorization: Bearer $TOKEN"
# {"ok": true, "token": "5Jd...Qy"}

# From now on every print carries it
curl -X POST https://printer.example.ch/api/v1/print \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-Token: 5Jd...Qy" \
  -H "Content-Type: application/json" -d '{"name": "Anna Muster"}'

# Offboarding - only the holder of the session token can do this
curl -X DELETE https://printer.example.ch/api/v1/session \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-Token: 5Jd...Qy"
```

You may supply your own token with `{"token": "..."}` (at least 16 characters) instead of letting
the server generate one.

Four properties, and the reasoning behind each:

- **A second onboarding is refused with `409`, never silently overwritten.** Overwriting would be
  the hijack this mechanism exists to prevent.
- **Onboarding sits behind the normal token guard.** This device is reachable from any network by
  design; an unauthenticated onboarding would mean whoever reaches the address first locks
  everyone else out — with a trip to the Pi as the only remedy. The static token from
  `api_tokens.txt` is the entry ticket, so there is no second authentication model.
- **The session lives in memory and dies with the process.** There is no admin override and no
  expiry: restarting the service is the way out of a forgotten session. That is also why it is
  not written to a file — a file would survive exactly the restart that is supposed to clear it.
- **Without a session nothing changes.** Callers with a static token print as before; exclusivity
  begins with the first onboarding.

The kiosk page shows a small line while a session is connected, including how many badges it has
printed. It is display only — there are no controls, and the session token is never shown.

#### Signed session tokens (optional) — surviving a restart of the caller

The four properties above have one hard edge: the holder is recognised by the **hash of a value**,
so it has to remember that value. A caller that is redeployed daily cannot. Guild42's check-in
system loses its token on every deploy — from then on every print and even the offboarding answers
`403`, and the only documented remedy is a restart of this Pi.

If the caller brings a **signed token** instead, the session remembers *who* opened it rather than
only *what* was handed over, and the same holder may take it over with a fresh token:

```ini
# .env — all three are optional; without SESSION_JWKS_URL nothing changes
SESSION_JWKS_URL=https://app.guild42.ch/.well-known/jwks.json
SESSION_JWT_ISSUER=https://app.guild42.ch
SESSION_JWT_AUDIENCE=guild42-label-printer
```

The token must be a `RS256` JWT signed by a key published at `SESSION_JWKS_URL`, carrying `sub`,
`exp` and the expected `aud`/`iss`. Identity is `iss` + `sub`: the same identity resumes the
running session (`200` with `"resumed": true`, print counter preserved), everything else keeps
getting `409`.

Deliberate limits:

- **The key set address is configured here, never taken from the token.** Reading `iss` and
  fetching keys from wherever it points would let the caller decide who is trusted.
- **A session opened with a static token has no identity** and cannot be taken over by any
  signature — only the value counts, exactly as before.
- **`exp` is required.** A token without expiry would be an eternal one, and this one travels as an
  HTTP header.
- **Only `RS256`.** A header claiming another algorithm is refused even when the RSA signature is
  valid (`none` and HMAC are the alg-confusion attack).
- **An unreachable issuer costs the takeover, not the operation.** The running session keeps
  printing with the token it already knows.

No new dependency: verification is a modular exponentiation plus a byte comparison from the
standard library.

### What a success response does and does not mean

```json
{"ok": true, "accepted": true, "name": "Anna Muster"}
```

**`accepted`, not `printed`.** The response means the raster was handed to the printer and the
device took it. Whether a label actually came out — paper left on the roll, no jam, label not
peeled off — cannot be observed from here, and claiming otherwise would make callers report
something to their users that may not be true.

| Status | Meaning |
|---|---|
| `400` | `name` missing or empty |
| `401` | token missing, malformed or unknown (no further detail, on purpose) |
| `403` | a session is running and the `X-Session-Token` is missing or wrong |
| `404` | the API is not enabled on this installation (no token file) |
| `409` | onboarding refused — a session is already running (and the caller did not prove the same signed identity) |
| `429` | rate limit reached |
| `503` | printer device missing or not openable |
| `500` | printing failed for another reason — details are in the log, not in the response |

### Rate limits

Printing costs material, so `/api/v1/print` is limited. Defaults are `10/minute` per token and
`30/minute` overall; both can be changed in `.env`:

```
API_RATE_LIMIT=10/minute
API_RATE_LIMIT_GLOBAL=30/minute
API_TOKENS_FILE=/etc/nametag/api_tokens.txt   # optional, defaults to the app directory
```

The limits need `Flask-Limiter` (added to `requirements.txt`). The kiosk route `/print` is
deliberately left unlimited so the existing behaviour is unchanged — add a limit there too if your
installation is reachable from outside.

### Tests

```bash
pip install pytest
python3 -m pytest tests/
```

They mock the printer backend and never touch a real device, so they run anywhere.

---

## Credits

- Hardware: [Zooey.ch](https://zooey.ch)
- Community: [Guild42.ch](https://guild42.ch)
- Live at: [printer.guild42.ch](https://printer.guild42.ch)
