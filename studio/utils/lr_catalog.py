"""
Lightroom Classic catalog updater.
Writes plugin metadata (ch.aviationphoto.aircraftmetadata) and registers
photos directly in the catalog after they are moved to the output folder.

Requires LR to be closed before writing — SQLite WAL lock conflicts otherwise.
"""
import os
import platform
import re as _re
import sqlite3
import struct
import uuid
import zlib
from datetime import datetime
from pathlib import Path

PLUGIN_ID = 'ch.aviationphoto.aircraftmetadata'
_LR_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01 UTC
_RAW_EXTS = {'.cr2', '.nef', '.arw', '.dng', '.raf', '.rw2', '.orf', '.srw', '.raw'}

# Full spec definitions for the AircraftMetadata plugin.
# Inserted automatically if the catalog has never been used with the plugin.
_PLUGIN_SPECS = [
    ('registration',       'Registration',    'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
    ('aircraft_manufacturer', 'Manufacturer', 'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
    ('aircraft_type',      'Type',            'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
    ('aircraft_notes',     'Notes',           'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
    ('airline',            'Airline',         'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
    ('aircraft_url',       'Aircraft URL',    'AgPhotoProperty',
     't = {\n\tbrowsable = false,\n\tdataType = "url",\n\tentitySpecName = "AgPhotoProperty",\n\treadOnly = true,\n\tsearchable = false,\n}\n'),
    ('airport_iata',       'Airport (IATA)',  'AgSearchablePhotoProperty',
     't = {\n\tbrowsable = true,\n\tdataType = "string",\n\tentitySpecName = "AgSearchablePhotoProperty",\n\tsearchable = true,\n}\n'),
]


def is_lightroom_running(catalog_path: Path = None) -> bool:
    """Return True if Lightroom Classic is currently running."""
    try:
        import subprocess
        if platform.system() == 'Windows':
            out = subprocess.check_output(
                ['tasklist', '/FI', 'IMAGENAME eq lightroom.exe', '/NH'],
                encoding='utf-8', errors='ignore', stderr=subprocess.DEVNULL
            )
            process_running = 'lightroom.exe' in out.lower()
        else:
            result = subprocess.run(['pgrep', '-xi', 'lightroom'], capture_output=True)
            process_running = result.returncode == 0
    except Exception:
        process_running = False

    if process_running:
        return True

    # Process not running — clean up stale lock file if present
    if catalog_path:
        lock = Path(str(catalog_path) + '.lock')
        if lock.exists():
            try:
                lock.unlink()
            except Exception:
                pass

    return False


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _lr_path(p: Path) -> str:
    return p.as_posix()


def _to_lr_time(unix_ts: float) -> float:
    return unix_ts - _LR_EPOCH_OFFSET


def _ensure_plugin_specs(con: sqlite3.Connection) -> dict:
    """
    Return {key: (spec_id, table)} for each aircraftmetadata field.
    If any specs are missing from the catalog (plugin never used), insert them.
    """
    rows = con.execute(
        "SELECT id_local, key, flattenedAttributes FROM AgPhotoPropertySpec WHERE sourcePlugin = ?",
        (PLUGIN_ID,)
    ).fetchall()
    existing = {key: (spec_id, ('AgSearchablePhotoProperty' if 'AgSearchablePhotoProperty' in (attrs or '') else 'AgPhotoProperty'))
                for spec_id, key, attrs in rows}

    for key, display_name, table, attrs in _PLUGIN_SPECS:
        if key not in existing:
            cur = con.execute(
                "INSERT INTO AgPhotoPropertySpec (id_global, flattenedAttributes, key, pluginVersion, sourcePlugin, userVisibleName)"
                " VALUES (?,?,?,?,?,?)",
                (_uid(), attrs, key, 0.0, PLUGIN_ID, display_name)
            )
            existing[key] = (cur.lastrowid, table)

    return existing


def _find_or_create_root_folder(con: sqlite3.Connection, catalog_path: Path, root_dir: Path) -> int:
    abs_path = _lr_path(root_dir) + '/'
    row = con.execute(
        "SELECT id_local FROM AgLibraryRootFolder WHERE absolutePath = ?", (abs_path,)
    ).fetchone()
    if row:
        return row[0]

    try:
        rel = root_dir.relative_to(catalog_path.parent)
        rel_path = _lr_path(rel) + '/'
    except ValueError:
        rel_path = os.path.relpath(root_dir, catalog_path.parent).replace('\\', '/') + '/'

    cur = con.execute(
        "INSERT INTO AgLibraryRootFolder (id_global, absolutePath, name, relativePathFromCatalog) VALUES (?,?,?,?)",
        (_uid(), abs_path, root_dir.name, rel_path)
    )
    return cur.lastrowid


def _find_or_create_folder(con: sqlite3.Connection, root_id: int, path_from_root: str) -> int:
    row = con.execute(
        "SELECT id_local FROM AgLibraryFolder WHERE rootFolder = ? AND pathFromRoot = ?",
        (root_id, path_from_root)
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO AgLibraryFolder (id_global, parentId, pathFromRoot, rootFolder, visibility) VALUES (?,?,?,?,?)",
        (_uid(), None, path_from_root, root_id, None)
    )
    return cur.lastrowid


def _create_file_record(con: sqlite3.Connection, fpath: Path, folder_id: int) -> tuple:
    """Insert AgLibraryFile + Adobe_images for a new file. Returns (file_id, image_id)."""
    stat = fpath.stat()
    lr_mtime = _to_lr_time(stat.st_mtime)
    capture_time = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%dT%H:%M:%S')
    stem = fpath.stem
    ext = fpath.suffix.lstrip('.')
    filename = fpath.name
    file_format = 'RAW' if fpath.suffix.lower() in _RAW_EXTS else 'JPEG'

    cur = con.execute(
        """INSERT INTO AgLibraryFile
           (id_global, baseName, extension, externalModTime, folder,
            idx_filename, lc_idx_filename, lc_idx_filenameExtension,
            modTime, originalFilename, sidecarExtensions)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (_uid(), stem, ext, lr_mtime, folder_id,
         filename, filename.lower(), ext.lower(),
         lr_mtime, filename, '')
    )
    file_id = cur.lastrowid

    cur2 = con.execute(
        """INSERT INTO Adobe_images
           (id_global, captureTime, fileFormat, pick, rating, editLock,
            hasMissingSidecars, touchCount, touchTime, rootFile)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (_uid(), capture_time, file_format, 0, None, 0, 0, 0, lr_mtime, file_id)
    )
    return file_id, cur2.lastrowid


def _upsert_property(con: sqlite3.Connection, table: str, image_id: int, spec_id: int, value: str):
    existing = con.execute(
        f"SELECT id_local FROM {table} WHERE photo = ? AND propertySpec = ?",
        (image_id, spec_id)
    ).fetchone()
    lc = value.lower() if value else ''
    if existing:
        if table == 'AgSearchablePhotoProperty':
            con.execute(
                f"UPDATE {table} SET internalValue=?, lc_idx_internalValue=? WHERE id_local=?",
                (value, lc, existing[0])
            )
        else:
            con.execute(f"UPDATE {table} SET internalValue=? WHERE id_local=?", (value, existing[0]))
    else:
        if table == 'AgSearchablePhotoProperty':
            con.execute(
                f"INSERT INTO {table} (id_global,dataType,internalValue,lc_idx_internalValue,photo,propertySpec)"
                f" VALUES (?,?,?,?,?,?)",
                (_uid(), None, value, lc, image_id, spec_id)
            )
        else:
            con.execute(
                f"INSERT INTO {table} (id_global,dataType,internalValue,photo,propertySpec) VALUES (?,?,?,?,?)",
                (_uid(), None, value, image_id, spec_id)
            )


def get_keywords(catalog_path: Path) -> list:
    """Return all keyword names from the catalog, sorted alphabetically."""
    if not catalog_path.exists():
        return []
    try:
        con = sqlite3.connect(str(catalog_path))
        rows = con.execute(
            "SELECT name FROM AgLibraryKeyword WHERE name IS NOT NULL ORDER BY lc_name"
        ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _lr_genealogy(root_id: int, own_id: int) -> str:
    """Build LR's genealogy string: /NrootROOT/NownOWN where N = digit count."""
    r, o = str(root_id), str(own_id)
    return f'/{len(r)}{root_id}/{len(o)}{own_id}'


def _keyword_root_id(con: sqlite3.Connection) -> int:
    """Return the invisible root keyword node (parent IS NULL, name IS NULL)."""
    row = con.execute(
        "SELECT id_local FROM AgLibraryKeyword WHERE parent IS NULL"
    ).fetchone()
    return row[0] if row else None


def _find_or_create_keyword(con: sqlite3.Connection, name: str, root_id: int = None) -> int:
    lc = name.lower().strip()
    row = con.execute(
        "SELECT id_local FROM AgLibraryKeyword WHERE lc_name = ?", (lc,)
    ).fetchone()
    if row:
        return row[0]
    if root_id is None:
        root_id = _keyword_root_id(con)
    cur = con.execute(
        """INSERT INTO AgLibraryKeyword
           (id_global, dateCreated, genealogy, imageCountCache, includeOnExport,
            includeParents, includeSynonyms, lc_name, name, parent)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (_uid(), _to_lr_time(__import__('time').time()),
         '', -1, 1, 1, 1, lc, name.strip(), root_id)
    )
    own_id = cur.lastrowid
    genealogy = _lr_genealogy(root_id, own_id) if root_id else f'/{len(str(own_id))}{own_id}'
    con.execute("UPDATE AgLibraryKeyword SET genealogy = ? WHERE id_local = ?", (genealogy, own_id))
    return own_id


def _read_exif_for_catalog(path: Path) -> dict:
    """Read camera model, capture time, and timezone offset from raw/JPEG EXIF."""
    result = {'camera_model': None, 'capture_time': None, 'tz_offset_secs': None}
    try:
        with open(path, 'rb') as f:
            data = f.read(131072)

        # Locate TIFF block (raw = starts at 0, JPEG = in APP1)
        if data[:2] == b'\xff\xd8':
            tiff_start, pos = None, 2
            while pos + 4 < len(data):
                marker = data[pos:pos+2]
                seg_len = struct.unpack_from('>H', data, pos+2)[0]
                if marker == b'\xff\xe1' and data[pos+4:pos+10] == b'Exif\x00\x00':
                    tiff_start = pos + 10
                    break
                pos += 2 + seg_len
            if tiff_start is None:
                return result
        else:
            tiff_start = 0

        bo = data[tiff_start:tiff_start+2]
        endian = '<' if bo == b'II' else '>' if bo == b'MM' else None
        if not endian:
            return result

        def read_str(ep, cnt):
            if cnt <= 4:
                return data[ep+8:ep+8+cnt].rstrip(b'\x00').decode('ascii', errors='replace').strip()
            off = tiff_start + struct.unpack_from(endian+'I', data, ep+8)[0]
            return data[off:off+cnt].rstrip(b'\x00').decode('ascii', errors='replace').strip()

        ifd0_pos = tiff_start + struct.unpack_from(endian+'I', data, tiff_start+4)[0]
        n = struct.unpack_from(endian+'H', data, ifd0_pos)[0]
        exif_ifd_pos = None

        for i in range(min(n, 256)):
            ep = ifd0_pos + 2 + i * 12
            if ep + 12 > len(data): break
            tag = struct.unpack_from(endian+'H', data, ep)[0]
            cnt = struct.unpack_from(endian+'I', data, ep+4)[0]
            if tag == 0x0110:   # Model
                result['camera_model'] = read_str(ep, cnt)
            elif tag == 0x8769: # ExifIFD pointer
                exif_ifd_pos = tiff_start + struct.unpack_from(endian+'I', data, ep+8)[0]

        if exif_ifd_pos and exif_ifd_pos + 2 <= len(data):
            n2 = struct.unpack_from(endian+'H', data, exif_ifd_pos)[0]
            for i in range(min(n2, 512)):
                ep = exif_ifd_pos + 2 + i * 12
                if ep + 12 > len(data): break
                tag = struct.unpack_from(endian+'H', data, ep)[0]
                cnt = struct.unpack_from(endian+'I', data, ep+4)[0]
                if tag == 0x9003:  # DateTimeOriginal "2026:05:30 08:43:12"
                    s = read_str(ep, cnt)
                    if len(s) >= 19:
                        result['capture_time'] = s[:10].replace(':', '-') + 'T' + s[11:19]
                elif tag == 0x9010 and result['tz_offset_secs'] is None:  # OffsetTimeOriginal "+10:00"
                    m = _re.match(r'([+-])(\d{2}):(\d{2})', read_str(ep, cnt))
                    if m:
                        sign = 1 if m.group(1) == '+' else -1
                        result['tz_offset_secs'] = sign * (int(m.group(2))*3600 + int(m.group(3))*60)
                elif tag == 0x882A and result['tz_offset_secs'] is None:  # TimeZoneOffset (signed short, hours)
                    result['tz_offset_secs'] = struct.unpack_from(endian+'h', data, ep+8)[0] * 3600
    except Exception:
        pass
    return result


def _write_exif_metadata(con: sqlite3.Connection, image_id: int, camera_model: str, tz_offset_secs):
    """Write camera model + timezone offset into the catalog EXIF tables."""
    cam_ref = None
    if camera_model:
        sc = f'/t{camera_model.lower()}/t'
        row = con.execute(
            "SELECT id_local FROM AgInternedExifCameraModel WHERE searchIndex = ?", (sc,)
        ).fetchone()
        cam_ref = row[0] if row else con.execute(
            "INSERT INTO AgInternedExifCameraModel (searchIndex, value) VALUES (?,?)",
            (sc, camera_model)
        ).lastrowid

    exif_row = con.execute(
        "SELECT id_local FROM AgHarvestedExifMetadata WHERE image = ?", (image_id,)
    ).fetchone()
    if exif_row:
        if cam_ref:
            con.execute(
                "UPDATE AgHarvestedExifMetadata SET cameraModelRef=? WHERE id_local=?",
                (cam_ref, exif_row[0])
            )
    else:
        con.execute(
            "INSERT INTO AgHarvestedExifMetadata (image, cameraModelRef) VALUES (?,?)",
            (image_id, cam_ref)
        )

    if tz_offset_secs is not None:
        tz_exists = con.execute(
            "SELECT 1 FROM AgLibraryImageChangeCounter WHERE image = ?", (image_id,)
        ).fetchone()
        now_iso = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        if tz_exists:
            con.execute(
                "UPDATE AgLibraryImageChangeCounter SET localTimeOffsetSecs=? WHERE image=?",
                (tz_offset_secs, image_id)
            )
        else:
            con.execute(
                """INSERT INTO AgLibraryImageChangeCounter
                   (image, changeCounter, lastSyncedChangeCounter, changedAtTime, localTimeOffsetSecs)
                   VALUES (?,1,0,?,?)""",
                (image_id, now_iso, tz_offset_secs)
            )


def _has_file_gps(path: Path) -> bool:
    """Return True if the file has a GPS IFD embedded in its EXIF."""
    import struct
    try:
        with open(path, 'rb') as f:
            chunk = f.read(65536)
        if chunk[:2] == b'\xff\xd8':  # JPEG
            pos = 2
            while pos + 4 < len(chunk):
                marker = chunk[pos:pos+2]
                seg_len = struct.unpack_from('>H', chunk, pos+2)[0]
                if marker == b'\xff\xe1' and chunk[pos+4:pos+10] == b'Exif\x00\x00':
                    tiff_start = pos + 10
                    break
                pos += 2 + seg_len
            else:
                return False
        else:
            tiff_start = 0
        bo = chunk[tiff_start:tiff_start+2]
        endian = '<' if bo == b'II' else '>' if bo == b'MM' else None
        if not endian:
            return False
        ifd0_off = struct.unpack_from(endian+'I', chunk, tiff_start+4)[0] + tiff_start
        if ifd0_off + 2 > len(chunk):
            return False
        count = struct.unpack_from(endian+'H', chunk, ifd0_off)[0]
        for i in range(count):
            o = ifd0_off + 2 + i*12
            if o + 12 > len(chunk):
                break
            tag = struct.unpack_from(endian+'H', chunk, o)[0]
            if tag == 0x8825:  # GPS IFD pointer
                return True
    except Exception:
        pass
    return False


def _dd_to_lr_gps(decimal_deg: float, is_lat: bool) -> str:
    """Convert decimal degrees to LR's GPS string format: 'DD,MM.MMMMMMMH'"""
    abs_deg = abs(decimal_deg)
    degrees = int(abs_deg)
    minutes = (abs_deg - degrees) * 60
    hemi = ('N' if decimal_deg >= 0 else 'S') if is_lat else ('E' if decimal_deg >= 0 else 'W')
    return f"{degrees},{minutes:.7f}{hemi}"


def _make_gps_xmp_blob(lat: float, lon: float) -> bytes:
    """Build the compressed XMP blob for Adobe_AdditionalMetadata containing GPS."""
    lat_str = _dd_to_lr_gps(lat, True)
    lon_str = _dd_to_lr_gps(lon, False)
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000, 0000/00/00-00:00:00        ">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:exif="http://ns.adobe.com/exif/1.0/"\n'
        f'   exif:GPSLatitude="{lat_str}"\n'
        f'   exif:GPSLongitude="{lon_str}">\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>'
    )
    xmp_bytes = xmp.encode('utf-8')
    return struct.pack('>I', len(xmp_bytes)) + zlib.compress(xmp_bytes)


def _write_gps(con: sqlite3.Connection, image_id: int, lat: float, lon: float, is_raw: bool = True):
    """Write airport GPS to the catalog (AgHarvestedExifMetadata + Adobe_AdditionalMetadata)."""
    # --- AgHarvestedExifMetadata (search index) ---
    exif_row = con.execute(
        "SELECT id_local, hasGPS FROM AgHarvestedExifMetadata WHERE image = ?", (image_id,)
    ).fetchone()
    if exif_row:
        if not exif_row[1]:
            con.execute(
                "UPDATE AgHarvestedExifMetadata SET gpsLatitude=?, gpsLongitude=?, gpsSequence=1.0, hasGPS=1 WHERE id_local=?",
                (lat, lon, exif_row[0])
            )
    else:
        con.execute(
            "INSERT INTO AgHarvestedExifMetadata (image, gpsLatitude, gpsLongitude, gpsSequence, hasGPS) VALUES (?,?,?,?,?)",
            (image_id, lat, lon, 1.0, 1)
        )

    # --- Adobe_AdditionalMetadata (XMP blob — what LR Map actually reads) ---
    am_row = con.execute(
        "SELECT id_local, xmp FROM Adobe_AdditionalMetadata WHERE image = ?", (image_id,)
    ).fetchone()
    if am_row:
        existing_xmp = am_row[1]
        # Only patch if GPS not already present
        if isinstance(existing_xmp, bytes) and len(existing_xmp) > 4:
            text = zlib.decompress(existing_xmp[4:]).decode('utf-8', errors='replace')
        else:
            text = existing_xmp or ''
        if 'GPSLatitude' not in text:
            lat_str = _dd_to_lr_gps(lat, True)
            lon_str = _dd_to_lr_gps(lon, False)
            gps_attrs = f'   exif:GPSLatitude="{lat_str}"\n   exif:GPSLongitude="{lon_str}"\n'
            # Inject GPS attrs just before the closing > of rdf:Description
            patched = text.replace('   exif:', gps_attrs + '   exif:', 1)
            if patched == text:
                # No existing exif attrs — inject before closing > of rdf:Description
                patched = text.replace('>\n  </rdf:Description>', f'\n{gps_attrs}  </rdf:Description>', 1)
                if patched == text:
                    patched = text  # give up patching, GPS just won't be in XMP
            patched_bytes = patched.encode('utf-8')
            blob = struct.pack('>I', len(patched_bytes)) + zlib.compress(patched_bytes)
            con.execute(
                "UPDATE Adobe_AdditionalMetadata SET xmp=? WHERE id_local=?",
                (blob, am_row[0])
            )
    else:
        blob = _make_gps_xmp_blob(lat, lon)
        con.execute(
            """INSERT INTO Adobe_AdditionalMetadata
               (id_global, additionalInfoSet, embeddedXmp, externalXmpIsDirty, image,
                incrementalWhiteBalance, isRawFile, lastSynchronizedTimestamp, monochrome, xmp)
               VALUES (?,0,0,0,?,0,?,?,0,?)""",
            (_uid(), image_id, 1 if is_raw else 0, -63113817600.0, blob)
        )


def _write_keywords(con: sqlite3.Connection, image_id: int, keyword_ids: list):
    existing = {r[0] for r in con.execute(
        "SELECT tag FROM AgLibraryKeywordImage WHERE image = ?", (image_id,)
    ).fetchall()}
    for kid in keyword_ids:
        if kid not in existing:
            con.execute(
                "INSERT INTO AgLibraryKeywordImage (image, tag) VALUES (?,?)",
                (image_id, kid)
            )


def update_catalog(
    catalog_path: Path,
    file_paths: list,
    destination_folder: Path,
    metadata: dict,
    lr_root: Path = None,
    keywords: list = None,
    airport_coords: tuple = None,
) -> tuple:
    """
    Register files in the LR catalog and write plugin metadata.

    destination_folder: absolute path to the registration folder
        (e.g. .../Plane Spotting/2026 - Oceania/20260530 - SYD/Qantas/VH-OGB)

    lr_root: the folder LR uses as its root entry — typically the session folder
        (e.g. .../Plane Spotting/2026 - Oceania). If None, falls back to the
        date-airport folder (legacy single-level behaviour).

    Returns (updated_count, errors_list).
    """
    if not catalog_path.exists():
        return 0, [f'Catalog not found: {catalog_path}']

    # Determine the LR root and the path-from-root to the destination
    if lr_root is None:
        lr_root = destination_folder.parent.parent  # legacy: date-airport is the root

    try:
        rel = destination_folder.relative_to(lr_root)
    except ValueError:
        return 0, [f'destination_folder is not under lr_root ({lr_root})']

    # Build the pathFromRoot strings for each level
    parts = rel.parts  # e.g. ('20260530 - SYD', 'Qantas', 'VH-OGB')
    leaf_path_from_root = '/'.join(parts) + '/'  # '20260530 - SYD/Qantas/VH-OGB/'

    root_abs = _lr_path(lr_root) + '/'  # used for collision-safe lookup

    updated = 0
    errors = []
    con = sqlite3.connect(str(catalog_path))
    try:
        spec_ids = _ensure_plugin_specs(con)

        root_id = _find_or_create_root_folder(con, catalog_path, lr_root)

        # Create the full folder hierarchy under this root
        _find_or_create_folder(con, root_id, '')
        current = ''
        for part in parts[:-1]:
            current = f'{current}{part}/'
            _find_or_create_folder(con, root_id, current)
        leaf_folder_id = _find_or_create_folder(con, root_id, leaf_path_from_root)
        kw_root_id = _keyword_root_id(con) if keywords else None

        for fpath in file_paths:
            if fpath.suffix.lower() not in _RAW_EXTS | {'.jpg', '.jpeg', '.tiff', '.tif', '.png'}:
                continue
            filename = fpath.name
            lc_filename = filename.lower()

            # Safe lookup: match filename AND exact destination folder.
            # This prevents collisions with identically-named files from past sessions.
            row = con.execute(
                """SELECT lf.id_local, img.id_local
                   FROM AgLibraryFile lf
                   JOIN AgLibraryFolder f   ON lf.folder    = f.id_local
                   JOIN AgLibraryRootFolder rf ON f.rootFolder = rf.id_local
                   JOIN Adobe_images img    ON img.rootFile  = lf.id_local
                   WHERE lf.lc_idx_filename = ?
                     AND rf.absolutePath    = ?
                     AND f.pathFromRoot     = ?""",
                (lc_filename, root_abs, leaf_path_from_root)
            ).fetchone()

            if row:
                # Already at the right location — just refresh metadata
                file_id, image_id = row
            else:
                if not fpath.exists():
                    errors.append(f'{filename}: file not found at destination')
                    continue
                file_id, image_id = _create_file_record(con, fpath, leaf_folder_id)

            for key, value in metadata.items():
                if value and key in spec_ids:
                    spec_id, table = spec_ids[key]
                    _upsert_property(con, table, image_id, spec_id, value)

            if keywords:
                kw_ids = [_find_or_create_keyword(con, kw, kw_root_id) for kw in keywords if kw.strip()]
                _write_keywords(con, image_id, kw_ids)

            if airport_coords and not _has_file_gps(fpath):
                _write_gps(con, image_id, airport_coords[0], airport_coords[1],
                           is_raw=fpath.suffix.lower() in _RAW_EXTS)

            # Read and write EXIF (camera model + timezone) — also updates captureTime
            exif = _read_exif_for_catalog(fpath)
            _write_exif_metadata(con, image_id, exif['camera_model'], exif['tz_offset_secs'])
            if exif['capture_time']:
                con.execute(
                    "UPDATE Adobe_images SET captureTime=? WHERE id_local=?",
                    (exif['capture_time'], image_id)
                )

            updated += 1

        con.commit()
    except Exception as e:
        errors.append(str(e))
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        con.close()

    return updated, errors
