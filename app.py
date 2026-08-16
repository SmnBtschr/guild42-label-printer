from flask import Flask, request, render_template, jsonify
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send
from brother_ql.raster import BrotherQLRaster
from PIL import Image, ImageDraw, ImageFont
import os

app = Flask(__name__)

# Optional REST API (api.py). It is a no-op unless someone creates a token file, so an
# installation that does not want it is unaffected. See the "REST API" section in README.md.
limiter = None
try:
    from api import api_bp, register_limits

    app.register_blueprint(api_bp)

    # The limits api.py defines were never attached to anything: register_limits() had no
    # caller, so Flask-Limiter was installed and inert while README.md already promised
    # "10/minute per token, 30/minute overall" and a 429. Printing costs material, so an
    # unlimited endpoint is not academic.
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address

        # default_limits stays empty on purpose: only the API blueprint is capped. The kiosk
        # route /print must stay unlimited - README.md says so, and a queue at the printer is
        # not the place to discover a rate limit.
        limiter = Limiter(get_remote_address, app=app, default_limits=[],
                          storage_uri="memory://")
        register_limits(limiter)
    except ImportError:  # Flask-Limiter absent - the API works, just uncapped, as before
        pass
except ImportError:  # api.py removed or dependencies missing - the kiosk keeps working
    pass

# Optional printer simulator (simulator.py). Enabled only with PRINTER_SIM=1 — an installation
# next to real hardware is byte-for-byte unaffected, the /sim routes then answer 404.
try:
    from simulator import sim_bp, sim_enabled, record_print

    app.register_blueprint(sim_bp)
except ImportError:  # simulator.py absent - hardware-only installation, as before
    def sim_enabled() -> bool:
        return False

    def record_print(image, name, subtitle, source):  # pragma: no cover - never called
        raise RuntimeError('simulator not available')

PRINTER_MODEL = 'QL-820NWB'
PRINTER_URI   = '/dev/usb/lp0'
LABEL_SIZE    = '62'

SUBTITLES = [
    'Guild42.ch',
    'CH-Open.ch',
    'Workshop-Tage.ch',
]


def get_default_subtitle() -> str:
    """Read the default subtitle from .env file."""
    try:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        with open(env_path) as f:
            for line in f:
                if line.startswith('DEFAULT_SUBTITLE='):
                    return line.strip().split('=', 1)[1]
    except Exception:
        pass
    return 'Guild42.ch'


def create_label_image(name: str, subtitle: str = 'Guild42.ch') -> Image.Image:
    """Render a 62mm label image (696x271px @ 300dpi)."""
    W, H = 696, 271
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_name = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 80)
        font_sub = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 40)
    except Exception:
        font_name = ImageFont.load_default()
        font_sub = font_name

    # Decorative top stripe
    draw.rectangle([0, 0, W, 18], fill='#1a1a2e')

    # Name and subtitle centred
    draw.text((W // 2, 120), name, font=font_name, fill='black', anchor='mm')
    draw.text((W // 2, 210), subtitle, font=font_sub, fill='#555555', anchor='mm')

    return img


@app.route('/')
def index():
    default = get_default_subtitle()
    return render_template('index.html', default_subtitle=default, subtitles=SUBTITLES,
                           sim=sim_enabled())


@app.route('/session-status')
def session_status():
    """Read-only status for the kiosk page: is an API session connected, and how much has it
    printed?

    Deliberately here and not on the API blueprint: the kiosk page has no token, so a status
    behind the API guard could not be displayed at all. It reveals nothing the kiosk does not
    already expose - anyone who can open this page can print on this printer - and it returns a
    boolean plus a counter, never the session token.

    Without api.py (or without a token file) it reports enabled=false and the page simply shows
    no status line.
    """
    try:
        from api import api_enabled, session_status as api_session_status
    except ImportError:
        return jsonify({'enabled': False})
    if not api_enabled():
        return jsonify({'enabled': False})
    return jsonify({'enabled': True, **api_session_status()})


@app.route('/print', methods=['POST'])
def print_label():
    data = request.get_json()
    name = (data.get('name') or '').strip()[:40]
    subtitle = (data.get('subtitle') or get_default_subtitle()).strip()[:50]

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    try:
        img = create_label_image(name, subtitle)
        if sim_enabled():
            # Same pipeline, different sink: the label lands in the simulator instead of the
            # device. The response is identical on purpose - callers must not see a difference.
            record_print(img, name, subtitle, 'kiosk')
            return jsonify({'ok': True, 'name': name})
        qlr = BrotherQLRaster(PRINTER_MODEL)
        qlr.exception_on_warning = False
        convert(qlr=qlr, images=[img], label=LABEL_SIZE, rotate='auto', dpi_600=False)
        send(
            instructions=qlr.data,
            printer_identifier=PRINTER_URI,
            backend_identifier='linux_kernel',
        )
        return jsonify({'ok': True, 'name': name})
    except Exception as e:
        # The exception text can contain device paths and internals; it goes to the log, not to
        # the caller. The UI only needs to know that printing failed.
        app.logger.exception('print failed: %s', e)
        return jsonify({'error': 'Printing failed'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
