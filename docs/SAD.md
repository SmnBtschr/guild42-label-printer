# arc42 Architecture Documentation
## Guild42 Self-Service Name Badge Printer

| Field | Value |
|-------|-------|
| Version | 1.2 |
| Status | Active |
| Author | Guild42.ch |
| Hardware Sponsor | Zooey.ch |
| Template | arc42 Version 8 |

---

## 1. Introduction and Goals

### 1.1 Requirements Overview

The Guild42 Self-Service Name Badge Printer allows event attendees to print personalised name badges without staff assistance. Attendees scan a QR code at the check-in desk, open a web page on their mobile phone, enter their first name, and receive a printed label within seconds.

The system must: 

- Be operable by any attendee without instructions
- Print a legible 62mm label within 5 seconds of submission
- Survive a full event (several hours) without manual intervention
- Support multiple event brands (Guild42.ch, CH-Open.ch, Workshop-Tage.ch)
- Allow the active event to be switched by an operator with a single command
- Be accessible from any mobile network via a public HTTPS URL

### 1.2 Quality Goals

| Priority | Quality Goal | Scenario |
|----------|-------------|----------|
| 1 | Reliability | The system prints every submitted job without operator intervention during an event |
| 2 | Accessibility | Attendees can print from their own mobile network — no shared WiFi required |
| 3 | Operability | An operator can switch the active event brand via SSH in under 30 seconds |
| 4 | Simplicity | An attendee with no prior instructions can print a badge within 60 seconds of scanning the QR code |
| 5 | Portability | The system works at any venue by switching to iPhone hotspot automatically |

### 1.3 Stakeholders

| Role | Name / Group | Expectation |
|------|-------------|-------------|
| Event attendee | Guild42 / CH-Open / Workshop-Tage participants | Self-service badge printing without friction |
| Event operator | Guild42 board (Simon) | Reliable system, simple event switching |
| Hardware sponsor | Zooey.ch | System makes good use of donated hardware |
| Community events | CH-Open.ch, Workshop-Tage.ch | Reusable by other Swiss tech community events |

---

## 2. Architecture Constraints

### 2.1 Technical Constraints

| Constraint | Background |
|-----------|-----------|
| Raspberry Pi OS Bookworm 64-bit | Available hardware; 64-bit required for performance |
| Brother QL-820NWBc via USB | Donated hardware; must use this specific printer model |
| DK-22205 roll (62mm continuous) | Standard roll available for QL-820NWBc |
| Public HTTPS access required | Attendees must access the system from their own mobile network |
| Python ecosystem | Team familiarity; `brother_ql` library only available in Python |

### 2.2 Organisational Constraints

| Constraint | Background |
|-----------|-----------|
| Volunteer-run | No paid operations staff; system must be self-managing |
| Shared across events | Guild42.ch lends the hardware to CH-Open and Workshop-Tage |
| Open source | Code published on GitHub for community reuse |

### 2.3 Conventions

- Code and documentation in English
- arc42 for architecture documentation
- Semantic versioning for releases
- ADRs for significant architecture decisions

---

## 3. System Scope and Context

### 3.1 Business Context

```
┌──────────────────────────────────────────────────────────────────┐
│                         Event Venue                              │
│                                                                  │
│  ┌─────────────┐   scans QR code    ┌──────────────────────────┐ │
│  │  Attendee   │ ─────────────────▶ │   Badge Printer System   │ │
│  │  (any phone)│ ◀───────────────── │   (Raspberry Pi)         │ │
│  └─────────────┘   badge prints     └──────────────────────────┘ │
│                                                                  │
│  ┌─────────────┐   SSH + .env edit  ┌──────────────────────────┐ │
│  │  Operator   │ ─────────────────▶ │   Badge Printer System   │ │
│  │  (laptop)   │                    │   (Raspberry Pi)         │ │
│  └─────────────┘                    └──────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

| Neighbour | Communication | Direction |
|-----------|-------------|-----------|
| Attendee mobile browser | HTTPS via `printer.guild42.ch` (any network) | Bidirectional |
| Operator laptop | SSH (local WiFi or hotspot) | Operator → System |
| Brother QL-820NWBc | USB (usblp kernel driver) | System → Printer |
| Cloudflare network | QUIC/HTTPS tunnel | Bidirectional |

### 3.2 Technical Context

```
Internet (any network)
        │
        │ HTTPS  https://printer.guild42.ch
        ▼
┌───────────────────┐
│  Cloudflare Edge  │
│  (global CDN)     │
└────────┬──────────┘
         │ QUIC tunnel (outbound from Pi)
         ▼
┌─────────────────────────────────────────────────────────┐
│                  Local Network / Hotspot                │
│                                                         │
│   ┌───────────────────────────────────────────────┐     │
│   │  Raspberry Pi 4  (guild42-printer.local)      │     │
│   │                                               │     │
│   │  cloudflared  ◀──▶  Flask :5000               │     │
│   │                          │                   │     │
│   │                    /dev/usb/lp0               │     │
│   └──────────────────────────┬────────────────────┘     │
└──────────────────────────────┼─────────────────────────┘
                               │ USB
                      ┌────────▼──────────┐
                      │ Brother QL-820NWBc │
                      │  DK-22205 62mm     │
                      └───────────────────┘
```

---

## 4. Solution Strategy

| Goal | Strategy |
|------|----------|
| Reliability | Bypass CUPS; use `brother_ql` with `linux_kernel` backend directly to `/dev/usb/lp0` |
| Public accessibility | Cloudflare Tunnel with fixed domain `printer.guild42.ch` — no port forwarding, no static IP |
| Venue portability | Two WiFi profiles with priority: iPhone hotspot (priority 50) > home network (priority 10) |
| Simplicity | Single Python file, no database, no framework beyond Flask |
| Operability | File-based configuration (`.env`); systemd for auto-restart and boot start |
| Zero attendee friction | No login, no app install — QR code opens directly to print form over public HTTPS |
| Multi-event support | Configurable subtitle per deployment via `.env`; session override via admin toggle |

---

## 5. Building Block View

### 5.1 Level 1 — Overall System

```
┌──────────────────────────────────────────────────────────────────┐
│                      Badge Printer System                        │
│                                                                  │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │  Cloudflare │   │   Web UI     │   │     Flask App        │  │
│  │  Tunnel     │──▶│ (index.html) │──▶│     (app.py)         │  │
│  │ (cloudflared│   └──────────────┘   └──────────┬───────────┘  │
│  └─────────────┘                                 │              │
│                                        ┌─────────▼───────────┐  │
│                                        │   Label Renderer    │  │
│                                        │   (Pillow)          │  │
│                                        └─────────┬───────────┘  │
│                                                  │              │
│                                        ┌─────────▼───────────┐  │
│                                        │   Print Backend     │  │
│                                        │   (brother_ql)      │  │
│                                        └─────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

| Building Block | Responsibility |
|---------------|---------------|
| Cloudflare Tunnel | Exposes local Flask app at `https://printer.guild42.ch` without port forwarding |
| Web UI | Mobile-friendly form; live preview; admin event selector |
| Flask App | HTTP routing; configuration reading; orchestration |
| Label Renderer | Generates 696×271px PNG from name and subtitle |
| Print Backend | Converts image to Brother raster format; sends to `/dev/usb/lp0` |

### 5.2 Level 2 — Flask App (`app.py`)

| Component | Description |
|-----------|-------------|
| `GET /` | Renders `index.html` with `default_subtitle` and `subtitles` list |
| `POST /print` | Validates input; calls `create_label_image`; calls `brother_ql` send |
| `get_default_subtitle()` | Reads `DEFAULT_SUBTITLE` from `.env`; falls back to `Guild42.ch` |
| `create_label_image()` | Renders PIL image: dark stripe, bold name, subtitle |

### 5.3 Level 2 — Web UI (`index.html`)

| Component | Description |
|-----------|-------------|
| Name input | Text field; triggers live preview on every keystroke |
| Preview box | Renders a visual approximation of the printed label |
| Print button | POSTs JSON `{name, subtitle}` to `/print`; shows status |
| Admin panel | Hidden behind ⚙ toggle; radio buttons for event selection |

---

## 6. Runtime View

### 6.1 Scenario: Attendee prints a badge (from any network)

```
Attendee        Cloudflare       Flask App        Label Renderer    Printer
   │                │                │                  │               │
   │  scan QR       │                │                  │               │
   │  (any network) │                │                  │               │
   │───────────────▶│                │                  │               │
   │                │ tunnel forward │                  │               │
   │                │───────────────▶│                  │               │
   │                │  200 HTML      │                  │               │
   │                │◀───────────────│                  │               │
   │◀───────────────│                │                  │               │
   │  type name     │                │                  │               │
   │  tap Print     │                │                  │               │
   │───────────────▶│                │                  │               │
   │                │ POST /print    │                  │               │
   │                │───────────────▶│                  │               │
   │                │                │ create_label_image│               │
   │                │                │─────────────────▶│               │
   │                │                │  PIL Image        │               │
   │                │                │◀─────────────────│               │
   │                │                │  brother_ql send  │               │
   │                │                │──────────────────────────────────▶│
   │                │                │                  │   label prints │
   │                │  {ok: true}    │                  │               │
   │◀───────────────│◀───────────────│                  │               │
   │  "✓ printing!" │                │                  │               │
```

### 6.2 Scenario: Operator switches event brand

```
Operator (SSH)                    Raspberry Pi
      │                                │
      │  echo "DEFAULT_SUBTITLE=       │
      │    CH-Open.ch" > .env          │
      │───────────────────────────────▶│
      │  sudo systemctl restart nametag│
      │───────────────────────────────▶│
      │                                │ Flask reloads .env
      │  service active                │
      │◀───────────────────────────────│
```

### 6.3 Boot Sequence

```
Power on
    │
    ├─▶ NetworkManager connects WiFi
    │     ├── "STARLINK 🚀" hotspot available? → connect (priority 50)
    │     └── fallback: "Happy LANding" home network (priority 10)
    │
    ├─▶ systemd: load usblp module (modules-load.d/usblp.conf)
    │
    ├─▶ udev: set permissions on /dev/usb/lp0 (99-brother-ql.rules)
    │
    ├─▶ systemd: start cloudflared.service
    │     └── QUIC tunnel to Cloudflare → printer.guild42.ch active
    │
    └─▶ systemd: start nametag.service
          ├─▶ ExecStartPre: brother-setup.sh
          │       ├── systemctl stop ipp-usb
          │       ├── modprobe -r usblp && modprobe usblp
          │       └── wait for /dev/usb/lp0
          └─▶ ExecStart: python3 app.py
                  └── Flask listening on 0.0.0.0:5000
```

---

## 7. Deployment View

### 7.1 Infrastructure

```
Internet
    │
    │  https://printer.guild42.ch
    ▼
┌──────────────────────┐
│  Cloudflare Edge     │
│  DNS + TLS + CDN     │
└──────────┬───────────┘
           │ QUIC tunnel (outbound)
           ▼
┌──────────────────────────────────────────────────┐
│  Raspberry Pi 4  (guild42-printer.local)          │
│  Raspberry Pi OS Bookworm 64-bit                  │
│                                                   │
│  cloudflared.service  ──▶  :5000                  │
│  nametag.service      ──▶  python3 app.py         │
│                                                   │
│  /home/guild42/nametag/                           │
│  ├── app.py                                       │
│  ├── .env                                         │
│  └── templates/index.html                         │
│                                                   │
│  /usr/local/bin/brother-setup.sh                  │
│  /etc/systemd/system/nametag.service              │
│  /etc/modules-load.d/usblp.conf                   │
│  /etc/udev/rules.d/99-brother-ql.rules            │
│                                                   │
│  WiFi profiles (NetworkManager):                  │
│  ├── STARLINK 🚀     priority 50 (event hotspot)  │
│  └── Happy LANding   priority 10 (home network)   │
│                                                   │
│  Port: 5000 (localhost only, via Cloudflare)      │
│  USB:  /dev/usb/lp0                               │
└────────────────────────┬─────────────────────────┘
                         │ USB
               ┌─────────▼──────────┐
               │ Brother QL-820NWBc  │
               │  DK-22205 62mm roll │
               └────────────────────┘
```

### 7.2 Required System Packages

| Package | Purpose |
|---------|---------|
| `python3`, `python3-pip` | Runtime |
| `python3-pil` | Image rendering |
| `libusb-1.0-0` | USB access |
| `imagemagick` | Image format conversion |
| `libc6:armhf` | 32-bit ARM compatibility for Brother filter binary |
| `qrencode` | QR code generation |
| `cloudflared` | Cloudflare Tunnel client |
| `ql820nwbpdrv-2.1.4-0.armhf.deb` | Official Brother driver |

### 7.3 Python Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | ≥ 2.3 | Web framework |
| `brother_ql` | ≥ 0.9 | Label printer library |
| `Pillow` | ≥ 10.0 | Image rendering |

---

## 8. Cross-Cutting Concepts

### 8.1 Label Rendering

All labels are rendered as in-memory PIL images at 696 × 271 pixels (62mm × ~28mm at 300dpi):

```
┌──────────────────────────────────────────┐ ← 8px stripe (#1a1a2e)
│                                          │
│              First Name                  │ ← DejaVuSans-Bold 80pt, centred
│                                          │
│              Guild42.ch                  │ ← DejaVuSans 40pt, centred (#555)
│                                          │
└──────────────────────────────────────────┘
  696px × 271px  |  300dpi  |  62mm roll
```

### 8.2 Configuration Management

The active event brand is controlled by a single line in `.env`:

```
DEFAULT_SUBTITLE=Guild42.ch
```

Switch event:
```bash
echo "DEFAULT_SUBTITLE=CH-Open.ch" > ~/nametag/.env
sudo systemctl restart nametag
```

Available values: `Guild42.ch`, `CH-Open.ch`, `Workshop-Tage.ch`

### 8.3 Network / Tunnel

The Flask app listens on `localhost:5000` only. Cloudflare Tunnel (`cloudflared`) forwards public HTTPS traffic from `printer.guild42.ch` to `localhost:5000` via an outbound QUIC connection — no inbound firewall rules or port forwarding required.

WiFi failover is handled by NetworkManager priority:

| Network | Priority | Use case |
|---------|----------|---------|
| STARLINK 🚀 (iPhone hotspot) | 50 | Event venues |
| Happy LANding (home network) | 10 | Development / storage |

### 8.4 Error Handling

| Layer | Error | Handling |
|-------|-------|---------|
| Flask `/print` | Missing name | HTTP 400 with JSON error |
| Flask `/print` | Print exception | HTTP 500 with exception message |
| Flask `/print` | `.env` missing | Falls back to `Guild42.ch` |
| systemd | Flask crash | `Restart=always` with 10s backoff |
| brother-setup.sh | `/dev/usb/lp0` absent | Logs warning; Flask starts anyway |

### 8.5 Security

The system makes an explicit trust decision. The Cloudflare Tunnel provides HTTPS with valid TLS certificate. No authentication is implemented because:

- No personal data beyond a first name is transmitted
- Adding login would create friction for attendees
- Cloudflare provides DDoS protection at the edge

If abuse becomes a concern, rate limiting via Flask-Limiter should be added.

### 8.6 Logging

```bash
sudo journalctl -u nametag -f       # Flask app logs
sudo journalctl -u cloudflared -f   # Tunnel logs
```

---

## 9. Architecture Decisions

### ADR-001: `linux_kernel` backend instead of CUPS

**Context:**
CUPS + Brother PPD driver + `ipp-usb` was attempted. Jobs were accepted but never printed. Root causes:
- `rastertobrpt1` is a 32-bit ARM binary requiring `libc6:armhf` on 64-bit OS
- Perl wrapper scripts had path resolution bugs (double `//`)
- `ipp-usb` blocked direct USB access
- Printer IPP implementation returned 0 bytes to some CUPS queries

**Decision:** Use `brother_ql` with `linux_kernel` backend directly to `/dev/usb/lp0`.

**Consequences:** Reliable printing. CUPS and `ipp-usb` disabled. Printer not shareable as network printer.

---

### ADR-002: Cloudflare Tunnel for public access

**Context:**
Attendees arrive with their own mobile network. Requiring everyone to join a shared WiFi creates friction and is not always feasible at venues. The Pi has no static public IP and port forwarding is not available on mobile hotspots.

**Decision:** Use Cloudflare Tunnel (`cloudflared`) with a fixed domain `printer.guild42.ch`. The tunnel connects outbound from the Pi — no inbound firewall rules needed.

**Consequences:**
- Attendees access the system from any network via QR code
- Fixed domain means QR code is printed once and never changes
- HTTPS with valid TLS certificate provided automatically by Cloudflare
- Requires internet connectivity on the Pi (via hotspot or venue WiFi)
- Free tier sufficient for event kiosk usage

---

### ADR-003: Dual WiFi profile with priority-based failover

**Context:**
The Pi is used both at home (development, storage) and at events (venues with no fixed WiFi). A manual WiFi switch before each event would be error-prone.

**Decision:** Configure two WiFi profiles in NetworkManager with priorities. iPhone hotspot gets priority 50; home network gets priority 10. Both set to autoconnect.

**Consequences:**
- Pi automatically connects to hotspot at events, falls back to home network otherwise
- No manual intervention required when moving between environments
- Operator must ensure hotspot SSID/password matches the saved profile

---

### ADR-004: File-based configuration over database

**Context:**
Three event brands need to be selectable. Operator uses SSH only.

**Decision:** Plain `.env` file with `DEFAULT_SUBTITLE=` key.

**Consequences:** One-command event switching. No schema, no migration, no admin UI.

---

### ADR-005: No authentication

**Context:**
Kiosk at a physical venue. Only a first name is transmitted.

**Decision:** No login, no rate limiting.

**Consequences:** Zero friction for attendees. Not suitable for untrusted public networks without Flask-Limiter.

---

## 10. Quality Requirements

### 10.1 Quality Tree

```
Quality
├── Reliability
│   ├── Prints after reboot (systemd + brother-setup.sh)
│   └── Auto-restarts on crash (Restart=always)
├── Accessibility
│   ├── Works from any mobile network (Cloudflare Tunnel)
│   └── HTTPS with valid certificate
├── Usability
│   ├── No instructions needed (single input)
│   ├── Live preview before printing
│   └── Mobile-optimised layout
├── Operability
│   ├── Event switching < 30 seconds (.env + restart)
│   ├── Automatic WiFi failover (NetworkManager priority)
│   └── Log access via journalctl
└── Maintainability
    ├── Single-file application
    ├── arc42 documentation
    └── Open source on GitHub
```

### 10.2 Quality Scenarios

| ID | Quality | Scenario | Measure |
|----|---------|----------|---------|
| Q1 | Reliability | Pi reboots before event | System prints within 20 seconds of boot |
| Q2 | Reliability | Flask crashes mid-event | systemd restarts within 10 seconds |
| Q3 | Accessibility | Attendee on 5G mobile | Badge printed via `printer.guild42.ch` without joining venue WiFi |
| Q4 | Operability | Switch Guild42 → CH-Open | Done in under 30 seconds via SSH |
| Q5 | Portability | New venue, unknown WiFi | Operator enables iPhone hotspot; Pi connects automatically |

---

## 11. Risks and Technical Debt

| ID | Risk | Probability | Impact | Mitigation |
|----|------|-------------|--------|-----------|
| R1 | `brother_ql` deprecated | Medium | High | Fork or replace with direct raster generation |
| R2 | Sample DK rolls not recognised | High | Low | Use genuine DK-22205 rolls only |
| R3 | Cloudflare free tier limits | Low | Medium | Monitor usage; upgrade if needed |
| R4 | iPhone hotspot SSID changes | Low | High | Update NetworkManager profile before event |
| R5 | `usblp` unavailable after OS upgrade | Low | High | Pin OS version; test after upgrades |
| R6 | Cloudflare outage | Very Low | High | Fallback: local WiFi QR code pointing to `192.168.x.x:5000` |

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| arc42 | Lean architecture documentation template (arc42.org) |
| Cloudflare Tunnel | Outbound tunnel service exposing local services via public HTTPS URL |
| cloudflared | Cloudflare Tunnel client daemon |
| DK-22205 | Brother continuous paper roll, 62mm wide, white |
| Flask | Lightweight Python web framework |
| ipp-usb | Linux daemon exposing USB printers as IPP network devices |
| Pillow (PIL) | Python image processing library |
| brother_ql | Python library for Brother QL label printers |
| usblp | Linux kernel module for USB printer device access |
| lp0 | First USB printer device node at `/dev/usb/lp0` |
| NetworkManager | Linux network configuration daemon |
| QUIC | UDP-based transport protocol used by Cloudflare Tunnel |

---

## Appendix B: References

| Resource | URL |
|----------|-----|
| arc42 template | https://arc42.org |
| brother_ql library | https://github.com/pklaus/brother_ql |
| Brother QL-820NWBc driver | https://support.brother.com |
| Flask documentation | https://flask.palletsprojects.com |
| Cloudflare Tunnel docs | https://developers.cloudflare.com/cloudflare-one/connections/connect-apps |
| Guild42.ch | https://guild42.ch |
| Zooey.ch (hardware sponsor) | https://zooey.ch |
