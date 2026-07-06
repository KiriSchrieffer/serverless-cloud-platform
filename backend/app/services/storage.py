"""Local package, result, and log storage abstractions."""

import hashlib
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from backend.app.core.config import settings


class PackageValidationError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class LogFileNotFoundError(Exception):
    def __init__(self, logs_ref: str) -> None:
        super().__init__(f"Invocation log file not found: {logs_ref}")
        self.logs_ref = logs_ref


@dataclass(frozen=True)
class StoredPackage:
    package_uri: str
    package_hash: str


class LocalPackageStorageService:
    def __init__(
        self,
        package_storage_dir: str | Path = settings.package_storage_dir,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.package_storage_dir = self.resolve_path(package_storage_dir)

    def store_function_package(
        self,
        *,
        owner_id: UUID,
        function_name: str,
        version_number: int,
        handler: str,
        contents: bytes,
    ) -> StoredPackage:
        self.validate_package(contents=contents, handler=handler)
        package_hash = hashlib.sha256(contents).hexdigest()
        package_path = (
            self.package_storage_dir
            / str(owner_id)
            / function_name
            / f"v{version_number}"
            / "function.zip"
        )
        package_path.parent.mkdir(parents=True, exist_ok=True)
        package_path.write_bytes(contents)
        return StoredPackage(
            package_uri=self.storage_ref(package_path),
            package_hash=package_hash,
        )

    def validate_package(self, *, contents: bytes, handler: str) -> None:
        if not contents:
            raise PackageValidationError("Package file is empty")

        try:
            with zipfile.ZipFile(BytesIO(contents)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise PackageValidationError("Package file must be a valid zip archive") from exc

        self.validate_safe_zip_paths(names)
        handler_module = handler.rsplit(".", 1)[0]
        expected_module_path = f"{handler_module.replace('.', '/')}.py"
        if expected_module_path not in names:
            raise PackageValidationError(
                f"Package must contain handler module '{expected_module_path}'"
            )

    @staticmethod
    def validate_safe_zip_paths(names: list[str]) -> None:
        for name in names:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise PackageValidationError("Package zip contains unsafe file paths")

    def storage_ref(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path.as_posix()

    def resolve_path(self, path: str | Path) -> Path:
        resolved = Path(path)
        if resolved.is_absolute():
            return resolved
        return (self.workspace_root / resolved).resolve()


class LocalLogStorageService:
    def __init__(
        self,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()

    def read_logs(self, logs_ref: str | None) -> str:
        if logs_ref is None:
            return ""

        log_path = self.resolve_storage_ref(logs_ref)
        if not log_path.exists():
            raise LogFileNotFoundError(logs_ref)
        return log_path.read_text(encoding="utf-8", errors="replace")

    def resolve_storage_ref(self, storage_ref: str) -> Path:
        path = Path(storage_ref)
        if path.is_absolute():
            return path

        resolved = (self.workspace_root / path).resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise LogFileNotFoundError(storage_ref)
        return resolved
