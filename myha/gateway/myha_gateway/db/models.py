"""SQLAlchemy models for the gateway database (design §5).

Ids are product strings such as ``unit_1a2b3c4d5e6f``; timestamps are aware UTC datetimes;
structured fields are JSON. In-place changes to a JSON value are not tracked: assign a new
object. ``version`` columns on admin-editable tables carry optimistic locking: services
compare and increment them, the ORM never does it implicitly.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, MetaData, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from myha_gateway.db.types import UtcDateTime

# Named constraints let later SQLite batch migrations address them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
# none_as_null: Python None becomes SQL NULL, not the JSON text 'null'.
_JSON = JSON(none_as_null=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: UtcDateTime(),
        dict[str, Any]: _JSON,
        list[str]: _JSON,
        list[dict[str, Any]]: _JSON,
    }


class Instance(Base):
    """The single row describing this installation."""

    __tablename__ = "instance"

    id: Mapped[str] = mapped_column(primary_key=True)
    short_id: Mapped[str]  # 8 characters, part of the cookie name (E25)
    name: Mapped[str]
    public_origin: Mapped[str | None]
    tls_mode: Mapped[str | None]  # tls | proxy
    tls_cert_path: Mapped[str | None]
    tls_key_path: Mapped[str | None]
    proxy_cidrs: Mapped[list[str]]
    trust_pins: Mapped[list[dict[str, Any]]]
    timezone: Mapped[str]
    policy_version: Mapped[int]
    security_generation: Mapped[int]
    setup_state: Mapped[str]
    operator_contact: Mapped[str | None]
    session_limits: Mapped[dict[str, Any]]
    audit_retention_days: Mapped[int]
    time_trusted: Mapped[bool]
    last_seen_clock: Mapped[datetime]  # high-water mark of the wall clock (E26)
    server_secret: Mapped[str]  # HMAC key; never exported
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Unit(Base):
    __tablename__ = "unit"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    sort: Mapped[int]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class ResourceBinding(Base):
    """A Home Assistant entity exposed as a product resource; at most one per entity."""

    __tablename__ = "resource_binding"

    id: Mapped[str] = mapped_column(primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("unit.id"), index=True)
    kind: Mapped[str]  # light | switch | climate | cover | sensor
    entity_id: Mapped[str] = mapped_column(unique=True)
    registry_entity_id: Mapped[str | None]
    unique_id: Mapped[str | None]
    device_id: Mapped[str | None]
    area_id: Mapped[str | None]
    label: Mapped[str]
    icon: Mapped[str | None]
    confirmed_capabilities: Mapped[dict[str, Any]]
    capability_fingerprint: Mapped[str]
    status: Mapped[str]  # confirmed | needs_review | blocked
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Principal(Base):
    __tablename__ = "principal"

    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str]  # resident | guest | display
    status: Mapped[str]
    display_name: Mapped[str]
    locale: Mapped[str | None]
    theme: Mapped[str | None]
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("stay.id"), index=True)  # guests only
    invite_id: Mapped[str | None]  # guests only
    created_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Credential(Base):
    __tablename__ = "credential"

    principal_id: Mapped[str] = mapped_column(ForeignKey("principal.id"), primary_key=True)
    username_normalized: Mapped[str] = mapped_column(unique=True)  # NFKC + casefold
    password_hash: Mapped[str]  # Argon2id
    changed_at: Mapped[datetime]
    must_reset: Mapped[bool]


class Group(Base):
    __tablename__ = "group"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("principal_id", "group_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principal.id"))
    group_id: Mapped[str] = mapped_column(ForeignKey("group.id"), index=True)
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]


class RoleTemplate(Base):
    __tablename__ = "role_template"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None]
    draft: Mapped[dict[str, Any] | None]
    draft_based_on: Mapped[int | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class RoleVersion(Base):
    """A published, immutable version of a role template."""

    __tablename__ = "role_version"
    __table_args__ = (UniqueConstraint("role_template_id", "version_number"),)

    id: Mapped[str] = mapped_column(primary_key=True)  # "<role_template_id>@<version_number>"
    role_template_id: Mapped[str] = mapped_column(ForeignKey("role_template.id"))
    version_number: Mapped[int]
    grants: Mapped[list[dict[str, Any]]]
    denies: Mapped[list[dict[str, Any]]]
    published_at: Mapped[datetime]
    based_on_version: Mapped[int | None]


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (Index("ix_assignment_subject", "subject_type", "subject_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    subject_type: Mapped[str]  # principal | group
    subject_id: Mapped[str]
    role_template_id: Mapped[str] = mapped_column(ForeignKey("role_template.id"))
    role_version: Mapped[int]
    unit_ids: Mapped[list[str]]
    status: Mapped[str]  # active | disabled_pending_review
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Fence(Base):
    __tablename__ = "fence"

    id: Mapped[str] = mapped_column(primary_key=True)
    scope_type: Mapped[str]  # instance | unit | stay | principal | session
    scope_id: Mapped[str | None]
    resource_ids: Mapped[list[str] | None]
    action: Mapped[str]
    parameters: Mapped[dict[str, Any]]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Stay(Base):
    """A guest stay; times are UTC, ``starts_at`` inclusive and ``ends_at`` exclusive."""

    __tablename__ = "stay"

    id: Mapped[str] = mapped_column(primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("unit.id"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    status: Mapped[str]  # draft | confirmed | checked_out | cancelled
    external_ref: Mapped[str | None]
    role_template_id: Mapped[str] = mapped_column(ForeignKey("role_template.id"))
    role_version: Mapped[int]
    device_slots: Mapped[int]
    checked_out_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Invite(Base):
    """A one-time guest invitation; it carries no role, the stay does."""

    __tablename__ = "invite"

    id: Mapped[str] = mapped_column(primary_key=True)
    secret_hash: Mapped[str] = mapped_column(unique=True)
    purpose: Mapped[str]  # guest_join | guest_test
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("stay.id"), index=True)
    device_slot: Mapped[int]
    redeem_until: Mapped[datetime]
    consumed_at: Mapped[datetime | None]
    consumed_session_id: Mapped[str | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class Session(Base):
    """An access, preview, pairing or test session of a client."""

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    family_id: Mapped[str | None] = mapped_column(index=True)  # native refresh family
    principal_id: Mapped[str | None] = mapped_column(ForeignKey("principal.id"), index=True)
    audience: Mapped[str]  # browser | native
    purpose: Mapped[str]  # access | preview | pairing | test
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("stay.id"), index=True)
    unit_ids: Mapped[list[str] | None]  # scope cap: None none, a list exactly these units
    not_before: Mapped[datetime | None]
    not_after: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None]
    csrf_secret: Mapped[str]
    device_label: Mapped[str | None]
    actor_admin_id: Mapped[str | None]
    preview_target: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime]


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id: Mapped[str] = mapped_column(primary_key=True)
    family_id: Mapped[str] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    issued_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None]
    used_at: Mapped[datetime | None]  # reuse revokes the whole family


class WsTicket(Base):
    __tablename__ = "ws_ticket"

    id: Mapped[str] = mapped_column(primary_key=True)
    ticket_hash: Mapped[str] = mapped_column(unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class PairingRequest(Base):
    __tablename__ = "pairing_request"

    id: Mapped[str] = mapped_column(primary_key=True)
    code: Mapped[str]
    poll_token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    approved_session_id: Mapped[str | None]
    created_at: Mapped[datetime]


class Dashboard(Base):
    """Card order, sizes and theme default of one unit; the only source for them (E10)."""

    __tablename__ = "dashboard"

    unit_id: Mapped[str] = mapped_column(ForeignKey("unit.id"), primary_key=True)
    layout: Mapped[dict[str, Any]]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class Command(Base):
    __tablename__ = "command"
    # Also serves lookups by session_id alone.
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"))
    idempotency_key: Mapped[str]
    body_hash: Mapped[str]
    resource_id: Mapped[str]
    action: Mapped[str]
    normalized_params: Mapped[dict[str, Any]]
    policy_version: Mapped[int]
    status: Mapped[str]  # accepted | sent | confirmed | failed | unknown
    ha_context_id: Mapped[str | None]
    expectation: Mapped[dict[str, Any] | None]
    reason: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        # Finds the row that folds repeated denials (design §5).
        Index("ix_audit_event_fold", "operation", "reason_code", "anon_key", "ts"),
        # AUTOINCREMENT: ids never repeat, even after retention emptied the table.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    actor: Mapped[str]
    effective_principal: Mapped[str | None]  # NULL: an event without a principal
    session_id: Mapped[str | None]
    operation: Mapped[str]
    result: Mapped[str]
    reason_code: Mapped[str | None]
    resource_id: Mapped[str | None]
    policy_version: Mapped[int | None]
    normalized_params: Mapped[dict[str, Any] | None]
    ha_context_id: Mapped[str | None]  # spec §15.2
    # Fold key of an anonymous event (operation|reason_code|minute|client_ip), else NULL.
    anon_key: Mapped[str | None] = mapped_column(String(128))
    # Denials folded into this row (design §5).
    aggregate_count: Mapped[int] = mapped_column(default=1, server_default="1")
