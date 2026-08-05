"""SQLite persistence for Streamlit workflow state and generated artifacts."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import SecretStr

from agentic_software_engineer.application.ports.state_store import AgentState, StateStore
from agentic_software_engineer.codegen.project_builder import RollbackResult, WriteResult
from agentic_software_engineer.domain.entities.code_generation_plan import (
    FileSpecification,
    GeneratedArtifact,
    GenerationStatus,
    OverwritePolicy,
)
from agentic_software_engineer.orchestrator.state import WorkflowExecutionStatus


class ExecutionSummary(BaseModel):
    """Small execution projection suitable for history selectors."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    execution_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class RuntimeConfiguration(BaseModel):
    """Decrypted runtime configuration whose secret representation is masked."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    openai_api_key: SecretStr
    openai_model: str = Field(min_length=1)


class ConfigurationDecryptionError(RuntimeError):
    """Raised when persisted configuration cannot be safely decrypted."""


class SQLiteExecutionStore(StateStore):
    """Persist complete workflow snapshots in a local SQLite database."""

    _TERMINAL_STATUSES = {
        WorkflowExecutionStatus.SUCCEEDED.value,
        WorkflowExecutionStatus.FAILED.value,
        WorkflowExecutionStatus.CANCELLED.value,
    }

    def __init__(self, database_path: Path, *, logger: logging.Logger | None = None) -> None:
        """Create the schema in the approved SQLite database file."""
        self._database_path = database_path.resolve()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._logger = logger or logging.getLogger(__name__)
        self._initialize_schema()

    @property
    def database_path(self) -> Path:
        """Return the resolved database location for operator display."""
        return self._database_path

    def save_execution_state(self, state: AgentState) -> AgentState:
        """Atomically create or replace a complete JSON workflow snapshot."""
        payload = state.model_dump_json()
        updated_at = state.timestamps.updated_at.isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_executions (
                    execution_id, project_name, status, state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    project_name = excluded.project_name,
                    status = excluded.status,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.execution_id,
                    state.project_name,
                    state.execution_status.value,
                    payload,
                    state.timestamps.created_at.isoformat(),
                    updated_at,
                ),
            )
        self._logger.info("Workflow state saved to SQLite", extra={"execution_id": state.execution_id})
        return state.model_copy(deep=True)

    def load_execution_state(self, execution_id: str) -> AgentState | None:
        """Load and strictly rehydrate one workflow snapshot."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM workflow_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return AgentState.model_validate_json(row[0]) if row else None

    def update_partial_state(self, execution_id: str, updates: Mapping[str, Any]) -> AgentState | None:
        """Apply a validated top-level update and persist the resulting snapshot."""
        current = self.load_execution_state(execution_id)
        if current is None:
            return None
        payload = current.model_dump(mode="python")
        payload.update(updates)
        return self.save_execution_state(AgentState.model_validate(payload))

    def delete_state(self, execution_id: str) -> bool:
        """Delete an execution and its cascaded artifacts and write history."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM workflow_executions WHERE execution_id = ?", (execution_id,))
        return cursor.rowcount > 0

    def list_active_executions(self) -> list[AgentState]:
        """Return all non-terminal workflow snapshots ordered by latest update."""
        placeholders = ",".join("?" for _ in self._TERMINAL_STATUSES)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT state_json FROM workflow_executions WHERE status NOT IN ({placeholders}) ORDER BY updated_at DESC",
                tuple(sorted(self._TERMINAL_STATUSES)),
            ).fetchall()
        return [AgentState.model_validate_json(row[0]) for row in rows]

    def list_executions(self, *, limit: int = 50) -> list[ExecutionSummary]:
        """Return recent execution metadata without loading artifact content."""
        bounded_limit = max(1, min(limit, 200))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT execution_id, project_name, status, updated_at
                FROM workflow_executions ORDER BY updated_at DESC LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [
            ExecutionSummary(execution_id=row[0], project_name=row[1], status=row[2], updated_at=row[3])
            for row in rows
        ]

    def list_generated_artifacts(self, execution_id: str) -> list[GeneratedArtifact]:
        """Load the current validated artifact set for one execution."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_json FROM generated_artifacts
                WHERE execution_id = ? ORDER BY path
                """,
                (execution_id,),
            ).fetchall()
        return [GeneratedArtifact.model_validate_json(row[0]) for row in rows]

    def _initialize_schema(self) -> None:
        """Create normalized execution, artifact, and rollback tables."""
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS workflow_executions (
                    execution_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generated_artifacts (
                    execution_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (execution_id, path),
                    FOREIGN KEY (execution_id) REFERENCES workflow_executions(execution_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifact_write_operations (
                    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    previous_artifact_json TEXT,
                    new_hash TEXT NOT NULL,
                    rolled_back INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (execution_id) REFERENCES workflow_executions(execution_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_updated_at
                    ON workflow_executions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_artifact_operations_execution
                    ON artifact_write_operations(execution_id, operation_id DESC);
                CREATE TABLE IF NOT EXISTS runtime_configuration (
                    configuration_key TEXT PRIMARY KEY,
                    configuration_value TEXT NOT NULL,
                    encrypted INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it after the operation."""
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            connection.close()


class EncryptedRuntimeConfigurationStore:
    """Persist OpenAI runtime configuration without plaintext API-key storage."""

    _API_KEY_SETTING = "openai_api_key"
    _MODEL_SETTING = "openai_model"

    def __init__(
        self,
        execution_store: SQLiteExecutionStore,
        key_path: Path,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create an encrypted configuration adapter over the workflow database."""
        self._execution_store = execution_store
        self._key_path = key_path.resolve()
        self._logger = logger or logging.getLogger(__name__)
        try:
            self._fernet = Fernet(self._load_or_create_key())
        except (ValueError, TypeError) as error:
            raise ConfigurationDecryptionError("Configuration encryption key is invalid.") from error

    def save(self, *, openai_api_key: str, openai_model: str) -> RuntimeConfiguration:
        """Encrypt and persist an API key alongside its non-secret model name."""
        api_key = openai_api_key.strip()
        model = openai_model.strip()
        if not api_key:
            raise ValueError("OpenAI API key is required.")
        if not model:
            raise ValueError("OpenAI model is required.")
        encrypted_key = self._fernet.encrypt(api_key.encode("utf-8")).decode("ascii")
        with self._execution_store._lock, self._execution_store._connect() as connection:
            connection.executemany(
                """
                INSERT INTO runtime_configuration (
                    configuration_key, configuration_value, encrypted
                ) VALUES (?, ?, ?)
                ON CONFLICT(configuration_key) DO UPDATE SET
                    configuration_value = excluded.configuration_value,
                    encrypted = excluded.encrypted,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    (self._API_KEY_SETTING, encrypted_key, 1),
                    (self._MODEL_SETTING, model, 0),
                ],
            )
        self._logger.info("Encrypted runtime configuration saved")
        return RuntimeConfiguration(openai_api_key=SecretStr(api_key), openai_model=model)

    def load(self) -> RuntimeConfiguration | None:
        """Load and decrypt a complete saved runtime configuration."""
        with self._execution_store._lock, self._execution_store._connect() as connection:
            rows = connection.execute(
                """
                SELECT configuration_key, configuration_value, encrypted
                FROM runtime_configuration
                WHERE configuration_key IN (?, ?)
                """,
                (self._API_KEY_SETTING, self._MODEL_SETTING),
            ).fetchall()
        settings = {row[0]: (row[1], bool(row[2])) for row in rows}
        if self._API_KEY_SETTING not in settings or self._MODEL_SETTING not in settings:
            return None
        encrypted_key, is_encrypted = settings[self._API_KEY_SETTING]
        if not is_encrypted:
            raise ConfigurationDecryptionError("Persisted API-key configuration is not encrypted.")
        try:
            api_key = self._fernet.decrypt(encrypted_key.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise ConfigurationDecryptionError("Persisted API-key configuration cannot be decrypted.") from error
        model, _ = settings[self._MODEL_SETTING]
        return RuntimeConfiguration(openai_api_key=SecretStr(api_key), openai_model=model)

    def clear(self) -> None:
        """Delete persisted provider credentials and model configuration."""
        with self._execution_store._lock, self._execution_store._connect() as connection:
            connection.execute(
                "DELETE FROM runtime_configuration WHERE configuration_key IN (?, ?)",
                (self._API_KEY_SETTING, self._MODEL_SETTING),
            )
        self._logger.info("Persisted runtime configuration cleared")

    def _load_or_create_key(self) -> bytes:
        """Load an injected master key or create a restricted local key file."""
        injected_key = os.getenv("APP_CONFIGURATION_KEY", "").strip()
        if injected_key:
            return injected_key.encode("ascii")
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_descriptor = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = self._key_path.read_bytes().strip()
            if not key:
                raise ConfigurationDecryptionError("Configuration encryption key file is empty.")
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                self._logger.warning("Could not tighten configuration-key file permissions")
            return key
        key = Fernet.generate_key()
        with os.fdopen(file_descriptor, "wb") as key_file:
            key_file.write(key)
        return key


class SQLiteProjectBuilder:
    """Persist generated files as versioned SQLite artifacts without disk writes."""

    _FORBIDDEN_PARTS = frozenset({".git", ".venv", ".ssh", "secrets", "credentials"})
    _FAILED_STATUSES = frozenset({GenerationStatus.VALIDATION_FAILED, GenerationStatus.FAILED})

    def __init__(
        self,
        store: SQLiteExecutionStore,
        execution_id: str,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Bind artifact writes to one durable workflow execution."""
        self._store = store
        self._execution_id = execution_id
        self._logger = logger or logging.getLogger(__name__)

    def write(self, artifact: GeneratedArtifact, specification: FileSpecification) -> WriteResult:
        """Validate and transactionally store one generated artifact in SQLite."""
        self._validate_contract(artifact, specification)
        if self._is_forbidden(specification.path):
            return self._rejected(specification.path, "Target path is protected and cannot be stored.")
        if artifact.validation_status in self._FAILED_STATUSES:
            return self._rejected(specification.path, "Artifact validation status prevents persistence.")
        expected_hash = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
        if expected_hash != artifact.content_hash:
            return self._rejected(specification.path, "Artifact content hash does not match supplied content.")

        with self._store._lock, self._store._connect() as connection:
            existing = connection.execute(
                """
                SELECT artifact_json, content_hash FROM generated_artifacts
                WHERE execution_id = ? AND path = ?
                """,
                (self._execution_id, specification.path),
            ).fetchone()
            if existing and specification.overwrite_policy in {OverwritePolicy.NEVER, OverwritePolicy.CREATE_ONLY}:
                return WriteResult(
                    path=specification.path,
                    written=False,
                    action="preserved",
                    message="Existing database artifact is protected by overwrite policy.",
                )
            if existing and specification.overwrite_policy is OverwritePolicy.REQUIRE_APPROVAL:
                return WriteResult(
                    path=specification.path,
                    written=False,
                    approval_required=True,
                    action="approval_required",
                    message="Existing database artifact requires human approval before replacement.",
                )

            previous_json = existing[0] if existing else None
            previous_hash = existing[1] if existing else None
            cursor = connection.execute(
                """
                INSERT INTO artifact_write_operations (
                    execution_id, file_id, path, previous_artifact_json, new_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (self._execution_id, artifact.file_id, artifact.path, previous_json, artifact.content_hash),
            )
            operation_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO generated_artifacts (
                    execution_id, file_id, path, content_hash, artifact_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(execution_id, path) DO UPDATE SET
                    file_id = excluded.file_id,
                    content_hash = excluded.content_hash,
                    artifact_json = excluded.artifact_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self._execution_id,
                    artifact.file_id,
                    artifact.path,
                    artifact.content_hash,
                    artifact.model_dump_json(),
                ),
            )
        action = "created" if existing is None else "merged" if specification.overwrite_policy is OverwritePolicy.MERGE else "replaced"
        self._logger.info(
            "Generated artifact persisted to SQLite",
            extra={"execution_id": self._execution_id, "file_id": artifact.file_id, "action": action},
        )
        return WriteResult(
            path=specification.path,
            written=True,
            action=action,
            previous_hash=previous_hash,
            new_hash=artifact.content_hash,
            backup_path=f"sqlite://artifact_write_operations/{operation_id}" if existing else None,
            message="Artifact was stored transactionally in SQLite.",
        )

    def rollback_latest(self) -> RollbackResult:
        """Restore or remove the latest artifact written by this execution."""
        with self._store._lock, self._store._connect() as connection:
            operation = connection.execute(
                """
                SELECT operation_id, path, previous_artifact_json, new_hash
                FROM artifact_write_operations
                WHERE execution_id = ? AND rolled_back = 0
                ORDER BY operation_id DESC LIMIT 1
                """,
                (self._execution_id,),
            ).fetchone()
            if operation is None:
                return RollbackResult(rolled_back=False, action="no_operation", message="No SQLite artifact write is available to roll back.")
            operation_id, path, previous_json, new_hash = operation
            current = connection.execute(
                "SELECT content_hash FROM generated_artifacts WHERE execution_id = ? AND path = ?",
                (self._execution_id, path),
            ).fetchone()
            if current is None or current[0] != new_hash:
                return RollbackResult(
                    rolled_back=False,
                    path=path,
                    action="rejected",
                    message="Current database artifact changed after generation and was not modified.",
                )
            if previous_json:
                restored = GeneratedArtifact.model_validate_json(previous_json)
                connection.execute(
                    """
                    UPDATE generated_artifacts SET file_id = ?, content_hash = ?,
                        artifact_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE execution_id = ? AND path = ?
                    """,
                    (restored.file_id, restored.content_hash, previous_json, self._execution_id, path),
                )
                action = "restored"
                restored_hash = restored.content_hash
            else:
                connection.execute(
                    "DELETE FROM generated_artifacts WHERE execution_id = ? AND path = ?",
                    (self._execution_id, path),
                )
                action = "removed"
                restored_hash = None
            connection.execute(
                "UPDATE artifact_write_operations SET rolled_back = 1 WHERE operation_id = ?",
                (operation_id,),
            )
        return RollbackResult(
            rolled_back=True,
            path=path,
            action=action,
            restored_hash=restored_hash,
            message="Latest SQLite artifact write was rolled back.",
        )

    @staticmethod
    def _validate_contract(artifact: GeneratedArtifact, specification: FileSpecification) -> None:
        """Validate artifact identity against its approved specification."""
        if artifact.file_id != specification.id or artifact.path != specification.path:
            raise ValueError("Generated artifact identity does not match its approved specification.")

    @classmethod
    def _is_forbidden(cls, path_value: str) -> bool:
        """Reject traversal, environment, secret, credential, and SSH paths."""
        normalized_path = path_value.replace("\\", "/")
        path = PurePosixPath(normalized_path)
        if path.is_absolute() or ".." in path.parts:
            return True
        for part in path.parts:
            normalized = part.casefold()
            if normalized in cls._FORBIDDEN_PARTS or normalized.startswith(".env"):
                return True
            if "secret" in normalized or "credential" in normalized:
                return True
        return False

    @staticmethod
    def _rejected(path: str, message: str) -> WriteResult:
        """Return a consistent non-writing result."""
        return WriteResult(path=path, written=False, action="rejected", message=message)
