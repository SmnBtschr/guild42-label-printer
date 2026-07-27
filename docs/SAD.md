# arc42 Architecture Documentation
## Guild42 Self-Service Name Badge Printer

| Field | Value |
|-------|-------|
| Version | 1.1 |
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

### 1.2 Quality Goals

| Priority | Quality Goal | Scenario |
|----------|-------------|----------|
| 1 | Reliability | The system prints every submitted job without operator intervention during an event |
| 2 | Operability | An operator can switch the active event brand via SSH in under 30 seconds |
| 3 | Simplicity | An attendee with no prior instructions can print a badge within 60 seconds of scanning the QR code |
| 4 | Portability | The system can be moved between venues and restarted without reconfiguration |

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
| No internet at runtime | Event venues may have restricted or unreliable internet |
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
│  │  (mobile)   │ ◀───────────────── │   (Raspberry Pi)         │ │
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
| Attendee mobile browser | HTTP on port 5000 (local WiFi) | Bidirectional |
| Operator laptop | SSH (local WiFi or direct cable) | Operator → System |
| Brother QL-820NWBc | USB (usblp kernel driver) | System → Printer |

### 3.2 Technical Context

```
┌─────────────────────────────────────────────────────────┐
│                     Local WiFi Network                  │
│                                                         │
│   ┌──────────┐    HTTP :5000      ┌───────────────────┐ │
│   │ Attendee │ ─────────────────▶ │  Raspberry Pi 4   │ │
│   │  mobile  │ ◀───────────────── │  Flask app        │ │
│   │  browser │                    │  /dev/usb/lp0     │ │
│   └──────────┘                    └────────┬──────────┘ │
│                                            │ USB        │
└────────────────────────────────────────────┼────────────┘
                                             │
                                   ┌─────────▼──────────┐
                                   │ Brother QL-820NWBc  │
                                   │  DK-22205 62mm roll │
                                   └────────────────────┘
```

---

## 4. Solution Strategy

The system is intentionally minimal. Key strategic decisions:

| Goal | Strategy |
|------|----------|
| Reliability | Bypass CUPS entirely; use `brother_ql` with `linux_kernel` backend directly to `/dev/usb/lp0` |
| Simplicity | Single Python file, no database, no framework beyond Flask |
| Operability | File-based configuration (`.env`); systemd for auto-restart and boot start |
| Zero attendee friction | No login, no app install, no account — QR code opens directly to print form |
| Multi-event support | Configurable subtitle per deployment via `.env`; session override via admin toggle |

---

## 5. Building Block View

### 5.1 Level 1 — Overall System

```
┌─────────────────────────────────────────────────────────────┐
│                  Badge Printer System                       │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │   Web UI     │   │  Flask App   │   │  Print Backend │  │
│  │ (index.html) │──▶│  (app.py)    │──▶│ (brother_ql)   │  │
│  └──────────────┘   └──────────────┘   └────────────────┘  │
│                            │                               │
│                   ┌────────▼────────┐                      │
│                   │ Label Renderer  │                      │
│                   │   (Pillow)      │                      │
│                   └─────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

| Building Block | Responsibility |
|---------------|---------------|
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

### 6.1 Scenario: Attendee prints a badge

```
Attendee        Browser          Flask App        Label Renderer    Printer
   │                │                │                  │               │
   │  scan QR       │                │                  │               │
   │──────────────▶ │                │                  │               │
   │                │  GET /         │                  │               │
   │                │───────────────▶│                  │               │
   │                │  200 HTML      │                  │               │
   │                │◀───────────────│                  │               │
   │  type name     │                │                  │               │
   │──────────────▶ │                │                  │               │
   │                │ (live preview update, JS only)     │               │
   │  tap Print     │                │                  │               │
   │──────────────▶ │                │                  │               │
   │                │ POST /print    │                  │               │
   │                │ {name,subtitle}│                  │               │
   │                │───────────────▶│                  │               │
   │                │                │ create_label_image│               │
   │                │                │─────────────────▶│               │
   │                │                │  PIL Image        │               │
   │                │                │◀─────────────────│               │
   │                │                │  brother_ql convert+send          │
   │                │                │──────────────────────────────────▶│
   │                │                │                  │   label prints │
   │                │  {ok: true}    │                  │               │
   │                │◀───────────────│                  │               │
   │  "✓ printing!" │                │                  │               │
   │◀──────────────-│                │                  │               │
```

### 6.2 Scenario: Operator switches event brand

```
Operator (SSH)                    Raspberry Pi
      │                                │
      │  echo "DEFAULT_SUBTITLE=       │
      │    CH-Open.ch" > .env          │
      │───────────────────────────────▶│
      │                                │
      │  sudo systemctl restart nametag│
      │───────────────────────────────▶│
      │                                │ brother-setup.sh runs
      │                                │ Flask reloads .env
      │  service active                │
      │◀───────────────────────────────│
      │                                │
      │  next attendee sees            │
      │  "CH-Open.ch" as default       │
```

### 6.3 Boot Sequence

```
Power on
    │
    ├─▶ systemd: load usblp module (modules-load.d/usblp.conf)
    │
    ├─▶ udev: set permissions on /dev/usb/lp0 (99-brother-ql.rules)
    │
    └─▶ systemd: start nametag.service
          │
          ├─▶ ExecStartPre: brother-setup.sh
          │       ├── systemctl stop ipp-usb
          │       ├── modprobe -r usblp
          │       ├── sleep 2
          │       ├── modprobe usblp
          │       └── sleep 2  (wait for /dev/usb/lp0)
          │
          └─▶ ExecStart: python3 app.py
                  └── Flask listening on 0.0.0.0:5000
```

---

## 7. Deployment View

### 7.1 Infrastructure

```
┌──────────────────────────────────────────────────┐
│  Raspberry Pi 4  (guild42-printer.local)          │
│  Raspberry Pi OS Bookworm 64-bit                  │
│                                                   │
│  ┌────────────────────────────────────────────┐   │
│  │  systemd service: nametag.service          │   │
│  │  User: root                                │   │
│  │  WorkingDir: /home/guild42/nametag/        │   │
│  │                                            │   │
│  │  /home/guild42/nametag/                    │   │
│  │  ├── app.py                                │   │
│  │  ├── .env                                  │   │
│  │  └── templates/index.html                  │   │
│  └────────────────────────────────────────────┘   │
│                                                   │
│  /usr/local/bin/brother-setup.sh                  │
│  /etc/systemd/system/nametag.service              │
│  /etc/modules-load.d/usblp.conf                   │
│  /etc/udev/rules.d/99-brother-ql.rules            │
│  /opt/brother/PTouch/ql820nwb/  (driver files)    │
│                                                   │
│  Ports: 5000 (HTTP, local network only)           │
│  USB:   /dev/usb/lp0                              │
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
| `imagemagick` | Image format conversion (setup only) |
| `libc6:armhf` | 32-bit ARM compatibility for Brother filter binary |
| `qrencode` | QR code generation |
| `ql820nwbpdrv-2.1.4-0.armhf.deb` | Official Brother driver (provides filter binaries) |

### 7.3 Python Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | ≥ 2.3 | Web framework |
| `brother_ql` | ≥ 0.9 | Label printer library |
| `Pillow` | ≥ 10.0 | Image rendering |

---

## 8. Cross-Cutting Concepts

### 8.1 Label Rendering

All labels are rendered as in-memory PIL images at a fixed 696 × 271 pixels (62mm × ~28mm at 300dpi):

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

Font fallback: if DejaVu fonts are unavailable, `ImageFont.load_default()` is used.

### 8.2 Configuration Management

The active event brand is controlled by a single line in `.env`:

```
DEFAULT_SUBTITLE=Guild42.ch
```

The Flask app reads this on every request (no caching), so a restart is only needed to apply a change server-wide. The admin panel in the UI overrides the subtitle for that browser session only.

### 8.3 Error Handling

| Layer | Error | Handling |
|-------|-------|---------|
| Flask `/print` | Missing name field | Returns HTTP 400 with JSON error |
| Flask `/print` | Print exception | Returns HTTP 500 with exception message |
| Flask `/print` | `.env` missing | Falls back to `Guild42.ch` silently |
| systemd | Flask crash | `Restart=always` with 10s backoff |
| brother-setup.sh | `/dev/usb/lp0` absent | Logs warning; Flask starts anyway |

### 8.4 Security

The system makes an explicit trust decision: the local event WiFi network is trusted. There is no authentication, no HTTPS, and no rate limiting. This is appropriate because:

- No personal data beyond a first name is transmitted
- The physical venue controls network access
- Adding authentication would create friction for attendees

If deployed in a less trusted environment (e.g. open public WiFi), rate limiting via Flask-Limiter should be added.

### 8.5 Logging

Flask logs all HTTP requests to stdout, captured by systemd journal:

```bash
sudo journalctl -u nametag -f
```

No application-level log files are written. Brother driver debug logs are written to `/tmp/br_cupswrapper_ink.log` when `$DEBUG > 0` (disabled by default).

---

## 9. Architecture Decisions

### ADR-001: `linux_kernel` backend instead of CUPS

**Context:**
CUPS with the official Brother PPD driver and `ipp-usb` was attempted first. Jobs were accepted by CUPS but never reached the printer. Root causes identified:

- The Brother filter binary `rastertobrpt1` is a 32-bit ARM ELF binary; on 64-bit Raspberry Pi OS it requires `libc6:armhf`
- The Brother Perl wrapper scripts contained path resolution bugs (double `//` in constructed paths) due to incorrect `$basedir` calculation
- `ipp-usb` claimed the USB device as an IPP network printer, blocking direct USB access
- The printer's IPP implementation returned `0 bytes` in response to some CUPS backend queries, causing silent job completion without printing

**Decision:**
Use `brother_ql` with the `linux_kernel` backend, writing directly to `/dev/usb/lp0` via the `usblp` kernel module. CUPS and `ipp-usb` are disabled.

**Consequences:**
- Printing is reliable and fast
- The setup is significantly simpler
- The printer cannot be shared as a network printer to other clients
- The Brother CUPS driver is still installed (provides `rastertobrpt1` and related binaries used by `brother_ql` internally)

---

### ADR-002: File-based configuration over database or environment variables

**Context:**
The system serves three different event brands. The active brand must be switchable by an operator who is not a developer, using only SSH access.

**Decision:**
A plain text `.env` file with a single `DEFAULT_SUBTITLE=` key. The Flask app reads it on each request.

**Consequences:**
- Switching events requires one `echo` command and a service restart
- No migration scripts, no schema, no admin UI needed
- The `.env` file is excluded from git (`.gitignore`) to avoid accidental credential exposure in forks
- `.env.example` is committed as a template

---

### ADR-003: No authentication

**Context:**
The kiosk is deployed in a physical venue on a local WiFi network. Attendees are expected to print their own badge. The only data transmitted is a first name and an event label.

**Decision:**
No login, no token, no rate limiting.

**Consequences:**
- Zero friction for attendees
- Anyone on the local network can trigger print jobs
- Acceptable risk for the community event threat model
- Not suitable for deployment on untrusted public networks without adding Flask-Limiter

---

### ADR-004: Single-file Flask application

**Context:**
The system has one endpoint that does one thing. Introducing a package structure, blueprints, or an ORM would add complexity without benefit.

**Decision:**
Everything in a single `app.py`.

**Consequences:**
- Easy to read and modify by any Python developer
- Simple to deploy (copy one file)
- Will require refactoring if functionality grows significantly (multiple printers, job queue, admin API)

---

## 10. Quality Requirements

### 10.1 Quality Tree

```
Quality
├── Reliability
│   ├── Printer available after reboot (systemd + brother-setup.sh)
│   └── Service auto-restarts on crash (Restart=always)
├── Usability
│   ├── No instructions needed (single input field)
│   ├── Live preview before printing
│   └── Mobile-optimised layout
├── Operability
│   ├── Event switching in < 30 seconds (one .env edit + restart)
│   └── Log access via journalctl
└── Maintainability
    ├── Single-file application
    ├── arc42 documentation
    └── Open source on GitHub
```

### 10.2 Quality Scenarios

| ID | Quality | Scenario | Measure |
|----|---------|----------|---------|
| Q1 | Reliability | Pi reboots before an event | System prints within 15 seconds of boot completing |
| Q2 | Reliability | Flask crashes during event | systemd restarts it within 10 seconds |
| Q3 | Usability | Attendee has never used the system | Badge printed within 60 seconds of scanning QR code |
| Q4 | Operability | Operator switches from Guild42 to CH-Open | Done in under 30 seconds via SSH |
| Q5 | Portability | System moved to new venue with different IP | QR code regenerated; no other changes needed |

---

## 11. Risks and Technical Debt

| ID | Risk | Probability | Impact | Mitigation |
|----|------|-------------|--------|-----------|
| R1 | `brother_ql` library deprecated | Medium | High | Fork or replace with direct raster generation if needed |
| R2 | Sample DK rolls not recognised by printer chip | High | Low | Document: use genuine DK-22205 rolls only |
| R3 | Flask development server overloaded | Low | Medium | Serial print queue is sufficient for event scale; upgrade to gunicorn if needed |
| R4 | IP address changes between events | High | Low | Regenerate QR code; consider mDNS (`guild42-printer.local`) |
| R5 | usblp module unavailable after OS upgrade | Low | High | Pin OS version; test after upgrades |

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| arc42 | Lean architecture documentation template (arc42.org) |
| DK-22205 | Brother continuous paper roll, 62mm wide, white |
| Flask | Lightweight Python web framework |
| ipp-usb | Linux daemon exposing USB printers as IPP network devices |
| Pillow (PIL) | Python image processing library |
| brother_ql | Python library for Brother QL label printers |
| usblp | Linux kernel module for USB printer device access |
| lp0 | First USB printer device node at `/dev/usb/lp0` |
| systemd | Linux service manager used for auto-start and supervision |
| udev | Linux device manager used for USB permissions |
| QR code | 2D barcode encoding the web app URL for attendee access |

---

## Appendix B: References

| Resource | URL |
|----------|-----|
| arc42 template | https://arc42.org |
| brother_ql library | https://github.com/pklaus/brother_ql |
| Brother QL-820NWBc driver | https://support.brother.com |
| Flask documentation | https://flask.palletsprojects.com |
| Guild42.ch | https://guild42.ch |
| Zooey.ch (hardware sponsor) | https://zooey.ch |
