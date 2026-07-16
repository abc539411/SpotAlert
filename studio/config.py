"""
Configuration for SpotAlert Studio
"""
import os
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent

# Paths
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
APP_DIR = PROJECT_ROOT / "app"
UTILS_DIR = PROJECT_ROOT / "utils"

# NAS Configuration
NAS_IP = "192.168.4.100"
_prod_output  = r'\\192.168.4.100\Photo\Plane Spotting'
_prod_inbox   = r'\\192.168.4.100\Photo\Inbox'
_prod_catalog = r'\\192.168.4.100\public\docker\spottingstation\catalog\Planespotting Catalogue\Planespotting Catalog-v2.lrcat'

NAS_BASE_PATH = os.getenv('OUTPUT_PATH', r'\\192.168.4.100\Photo\Plane Spotting')
INBOX_PATH    = Path(os.getenv('INBOX_PATH', r'C:\Users\abc53\Pictures\Planespotting Inbox'))

# JetPhotos — fallback lookup only, used when FR24 has no data for a registration
# (e.g. military aircraft, which FR24 doesn't track). See utils/fr24_lookup.py for
# the primary lookup path.
JETPHOTOS_BASE_URL = "https://www.jetphotos.com/registration"

# App Configuration
APP_TITLE = "SpotAlert Studio"
APP_DESCRIPTION = "Automated plane spotting photo organization and metadata management"

# Supported RAW formats
SUPPORTED_RAW_FORMATS = {'.cr2', '.nef', '.arw', '.dng', '.raf', '.rw2', '.orf', '.srw', '.raw'}
SUPPORTED_IMAGE_FORMATS = SUPPORTED_RAW_FORMATS | {'.jpg', '.jpeg', '.png', '.tiff'}

# Cache settings
CACHE_DIR = DATA_DIR / "cache"
CACHE_EXPIRY_HOURS = 168  # 1 week
THUMB_CACHE_DIR = DATA_DIR / "thumbnails"
THUMB_MAX_PX = 640  # long edge — enough for grid + edit view

# Lightroom Classic catalog path — set LR_CATALOG_PATH env var to override.
_default_catalog = r'C:\Users\abc53\Pictures\Lightroom\Planespotting Catalogue\Planespotting Catalog-v2.lrcat'
LR_CATALOG_PATH = os.getenv('LR_CATALOG_PATH', _default_catalog)

# Logging
LOG_DIR = DATA_DIR / "logs"
