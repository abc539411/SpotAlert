"""
File handling and folder organization
"""
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import datetime
from config import SUPPORTED_RAW_FORMATS
import shutil


class FileOrganizer:
    """Handle file organization and folder structure creation"""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_folder_structure_path(
        self,
        date: str,  # Format: YYYYMMDD
        airport: str,  # Format: SYD
        airline: str,  # Format: Air Canada
        registration: str  # Format: C-BASJ
    ) -> Path:
        """Generate the folder structure path"""
        # Format: 20260606 - SYD / Air Canada / C-BASJ
        date_airport = f"{date} - {airport}"
        path = self.base_path / date_airport / airline / registration
        return path

    def create_folder_structure(
        self,
        date: str,
        airport: str,
        airline: str,
        registration: str
    ) -> Tuple[bool, Path, str]:
        """
        Create folder structure for organized photos

        Returns:
            (success: bool, path: Path, message: str)
        """
        try:
            path = self.get_folder_structure_path(date, airport, airline, registration)
            path.mkdir(parents=True, exist_ok=True)
            return True, path, f"Folder created: {path}"
        except Exception as e:
            return False, None, f"Error creating folder: {str(e)}"

    def move_files(
        self,
        source_files: List[Path],
        destination: Path
    ) -> Tuple[int, int, List[str]]:
        """
        Move files to destination folder

        Returns:
            (success_count: int, failed_count: int, errors: List[str])
        """
        success_count = 0
        failed_count = 0
        errors = []

        for file in source_files:
            try:
                dest_file = destination / file.name
                shutil.move(str(file), str(dest_file))
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{file.name}: {str(e)}")

        return success_count, failed_count, errors

    def copy_files(
        self,
        source_files: List[Path],
        destination: Path
    ) -> Tuple[int, int, List[str]]:
        """
        Copy files to destination folder

        Returns:
            (success_count: int, failed_count: int, errors: List[str])
        """
        success_count = 0
        failed_count = 0
        errors = []

        for file in source_files:
            try:
                dest_file = destination / file.name
                shutil.copy2(str(file), str(dest_file))
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{file.name}: {str(e)}")

        return success_count, failed_count, errors

    def get_raw_files(self, directory: Path) -> List[Path]:
        """Get all RAW image files from directory"""
        if not directory.exists():
            return []

        files = []
        for ext in SUPPORTED_RAW_FORMATS:
            files.extend(directory.glob(f"*{ext}"))
            files.extend(directory.glob(f"*{ext.upper()}"))

        return sorted(files)

    def get_image_files(self, directory: Path) -> List[Path]:
        """Get all image files from directory (including RAW and common formats)"""
        if not directory.exists():
            return []

        files = []
        for ext in {'.cr2', '.nef', '.arw', '.dng', '.raf', '.rw2', '.orf', '.srw', '.raw',
                    '.jpg', '.jpeg', '.png', '.tiff', '.tif'}:
            files.extend(directory.glob(f"*{ext}"))
            files.extend(directory.glob(f"*{ext.upper()}"))

        return sorted(files)

    def get_file_info(self, file: Path) -> Dict:
        """Get file information"""
        stat = file.stat()
        return {
            'name': file.name,
            'size': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'path': str(file)
        }

    def validate_file_path(self, file_path: Path) -> Tuple[bool, str]:
        """Validate file path"""
        if not file_path.exists():
            return False, "File does not exist"

        if not file_path.is_file():
            return False, "Path is not a file"

        if file_path.suffix.lower() not in SUPPORTED_RAW_FORMATS:
            return False, f"Unsupported file format: {file_path.suffix}"

        return True, "Valid"
