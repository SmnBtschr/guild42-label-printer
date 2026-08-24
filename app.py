from flask import Flask, request, render_template, jsonify
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send
from brother_ql.raster import BrotherQLRaster
from PIL import Image, ImageDraw, ImageFont
import os

app = Flask(__name__)

PRINTER_MODEL = 'QL-820NWB'
PRINTER_URI   = '/dev/usb/lp0'
LABEL_SIZE    = '62'

SUBTITLES = [
    'Guild42.ch',
    'CH-Open.ch',
    'Workshop-Tage.ch',
]

RUNTIME_FILE = os.path.join(os.path.dirname(__file__), 'runtime.txt')


def get_default_subtitle():
    try:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        with open(env_path) as f:
            for line in f:
                if line.startswith('DEFAULT_SUBTITLE='):
                    return line.strip().split('=', 1)[1]
    except Exception:
        pass
    return 'Guild42.ch'


def get_active_subtitle():
    try:
        with open(RUNTIME_FILE) as f:
            value = f.read().strip()
            if value in SUBTITLES:
                return value
    except Exception:
        pass
    return get_default_subtitle()


def set_active_subtitle(subtitle):
    with open(RUNTIME_FILE, 'w') as f:
        f.write(subtitle)


def create_label_image(name, subtitle='Guild42.ch'):
    W, H = 696, 271
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)
    try:
        font_name = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 80)
        font_sub = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 40)
    except Exception:
        font_name = ImageFont.load_default()
        font_sub = font_name
    draw.rectangle([0, 0, W, 18], fill='#1a1a2e')
    draw.text((W // 2, 120), name, font=font_name, fill='black', anchor='mm')
    draw.text((W // 2, 210), subtitle, font=font_sub, fill='#555555', anchor='mm')
    return img


@app.route('/')
def index():
    return render_template('index.html',
                           default_subtitle=get_active_subtitle(),
                           subtitles=SUBTITLES)


@app.route('/set-event', methods=['POST'])
def set_event():
    data = request.get_json()
    subtitle = data.get('subtitle', '').strip()
    if subtitle not in SUBTITLES:
        return jsonify({'error': 'Invalid subtitle'}), 400
    set_active_subtitle(subtitle)
    return jsonify({'ok': True, 'active': subtitle})


@app.route('/print', methods=['POST'])
def print_label():
    data = request.get_json()
    name = (data.get('name') or '').strip()[:15]
    subtitle = (data.get('subtitle') or get_active_subtitle()).strip()[:50]
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    try:
        img = create_label_image(name, subtitle)
        qlr = BrotherQLRaster(PRINTER_MODEL)
        qlr.exception_on_warning = False
        convert(qlr=qlr, images=[img], label=LABEL_SIZE, rotate='auto', dpi_600=False)
        send(instructions=qlr.data,
             printer_identifier=PRINTER_URI,
             backend_identifier='linux_kernel')
        return jsonify({'ok': True, 'name': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
