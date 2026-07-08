from __future__ import annotations

from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
DEVICE_TRANSFER_DIR: Final = ROOT / "device_transfer"
CAMERA1_SOURCE_DIR: Final = DEVICE_TRANSFER_DIR / "camera1"
ORIN_SOURCE_DIR: Final = DEVICE_TRANSFER_DIR / "Edge"
FORBIDDEN_SECRET_NAMES: Final = {".env"}
FORBIDDEN_SECRET_SUFFIXES: Final = (".env", ".token")
FORBIDDEN_SECRET_DIRS: Final = {"secrets"}


class DeviceTransferBundleError(RuntimeError):
    pass


def _disabled_legacy_deploy_message() -> str:
    return (
        "device_transfer is the only device transfer root; "
        "legacy deploy bundle generation is disabled."
    )


def _is_forbidden_secret_name(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in FORBIDDEN_SECRET_NAMES
        or any(normalized.endswith(suffix) for suffix in FORBIDDEN_SECRET_SUFFIXES)
        or normalized in FORBIDDEN_SECRET_DIRS
    )


def find_forbidden_secret_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    findings: list[Path] = []
    for path in root.rglob("*"):
        parts = {part.lower() for part in path.parts}
        if parts & FORBIDDEN_SECRET_DIRS or _is_forbidden_secret_name(path.name):
            findings.append(path)
    return sorted(findings)


def assert_no_forbidden_secret_paths(root: Path) -> None:
    findings = find_forbidden_secret_paths(root)
    if findings:
        relative = ", ".join(str(path.relative_to(root)) for path in findings)
        raise DeviceTransferBundleError(f"secret files are not allowed in device_transfer: {relative}")


def build_camera1_bundle() -> Path:
    raise DeviceTransferBundleError(
        "device_transfer/camera1 is source-of-truth; direct rebuild is disabled. "
        f"Use existing folder: {CAMERA1_SOURCE_DIR}"
    )


def build_edge_orin_bundle() -> Path:
    raise DeviceTransferBundleError(
        "device_transfer/Edge is source-of-truth; direct rebuild is disabled. "
        f"Use existing folder: {ORIN_SOURCE_DIR}"
    )


def build_edge_bundle() -> None:
    raise DeviceTransferBundleError(_disabled_legacy_deploy_message())


def build_server_bundle() -> None:
    raise DeviceTransferBundleError(_disabled_legacy_deploy_message())


def build_device_transfer_bundles() -> tuple[Path, Path]:
    bundles = validate_device_transfer_bundles()
    for bundle in bundles:
        assert_no_forbidden_secret_paths(bundle)
    return bundles


def _require_paths(bundle_root: Path, required: list[str]) -> None:
    missing = [rel for rel in required if not (bundle_root / rel).exists()]
    if missing:
        raise DeviceTransferBundleError(
            f"{bundle_root.relative_to(ROOT)} missing required paths: "
            + ", ".join(missing)
        )


def validate_device_transfer_bundles() -> tuple[Path, Path]:
    camera1_root = CAMERA1_SOURCE_DIR
    edge_root = ORIN_SOURCE_DIR
    _require_paths(
        camera1_root,
        [
            "README.md",
            "edge/config.raspi_cam01.yaml",
            "edge/main.py",
            "edge/video_buffer.py",
            "shared/protocol.py",
            "scripts/systemd/elderly-edge-cam01.service",
            "scripts/systemd/elderly-mediamtx.service",
        ],
    )
    _require_paths(
        edge_root,
        [
            "README.md",
            "edge/config.orin_cam02.yaml",
            "edge/main.py",
            "edge/video_buffer.py",
            "server/config.orin.yaml",
            "server/config.yaml",
            "server/main.py",
            "server/services/stgcn_classifier.py",
            "shared/protocol.py",
            "tools/test_backend_batch.py",
            "scripts/systemd/elderly-orin-server.service",
            "scripts/systemd/elderly-edge-cam02.service",
        ],
    )
    forbidden = [
        camera1_root / "docs",
        edge_root / "docs",
        camera1_root / "server",
    ]
    existing_forbidden = [
        path.relative_to(ROOT).as_posix() for path in forbidden if path.exists()
    ]
    if existing_forbidden:
        raise DeviceTransferBundleError(
            "device_transfer contains forbidden duplicate paths: "
            + ", ".join(existing_forbidden)
        )
    return camera1_root, edge_root


def main() -> None:
    camera1_dir, edge_orin_dir = build_device_transfer_bundles()
    print("=" * 50)
    print("device_transfer source bundles validated:")
    print(f"  camera1 (Pi5)  : {camera1_dir}")
    print(f"  Edge    (Orin) : {edge_orin_dir}")
    print("legacy deploy bundle generation: disabled")
    print("=" * 50)


if __name__ == "__main__":
    main()
