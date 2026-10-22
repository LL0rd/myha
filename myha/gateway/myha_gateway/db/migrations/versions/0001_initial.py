"""initial

Revision ID: 0001
Revises:
Create Date: 2026-10-22 16:56:59.080808
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myha_gateway.db.types import UtcDateTime

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", UtcDateTime(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("effective_principal", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("normalized_params", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("aggregate_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f("ix_audit_event_ts"), "audit_event", ["ts"])

    op.create_table(
        "fence",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=True),
        sa.Column("resource_ids", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("parameters", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fence")),
    )
    op.create_table(
        "group",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group")),
    )
    op.create_table(
        "instance",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("short_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("public_origin", sa.String(), nullable=True),
        sa.Column("tls_mode", sa.String(), nullable=True),
        sa.Column("tls_cert_path", sa.String(), nullable=True),
        sa.Column("tls_key_path", sa.String(), nullable=True),
        sa.Column("proxy_cidrs", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("trust_pins", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("security_generation", sa.Integer(), nullable=False),
        sa.Column("setup_state", sa.String(), nullable=False),
        sa.Column("operator_contact", sa.String(), nullable=True),
        sa.Column("session_limits", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("audit_retention_days", sa.Integer(), nullable=False),
        sa.Column("time_trusted", sa.Boolean(), nullable=False),
        sa.Column("last_seen_clock", UtcDateTime(), nullable=False),
        sa.Column("server_secret", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instance")),
    )
    op.create_table(
        "pairing_request",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("poll_token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("approved_session_id", sa.String(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pairing_request")),
        sa.UniqueConstraint("poll_token_hash", name=op.f("uq_pairing_request_poll_token_hash")),
    )
    op.create_table(
        "role_template",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("draft", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("draft_based_on", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_template")),
    )
    op.create_table(
        "unit",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit")),
    )
    op.create_table(
        "assignment",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("role_template_id", sa.String(), nullable=False),
        sa.Column("role_version", sa.Integer(), nullable=False),
        sa.Column("unit_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_template_id"],
            ["role_template.id"],
            name=op.f("fk_assignment_role_template_id_role_template"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment")),
    )
    op.create_index("ix_assignment_subject", "assignment", ["subject_type", "subject_id"])

    op.create_table(
        "dashboard",
        sa.Column("unit_id", sa.String(), nullable=False),
        sa.Column("layout", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"], name=op.f("fk_dashboard_unit_id_unit")),
        sa.PrimaryKeyConstraint("unit_id", name=op.f("pk_dashboard")),
    )
    op.create_table(
        "resource_binding",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("unit_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("registry_entity_id", sa.String(), nullable=True),
        sa.Column("unique_id", sa.String(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("area_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("icon", sa.String(), nullable=True),
        sa.Column("confirmed_capabilities", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("capability_fingerprint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_resource_binding_unit_id_unit")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_binding")),
        sa.UniqueConstraint("entity_id", name=op.f("uq_resource_binding_entity_id")),
    )
    op.create_index(op.f("ix_resource_binding_unit_id"), "resource_binding", ["unit_id"])

    op.create_table(
        "role_version",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("role_template_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("grants", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("denies", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("published_at", UtcDateTime(), nullable=False),
        sa.Column("based_on_version", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["role_template_id"],
            ["role_template.id"],
            name=op.f("fk_role_version_role_template_id_role_template"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_version")),
        sa.UniqueConstraint(
            "role_template_id",
            "version_number",
            name=op.f("uq_role_version_role_template_id_version_number"),
        ),
    )
    op.create_table(
        "stay",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("unit_id", sa.String(), nullable=False),
        sa.Column("starts_at", UtcDateTime(), nullable=False),
        sa.Column("ends_at", UtcDateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("role_template_id", sa.String(), nullable=False),
        sa.Column("role_version", sa.Integer(), nullable=False),
        sa.Column("device_slots", sa.Integer(), nullable=False),
        sa.Column("checked_out_at", UtcDateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_template_id"],
            ["role_template.id"],
            name=op.f("fk_stay_role_template_id_role_template"),
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"], name=op.f("fk_stay_unit_id_unit")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stay")),
    )
    op.create_table(
        "invite",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("stay_id", sa.String(), nullable=True),
        sa.Column("device_slot", sa.Integer(), nullable=False),
        sa.Column("redeem_until", UtcDateTime(), nullable=False),
        sa.Column("consumed_at", UtcDateTime(), nullable=True),
        sa.Column("consumed_session_id", sa.String(), nullable=True),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["stay_id"], ["stay.id"], name=op.f("fk_invite_stay_id_stay")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite")),
        sa.UniqueConstraint("secret_hash", name=op.f("uq_invite_secret_hash")),
    )
    op.create_index(op.f("ix_invite_stay_id"), "invite", ["stay_id"])

    op.create_table(
        "principal",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=True),
        sa.Column("theme", sa.String(), nullable=True),
        sa.Column("stay_id", sa.String(), nullable=True),
        sa.Column("invite_id", sa.String(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["stay_id"], ["stay.id"], name=op.f("fk_principal_stay_id_stay")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_principal")),
    )
    op.create_table(
        "credential",
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("username_normalized", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("changed_at", UtcDateTime(), nullable=False),
        sa.Column("must_reset", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name=op.f("fk_credential_principal_id_principal")
        ),
        sa.PrimaryKeyConstraint("principal_id", name=op.f("pk_credential")),
        sa.UniqueConstraint("username_normalized", name=op.f("uq_credential_username_normalized")),
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("valid_from", UtcDateTime(), nullable=False),
        sa.Column("valid_to", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"], ["group.id"], name=op.f("fk_membership_group_id_group")
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name=op.f("fk_membership_principal_id_principal")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership")),
        sa.UniqueConstraint(
            "principal_id", "group_id", name=op.f("uq_membership_principal_id_group_id")
        ),
    )
    op.create_index(op.f("ix_membership_group_id"), "membership", ["group_id"])

    op.create_table(
        "session",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("family_id", sa.String(), nullable=True),
        sa.Column("principal_id", sa.String(), nullable=True),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("stay_id", sa.String(), nullable=True),
        sa.Column("unit_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("not_before", UtcDateTime(), nullable=True),
        sa.Column("not_after", UtcDateTime(), nullable=False),
        sa.Column("last_seen_at", UtcDateTime(), nullable=False),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(), nullable=True),
        sa.Column("csrf_secret", sa.String(), nullable=False),
        sa.Column("device_label", sa.String(), nullable=True),
        sa.Column("actor_admin_id", sa.String(), nullable=True),
        sa.Column("preview_target", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name=op.f("fk_session_principal_id_principal")
        ),
        sa.ForeignKeyConstraint(["stay_id"], ["stay.id"], name=op.f("fk_session_stay_id_stay")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_session_token_hash")),
    )
    op.create_index(op.f("ix_session_principal_id"), "session", ["principal_id"])
    op.create_index(op.f("ix_session_stay_id"), "session", ["stay_id"])

    op.create_table(
        "command",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("body_hash", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("normalized_params", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ha_context_id", sa.String(), nullable=True),
        sa.Column("expectation", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name=op.f("fk_command_session_id_session")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command")),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name=op.f("uq_command_session_id_idempotency_key")
        ),
    )
    op.create_table(
        "refresh_token",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("family_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("issued_at", UtcDateTime(), nullable=False),
        sa.Column("rotated_at", UtcDateTime(), nullable=True),
        sa.Column("used_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name=op.f("fk_refresh_token_session_id_session")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_token_token_hash")),
    )
    op.create_table(
        "ws_ticket",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ticket_hash", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("used_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name=op.f("fk_ws_ticket_session_id_session")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ws_ticket")),
        sa.UniqueConstraint("ticket_hash", name=op.f("uq_ws_ticket_ticket_hash")),
    )


def downgrade() -> None:
    op.drop_table("ws_ticket")
    op.drop_table("refresh_token")
    op.drop_table("command")
    op.drop_table("session")
    op.drop_table("membership")
    op.drop_table("credential")
    op.drop_table("principal")
    op.drop_table("invite")
    op.drop_table("stay")
    op.drop_table("role_version")
    op.drop_table("resource_binding")
    op.drop_table("dashboard")
    op.drop_table("assignment")
    op.drop_table("unit")
    op.drop_table("role_template")
    op.drop_table("pairing_request")
    op.drop_table("instance")
    op.drop_table("group")
    op.drop_table("fence")
    op.drop_table("audit_event")
