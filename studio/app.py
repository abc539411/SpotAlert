"""
SpotAlert Studio - Flask backend
"""
from flask import Flask, jsonify, send_file, request, render_template
from pathlib import Path
from datetime import datetime
import io
import os
import logging

from config import INBOX_PATH, NAS_BASE_PATH, SUPPORTED_RAW_FORMATS, THUMB_CACHE_DIR, THUMB_MAX_PX, LR_CATALOG_PATH
from utils.fr24_lookup import FR24Lookup
from utils.file_handler import FileOrganizer
from utils.thumbnail import get_thumbnail, extract_embedded_jpeg
from utils.lr_catalog import update_catalog, is_lightroom_running, get_keywords

app = Flask(__name__)


_AIRPORTS      = None
_AIRPORTS_ICAO = None

# Manual overrides for codes not in IATA or ICAO databases
_AIRPORT_OVERRIDES = {
    'NZWG': ('NZ', 'Wigram Aerodrome'),
    '12 Apostles Heliport': ('AU', '12 Apostles Heliport'),
}

def _load_airports():
    global _AIRPORTS
    if _AIRPORTS is None:
        import airportsdata
        _AIRPORTS = airportsdata.load('IATA')
    return _AIRPORTS

def _load_airports_icao():
    global _AIRPORTS_ICAO
    if _AIRPORTS_ICAO is None:
        import airportsdata
        _AIRPORTS_ICAO = airportsdata.load('ICAO')
    return _AIRPORTS_ICAO

def _airport_coords(iata: str):
    """Return (lat, lon) for an IATA or ICAO code, or None."""
    if not iata:
        return None
    try:
        a = _load_airports().get(iata.upper()) or _load_airports_icao().get(iata.upper())
        return (a['lat'], a['lon']) if a else None
    except Exception:
        return None

_LOG_FILE = Path(__file__).parent / 'server.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('ss')

# Resolved output base — used to block deletions of already-organised files
_OUTPUT_BASE = Path(NAS_BASE_PATH).resolve()


def _in_output(path: Path, output_override: str = '') -> bool:
    """Return True if path sits inside the output folder (or a session subfolder of it)."""
    base = Path(output_override).resolve() if output_override else _OUTPUT_BASE
    try:
        path.resolve().relative_to(base)
        return True
    except ValueError:
        return False


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config')
def get_config():
    return jsonify({
        'inbox': str(INBOX_PATH),
        'output': NAS_BASE_PATH,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'airport': 'SYD',
        'catalog': LR_CATALOG_PATH,
    })


def _exif_date(path: Path) -> str | None:
    """Extract DateTimeOriginal by parsing the TIFF/EXIF IFD directly.

    Works for ARW, CR2, NEF, and standard JPEG files. Reads only the first
    64 KB — EXIF IFD is always near the start of RAW/JPEG files.
    Returns 'YYYY-MM-DD' or None.
    """
    import struct
    try:
        with open(path, 'rb') as f:
            chunk = f.read(65536)

        # JPEG: look for APP1 EXIF marker
        if chunk[:2] == b'\xff\xd8':
            pos = 2
            while pos + 4 < len(chunk):
                marker = chunk[pos:pos+2]
                seg_len = struct.unpack_from('>H', chunk, pos + 2)[0]
                if marker == b'\xff\xe1' and chunk[pos+4:pos+10] == b'Exif\x00\x00':
                    tiff_start = pos + 10
                    break
                pos += 2 + seg_len
            else:
                return None
        else:
            tiff_start = 0

        # TIFF header
        bo = chunk[tiff_start:tiff_start+2]
        if bo == b'II':
            endian = '<'
        elif bo == b'MM':
            endian = '>'
        else:
            return None

        ifd0_off = struct.unpack_from(endian + 'I', chunk, tiff_start + 4)[0] + tiff_start

        def read_ifd(offset):
            if offset + 2 > len(chunk):
                return {}
            count = struct.unpack_from(endian + 'H', chunk, offset)[0]
            entries = {}
            for i in range(count):
                o = offset + 2 + i * 12
                if o + 12 > len(chunk):
                    break
                tag, _, _, val = struct.unpack_from(endian + 'HHII', chunk, o)
                entries[tag] = val + tiff_start
            return entries

        ifd0 = read_ifd(ifd0_off)
        exif_off = ifd0.get(0x8769)  # EXIF SubIFD pointer
        if exif_off is None:
            return None

        exif = read_ifd(exif_off)
        dto_off = exif.get(0x9003) or exif.get(0x9004) or ifd0.get(0x0132)
        if dto_off is None:
            return None

        raw = chunk[dto_off:dto_off + 20].split(b'\x00')[0].decode('ascii', errors='ignore')
        if len(raw) >= 10:
            return raw[:10].replace(':', '-')  # '2026:05:30' → '2026-05-30'
    except Exception:
        pass
    return None


@app.route('/api/scan')
def scan():
    inbox = Path(request.args.get('inbox', str(INBOX_PATH)))
    if not inbox.exists():
        return jsonify({'error': f'Inbox not found: {inbox}'}), 404

    found = set()
    for ext in SUPPORTED_RAW_FORMATS:
        found.update(inbox.rglob(f'*{ext}'))
        found.update(inbox.rglob(f'*{ext.upper()}'))

    sorted_files = sorted(found)
    files = [
        {'name': f.name, 'path': str(f), 'size_mb': round(f.stat().st_size / (1024 * 1024), 1)}
        for f in sorted_files
    ]

    # Best-effort: extract shoot date from first file's EXIF
    shoot_date = None
    for f in sorted_files[:5]:
        shoot_date = _exif_date(f)
        if shoot_date:
            break

    return jsonify({'files': files, 'shoot_date': shoot_date})


@app.route('/api/preview')
def preview():
    """Full-size embedded JPEG — no resize, cached."""
    path = request.args.get('path')
    if not path:
        return 'Missing path', 400
    from utils.thumbnail import get_raw_preview
    jpeg = get_raw_preview(Path(path), THUMB_CACHE_DIR)
    if not jpeg:
        return 'No preview', 404
    response = send_file(io.BytesIO(jpeg), mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/api/thumbnail')
def thumbnail():
    path = request.args.get('path')
    if not path:
        return 'Missing path', 400
    size = min(int(request.args.get('size', THUMB_MAX_PX)), 2000)
    jpeg = get_thumbnail(Path(path), THUMB_CACHE_DIR, size)
    if not jpeg:
        return 'No preview', 404
    response = send_file(io.BytesIO(jpeg), mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response



@app.route('/api/lookup', methods=['POST'])
def lookup():
    data = request.json
    registration = data.get('registration', '').upper().strip()
    if not registration:
        return jsonify({'error': 'No registration provided'}), 400
    success, result = FR24Lookup().lookup(registration)
    return jsonify({'success': success, 'data': result})


@app.route('/api/keywords')
def list_keywords():
    catalog = request.args.get('catalog', LR_CATALOG_PATH)
    if not catalog:
        return jsonify({'keywords': []})
    return jsonify({'keywords': get_keywords(Path(catalog))})


@app.route('/api/sessions')
def list_sessions():
    """List immediate subdirectories of the output base (year-region folders)."""
    output = request.args.get('output', NAS_BASE_PATH)
    base = Path(output)
    if not base.exists():
        return jsonify({'sessions': []})
    try:
        sessions = sorted(
            [d.name for d in base.iterdir() if d.is_dir()],
            reverse=True
        )
    except Exception as e:
        return jsonify({'sessions': [], 'error': str(e)})
    return jsonify({'sessions': sessions})


@app.route('/api/backfill-exif', methods=['POST'])
def backfill_exif():
    """Backfill camera model + timezone for catalog entries that are missing EXIF data."""
    catalog = request.json.get('catalog', LR_CATALOG_PATH) if request.json else LR_CATALOG_PATH
    cat = Path(catalog)
    if not cat.exists():
        return jsonify({'error': 'Catalog not found'}), 404
    if is_lightroom_running():
        return jsonify({'error': 'Lightroom is running — close it first'}), 409

    from utils.lr_catalog import _read_exif_for_catalog, _write_exif_metadata
    try:
        import sqlite3 as _sq
        con = _sq.connect(str(cat))
        rows = con.execute("""
            SELECT img.id_local,
                   rf.absolutePath || f.pathFromRoot || lf.idx_filename
            FROM Adobe_images img
            JOIN AgLibraryFile lf ON lf.id_local = img.rootFile
            JOIN AgLibraryFolder f ON f.id_local = lf.folder
            JOIN AgLibraryRootFolder rf ON rf.id_local = f.rootFolder
            LEFT JOIN AgHarvestedExifMetadata exif ON exif.image = img.id_local
            LEFT JOIN AgLibraryImageChangeCounter tz ON tz.image = img.id_local
            WHERE exif.image IS NULL
               OR exif.cameraModelRef IS NULL
               OR tz.image IS NULL
               OR tz.localTimeOffsetSecs IS NULL
        """).fetchall()

        updated, skipped = 0, 0
        for image_id, fpath_str in rows:
            fpath = Path(fpath_str)
            if not fpath.exists():
                skipped += 1
                continue
            exif = _read_exif_for_catalog(fpath)
            if exif['camera_model'] or exif['tz_offset_secs'] is not None:
                _write_exif_metadata(con, image_id, exif['camera_model'], exif['tz_offset_secs'])
                if exif['capture_time']:
                    con.execute(
                        "UPDATE Adobe_images SET captureTime=? WHERE id_local=?",
                        (exif['capture_time'], image_id)
                    )
                updated += 1
            else:
                skipped += 1

        con.commit()
        con.close()
        return jsonify({'updated': updated, 'skipped': skipped, 'total': len(rows)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/organize', methods=['POST'])
def organize():
    data = request.json
    date_str = data.get('date', datetime.now().strftime('%Y%m%d')).replace('-', '')
    airport = data.get('airport', 'SYD').upper()
    registration = data.get('registration', '').upper()
    airline = data.get('airline', 'Unknown')
    aircraft_manufacturer = data.get('aircraft_manufacturer', '')
    aircraft_type = data.get('aircraft_type', '')
    aircraft_url = data.get('aircraft_url', '')
    output = data.get('output', NAS_BASE_PATH)
    session = data.get('session', '').strip()
    paths = data.get('paths', [])
    keywords = data.get('keywords', [])

    # If a session is selected, organise into {output}/{session}/...
    organizer_base = Path(output) / session if session else Path(output)
    lr_root = organizer_base if session else None

    try:
        organizer = FileOrganizer(organizer_base)
    except Exception as e:
        print(f"[organize] cannot use output path '{organizer_base}': {e}")
        return jsonify({'success': False, 'error': f'Output path error: {e}'}), 500

    ok, folder, msg = organizer.create_folder_structure(date_str, airport, airline, registration)
    if not ok:
        print(f"[organize] folder creation failed: {msg}")
        return jsonify({'success': False, 'error': msg}), 500

    source_paths = [Path(p) for p in paths]
    moved, failed, errors = organizer.move_files(source_paths, folder)
    log.info(f"[organize] moved={moved} failed={failed} errors={errors} dest={folder}")
    if failed and not moved:
        return jsonify({'success': False, 'error': errors[0] if errors else 'Move failed'}), 500

    # Update Lightroom catalog if configured
    catalog_updated = 0
    catalog_errors = []
    catalog_path_str = data.get('catalog', LR_CATALOG_PATH)
    if catalog_path_str:
        cat = Path(catalog_path_str)
        lr_open = is_lightroom_running(cat)
        log.info(f"[catalog] path={cat} exists={cat.exists()} lr_running={lr_open}")
        if lr_open:
            catalog_errors.append('Lightroom is open — close it before syncing catalog')
        else:
            dest_files = [folder / Path(p).name for p in paths]
            log.info(f"[catalog] keywords={keywords} dest_files={[f.name for f in dest_files]}")
            meta = {
                'registration': registration,
                'airline': airline,
                'aircraft_manufacturer': aircraft_manufacturer,
                'aircraft_type': aircraft_type,
                'aircraft_url': aircraft_url,
                'airport_iata': airport,
            }
            catalog_updated, catalog_errors = update_catalog(
                Path(catalog_path_str), dest_files, folder, meta,
                lr_root=lr_root, keywords=keywords,
                airport_coords=_airport_coords(airport),
            )
            log.info(f"[catalog] updated={catalog_updated} errors={catalog_errors}")

    return jsonify({
        'success': True, 'moved': moved, 'failed': failed,
        'errors': errors, 'destination': str(folder),
        'catalog_updated': catalog_updated, 'catalog_errors': catalog_errors,
    })


@app.route('/api/move-orphans', methods=['POST'])
def move_orphans():
    data = request.json
    paths = data.get('paths', [])
    date_str = data.get('date', datetime.now().strftime('%Y%m%d')).replace('-', '')
    airport = data.get('airport', 'SYD').upper()
    output = data.get('output', NAS_BASE_PATH)
    session = data.get('session', '').strip()
    base = Path(output) / session if session else Path(output)
    folder = base / f"{date_str} - {airport}" / "Other"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    organizer = FileOrganizer(Path(output))
    moved, failed, errors = organizer.move_files([Path(p) for p in paths], folder)
    return jsonify({'success': True, 'moved': moved, 'failed': failed, 'destination': str(folder)})


@app.route('/api/delete-batch', methods=['POST'])
def delete_batch():
    paths = request.json.get('paths', [])
    output_override = request.json.get('output', '')
    deleted, errors = 0, []
    for p in paths:
        target = Path(p)
        if _in_output(target, output_override):
            errors.append(f'{target.name}: cannot delete — file is already in the output folder')
            continue
        try:
            target.unlink(missing_ok=True)
            deleted += 1
        except Exception as e:
            errors.append(str(e))
    return jsonify({'success': True, 'deleted': deleted, 'errors': errors})


@app.route('/api/cleanup-inbox', methods=['POST'])
def cleanup_inbox():
    inbox = Path(request.json.get('inbox', str(INBOX_PATH)))
    removed = []
    # Walk bottom-up so children are removed before parents
    for dirpath, dirnames, filenames in os.walk(inbox, topdown=False):
        d = Path(dirpath)
        if d == inbox:
            continue  # never remove the inbox root itself
        if not any(d.iterdir()):
            try:
                d.rmdir()
                removed.append(str(d))
            except Exception as e:
                print(f"[cleanup] could not remove {d}: {e}")
    return jsonify({'success': True, 'removed': removed})


@app.route('/api/delete', methods=['POST'])
def delete_photo():
    output_override = request.json.get('output', '')
    path = Path(request.json.get('path', ''))
    if _in_output(path, output_override):
        return jsonify({'success': False, 'error': 'Cannot delete — file is already in the output folder'}), 403
    try:
        if path.exists():
            path.unlink()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    INBOX_PATH.mkdir(parents=True, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
