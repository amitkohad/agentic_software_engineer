"""Safe, atomic persistence of validated generated artifacts."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.code_generation_plan import (
    FileSpecification,
    GeneratedArtifact,
    GenerationStatus,
    OverwritePolicy,
)


class WriteResult(BaseModel):
    """Structured outcome for one artifact persistence request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1, description="Project-relative artifact path.")
    written: bool = Field(description="Whether artifact content was persisted.")
    approval_required: bool = Field(default=False, description="Whether overwrite requires human approval.")
    action: str = Field(min_length=1, description="Write, preserve, approval, or rejection action.")
    previous_hash: str | None = Field(default=None, description="SHA-256 hash of the replaced file when present.")
    new_hash: str | None = Field(default=None, description="SHA-256 hash of persisted artifact content when written.")
    backup_path: str | None = Field(default=None, description="Timestamped backup path created before replacement.")
    message: str = Field(min_length=1, description="Safe, actionable result summary.")


class RollbackResult(BaseModel):
    """Structured outcome for rollback of the latest successful write operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rolled_back: bool = Field(description="Whether the latest write was successfully reverted.")
    path: str | None = Field(default=None, description="Project-relative path affected by rollback.")
    action: str = Field(min_length=1, description="Restore, remove, or no-operation action.")
    restored_hash: str | None = Field(default=None, description="SHA-256 hash of restored content when applicable.")
    message: str = Field(min_length=1, description="Safe, actionable rollback summary.")


class ArtifactProjectBuilder(Protocol):
    """Persistence boundary for validated generated artifacts."""

    def write(self, artifact: GeneratedArtifact, specification: FileSpecification) -> WriteResult:
        """Persist one validated artifact according to its overwrite policy."""

    def rollback_latest(self) -> RollbackResult:
        """Compensate the most recent successful artifact write."""


@dataclass(frozen=True, slots=True)
class _WriteOperation:
    """Internal rollback record for the most recent successful atomic write."""

    target: Path
    relative_path: str
    backup: Path | None
    previous_hash: str | None
    new_hash: str


class ProjectBuilder:
    """Persist generated artifacts safely beneath an approved project root.

    The builder treats generated content as untrusted until its declared hash,
    validation outcome, path, and overwrite policy are checked. It keeps an
    in-memory record of the latest successful write so callers can roll it back
    deterministically during the same builder lifetime.
    """

    _FORBIDDEN_PATH_PARTS = frozenset({".git", ".venv", ".ssh", "secrets", "credentials"})
    _FAILED_VALIDATION_STATUSES = frozenset({GenerationStatus.VALIDATION_FAILED, GenerationStatus.FAILED})

    def __init__(self, project_root: Path, *, logger: logging.Logger | None = None) -> None:
        """Create a builder constrained to the supplied, caller-approved project root."""
        self._project_root = project_root.resolve()
        self._project_root.mkdir(parents=True, exist_ok=True)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = RLock()
        self._latest_write: _WriteOperation | None = None

    def write(self, artifact: GeneratedArtifact, specification: FileSpecification) -> WriteResult:
        """Validate and atomically persist one artifact according to its overwrite policy.

        Args:
            artifact: In-memory artifact produced by a generator and validator.
            specification: Approved file contract that controls target and overwrite behavior.

        Returns:
            A structured write outcome. Protected or approval-gated paths are
            returned without filesystem modification.
        """
        with self._lock:
            self._validate_artifact_contract(artifact, specification)
            target = self._resolve_target(specification.path)
            if self._is_forbidden(target):
                return self._rejected_result(specification.path, "Target path is protected and cannot be written.")
            if artifact.validation_status in self._FAILED_VALIDATION_STATUSES:
                return self._rejected_result(specification.path, "Artifact validation status prevents writing.")

            expected_hash = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
            if expected_hash != artifact.content_hash:
                return self._rejected_result(specification.path, "Artifact content hash does not match supplied content.")

            exists = target.is_file()
            if exists and specification.overwrite_policy in {OverwritePolicy.NEVER, OverwritePolicy.CREATE_ONLY}:
                return WriteResult(
                    path=specification.path,
                    written=False,
                    action="preserved",
                    message="Existing file is protected by overwrite policy.",
                )
            if exists and specification.overwrite_policy is OverwritePolicy.REQUIRE_APPROVAL:
                return WriteResult(
                    path=specification.path,
                    written=False,
                    approval_required=True,
                    action="approval_required",
                    message="Existing file requires human approval before replacement.",
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            previous_hash = self._sha256_file(target) if exists else None
            backup = self._create_backup(target) if exists else None
            self._atomic_write(target, artifact.content)
            action = "created" if not exists else "merged" if specification.overwrite_policy is OverwritePolicy.MERGE else "replaced"
            self._latest_write = _WriteOperation(
                target=target,
                relative_path=specification.path,
                backup=backup,
                previous_hash=previous_hash,
                new_hash=expected_hash,
            )
            self._logger.info(
                "Generated artifact persisted",
                extra={"file_id": artifact.file_id, "path": specification.path, "action": action},
            )
            return WriteResult(
                path=specification.path,
                written=True,
                action=action,
                previous_hash=previous_hash,
                new_hash=expected_hash,
                backup_path=str(backup) if backup is not None else None,
                message="Artifact was written atomically.",
            )

    def rollback_latest(self) -> RollbackResult:
        """Roll back the latest successful write when its target remains unchanged.

        Returns:
            A structured result that restores the previous backup or removes a
            newly created file. Rollback refuses to alter a target changed after
            the recorded write, preserving concurrent or manual changes.
        """
        with self._lock:
            operation = self._latest_write
            if operation is None:
                return RollbackResult(rolled_back=False, action="no_operation", message="No successful write is available to roll back.")
            if not operation.target.is_file():
                return RollbackResult(
                    rolled_back=False,
                    path=operation.relative_path,
                    action="rejected",
                    message="Rollback target no longer exists and was not modified.",
                )
            if self._sha256_file(operation.target) != operation.new_hash:
                return RollbackResult(
                    rolled_back=False,
                    path=operation.relative_path,
                    action="rejected",
                    message="Rollback target changed after generation and was not modified.",
                )

            if operation.backup is not None and operation.backup.is_file():
                os.replace(operation.backup, operation.target)
                restored_hash = self._sha256_file(operation.target)
                action = "restored"
            else:
                operation.target.unlink()
                restored_hash = None
                action = "removed"
            self._latest_write = None
            self._logger.info("Latest generated artifact rolled back", extra={"path": operation.relative_path, "action": action})
            return RollbackResult(
                rolled_back=True,
                path=operation.relative_path,
                action=action,
                restored_hash=restored_hash,
                message="Latest generated artifact write was rolled back.",
            )

    def _validate_artifact_contract(self, artifact: GeneratedArtifact, specification: FileSpecification) -> None:
        """Validate immutable artifact identity against the approved file specification."""
        if artifact.file_id != specification.id:
            raise ValueError("Generated artifact file_id does not match the approved file specification.")
        if artifact.path != specification.path:
            raise ValueError("Generated artifact path does not match the approved file specification.")

    def _resolve_target(self, relative_path: str) -> Path:
        """Resolve a target and enforce that it remains strictly inside the project root."""
        target = (self._project_root / relative_path).resolve()
        if target == self._project_root or self._project_root not in target.parents:
            raise ValueError("Generated artifact path escapes the approved project root.")
        return target

    def _is_forbidden(self, target: Path) -> bool:
        """Return whether a target contains a protected source-control, secret, or SSH path."""
        for part in target.relative_to(self._project_root).parts:
            normalized = part.casefold()
            if normalized in self._FORBIDDEN_PATH_PARTS or normalized.startswith(".env"):
                return True
            if "secret" in normalized or "credential" in normalized:
                return True
        return False

    def _create_backup(self, target: Path) -> Path:
        """Copy an existing target to a timestamped sibling backup before replacement."""
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = target.with_name(f"{target.name}.backup.{timestamp}")
        shutil.copy2(target, backup)
        return backup

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """Write content through a same-directory temporary file and atomically replace target."""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        """Return the SHA-256 content hash of a file without loading it all at once."""
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _rejected_result(path: str, message: str) -> WriteResult:
        """Create a consistent non-writing rejection result."""
        return WriteResult(path=path, written=False, action="rejected", message=message)
