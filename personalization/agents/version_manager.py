"""Version Manager Agent - manages version snapshots and rollback."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import ModificationPatch, RollbackResult, VersionSnapshot


class VersionManagerAgent:
    """
    Manages version snapshots for rollback functionality.

    Creates snapshots before applying modifications and provides
    rollback capabilities.
    """

    def __init__(self, patches_dir: Path):
        """
        Initialize the version manager.

        Args:
            patches_dir: Directory to store patches and snapshots
        """
        self.patches_dir = patches_dir
        self.snapshots_dir = patches_dir / "snapshots"
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        self._snapshots: list[VersionSnapshot] = []
        self._load_existing_snapshots()

    def _load_existing_snapshots(self):
        """Load existing snapshots from disk."""
        for snapshot_dir in self.snapshots_dir.glob("snap_*"):
            if not snapshot_dir.is_dir():
                continue
            snapshot_file = snapshot_dir / "snapshot.json"
            if snapshot_file.exists():
                try:
                    data = json.loads(snapshot_file.read_text(encoding="utf-8"))
                    self._snapshots.append(VersionSnapshot(**data))
                except Exception:
                    pass  # Skip invalid snapshot files

    def create_snapshot(self, patch: ModificationPatch, base_path: Path, save_extensions: bool = True) -> VersionSnapshot:
        """
        Create a snapshot before applying a modification.

        Args:
            patch: ModificationPatch that will be applied
            base_path: Base path of the project
            save_extensions: Whether extension files are being saved to disk

        Returns:
            VersionSnapshot with backup information
        """
        snapshot_id = f"snap_{len(self._snapshots):04d}_{int(datetime.now().timestamp())}"
        snapshot_path = self.snapshots_dir / snapshot_id
        snapshot_path.mkdir(exist_ok=True)

        backed_up_files: list[str] = []
        backup_info: list[dict[str, Any]] = []

        for file_patch in patch.patches:
            if file_patch.operation.value in ("modify", "delete"):
                original_path = base_path / file_patch.file_path
                if original_path.exists():
                    # Create backup
                    backup_name = file_patch.file_path.replace("/", "_").replace("\\", "_")
                    backup_path = snapshot_path / backup_name

                    # Read original content
                    original_content = original_path.read_text(encoding="utf-8")

                    # Save backup
                    backup_path.write_text(original_content, encoding="utf-8")

                    backed_up_files.append(file_patch.file_path)
                    backup_info.append({
                        "file_path": file_patch.file_path,
                        "backup_path": str(backup_path),
                        "original_exists": True,
                    })
                else:
                    backup_info.append({
                        "file_path": file_patch.file_path,
                        "backup_path": None,
                        "original_exists": False,
                    })
            elif file_patch.operation.value == "create":
                # For new files (including extension files), store the content that will be created
                # This allows us to delete the file on rollback
                backup_info.append({
                    "file_path": file_patch.file_path,
                    "backup_path": None,
                    "original_exists": False,
                    "new_content": file_patch.new_snippet,
                    "is_extension": file_patch.file_path.startswith("personalization/extensions/"),
                    "save_to_disk": save_extensions,  # Track whether this extension was saved
                })

        # Save patch for future reference
        patch_file = snapshot_path / "patch.json"
        patch_file.write_text(json.dumps(patch.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        # Create snapshot metadata
        snapshot = VersionSnapshot(
            id=snapshot_id,
            patch_id=patch.patch_id,
            created_at=datetime.now().isoformat(),
            backed_up_files=backed_up_files,
            snapshot_path=str(snapshot_path),
            description=f"Snapshot for patch: {patch.patch_id}",
            save_extensions=save_extensions,
        )

        # Save snapshot metadata
        snapshot_file = snapshot_path / "snapshot.json"
        snapshot_file.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        # Save backup info for extension file rollback
        backup_info_file = snapshot_path / "backup_info.json"
        backup_info_file.write_text(json.dumps(backup_info, ensure_ascii=False, indent=2), encoding="utf-8")

        self._snapshots.append(snapshot)

        return snapshot

    def rollback(self, snapshot_id: str, base_path: Path) -> RollbackResult:
        """
        Rollback to a previous snapshot.

        Args:
            snapshot_id: ID of the snapshot to rollback to
            base_path: Base path of the project

        Returns:
            RollbackResult with operation details
        """
        snapshot = self._find_snapshot(snapshot_id)
        if not snapshot:
            return RollbackResult(success=False, snapshot_id=snapshot_id, error="Snapshot not found")

        snapshot_path = Path(snapshot.snapshot_path)
        if not snapshot_path.exists():
            return RollbackResult(success=False, snapshot_id=snapshot_id, error="Snapshot files not found")

        reverted_files: list[str] = []
        deleted_extensions: list[str] = []

        # Restore backed up files
        for backed_file in snapshot.backed_up_files:
            backup_name = backed_file.replace("/", "_").replace("\\", "_")
            backup_path = snapshot_path / backup_name

            if backup_path.exists():
                target = base_path / backed_file
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target)
                reverted_files.append(backed_file)

        # Load backup info to handle extension file deletion
        backup_info_file = snapshot_path / "backup_info.json"
        if backup_info_file.exists() and snapshot.save_extensions:
            # Only delete extension files if they were saved to disk
            backup_info = json.loads(backup_info_file.read_text(encoding="utf-8"))
            for info in backup_info:
                if info.get("is_extension") and not info.get("original_exists"):
                    if info.get("save_to_disk", True):
                        # Delete the extension file that was created
                        ext_file_path = base_path / info["file_path"]
                        if ext_file_path.exists():
                            ext_file_path.unlink()
                            deleted_extensions.append(info["file_path"])

        return RollbackResult(
            success=True,
            snapshot_id=snapshot_id,
            reverted_files=reverted_files,
        )

    def _find_snapshot(self, snapshot_id: str) -> VersionSnapshot | None:
        """Find a snapshot by ID."""
        for snapshot in self._snapshots:
            if snapshot.id == snapshot_id:
                return snapshot
        return None

    def list_snapshots(self) -> list[VersionSnapshot]:
        """
        List all available snapshots.

        Returns:
            List of VersionSnapshot objects
        """
        return sorted(self._snapshots, key=lambda s: s.created_at, reverse=True)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """
        Delete a snapshot.

        Args:
            snapshot_id: ID of the snapshot to delete

        Returns:
            True if deleted, False if not found
        """
        snapshot = self._find_snapshot(snapshot_id)
        if not snapshot:
            return False

        # Remove from disk
        snapshot_path = Path(snapshot.snapshot_path)
        if snapshot_path.exists():
            shutil.rmtree(snapshot_path)

        # Remove from memory
        self._snapshots.remove(snapshot)
        return True

    def get_snapshot_info(self, snapshot_id: str) -> dict[str, Any] | None:
        """
        Get detailed information about a snapshot.

        Args:
            snapshot_id: ID of the snapshot

        Returns:
            Dictionary with snapshot details or None
        """
        snapshot = self._find_snapshot(snapshot_id)
        if not snapshot:
            return None

        return {
            "id": snapshot.id,
            "patch_id": snapshot.patch_id,
            "created_at": snapshot.created_at,
            "backed_up_files": snapshot.backed_up_files,
            "description": snapshot.description,
            "can_rollback": snapshot.backed_up_files is not None and len(snapshot.backed_up_files) > 0,
        }

    def prune_old_snapshots(self, keep_count: int = 10) -> int:
        """
        Remove old snapshots, keeping only the most recent ones.

        Args:
            keep_count: Number of recent snapshots to keep

        Returns:
            Number of snapshots deleted
        """
        sorted_snapshots = sorted(self._snapshots, key=lambda s: s.created_at, reverse=True)

        deleted = 0
        for snapshot in sorted_snapshots[keep_count:]:
            if self.delete_snapshot(snapshot.id):
                deleted += 1

        return deleted