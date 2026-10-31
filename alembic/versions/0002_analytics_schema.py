"""Add analytics lifecycle fields, indexes and monthly snapshots.

The first migration builds the schema from the current SQLAlchemy metadata, so
this revision checks for existing objects. That makes ``alembic upgrade head``
safe both for an existing installation and for a newly created database.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_analytics"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(item["name"] == column for item in inspector.get_columns(table))


def _has_table(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def _has_index(inspector, table: str, index: str) -> bool:
    return any(item["name"] == index for item in inspector.get_indexes(table))


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if _has_table(inspector, "subscriptions"):
        missing = []
        if not _has_column(inspector, "subscriptions", "cancellation_requested_at"):
            missing.append(sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column(inspector, "subscriptions", "cancellation_reason"):
            missing.append(sa.Column("cancellation_reason", sa.String(length=40), nullable=True))
        if not _has_column(inspector, "subscriptions", "closed_at"):
            missing.append(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column(inspector, "subscriptions", "renewed_from_id"):
            missing.append(sa.Column("renewed_from_id", sa.Integer(), nullable=True))
        if missing:
            with op.batch_alter_table("subscriptions") as batch:
                for column in missing:
                    batch.add_column(column)
                if not _has_column(inspector, "subscriptions", "renewed_from_id"):
                    batch.create_foreign_key(
                        "fk_subscriptions_renewed_from_id",
                        "subscriptions",
                        ["renewed_from_id"],
                        ["id"],
                    )
        inspector = inspect(bind)
        for table, index, columns in [
            ("subscriptions", "ix_subscriptions_started_at", ["started_at"]),
            ("subscriptions", "ix_subscriptions_closed_at", ["closed_at"]),
            ("subscriptions", "ix_subscriptions_cancellation_reason", ["cancellation_reason"]),
            ("subscriptions", "ix_subscriptions_renewed_from_id", ["renewed_from_id"]),
        ]:
            if not _has_index(inspector, table, index):
                op.create_index(index, table, columns)

    if _has_table(inspector, "invoices") and not _has_index(inspector, "invoices", "ix_invoices_created_at"):
        op.create_index("ix_invoices_created_at", "invoices", ["created_at"])
    if _has_table(inspector, "payments") and not _has_index(inspector, "payments", "ix_payments_created_at"):
        op.create_index("ix_payments_created_at", "payments", ["created_at"])

    if not _has_table(inspector, "analytics_snapshots"):
        op.create_table(
            "analytics_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("snapshot_month", sa.Date(), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="captured"),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.UniqueConstraint("snapshot_month", name="uq_analytics_snapshot_month"),
        )
    inspector = inspect(bind)
    if not _has_index(inspector, "analytics_snapshots", "ix_analytics_snapshots_snapshot_month"):
        op.create_index(
            "ix_analytics_snapshots_snapshot_month",
            "analytics_snapshots",
            ["snapshot_month"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if _has_table(inspector, "analytics_snapshots"):
        op.drop_table("analytics_snapshots")
    if _has_table(inspector, "subscriptions"):
        for index in [
            "ix_subscriptions_started_at",
            "ix_subscriptions_closed_at",
            "ix_subscriptions_cancellation_reason",
            "ix_subscriptions_renewed_from_id",
        ]:
            if _has_index(inspector, "subscriptions", index):
                op.drop_index(index, table_name="subscriptions")
        columns = {item["name"] for item in inspector.get_columns("subscriptions")}
        with op.batch_alter_table("subscriptions") as batch:
            if "renewed_from_id" in columns:
                batch.drop_constraint("fk_subscriptions_renewed_from_id", type_="foreignkey")
                batch.drop_column("renewed_from_id")
            for column in ["closed_at", "cancellation_reason", "cancellation_requested_at"]:
                if column in columns:
                    batch.drop_column(column)
    if _has_table(inspector, "invoices") and _has_index(inspector, "invoices", "ix_invoices_created_at"):
        op.drop_index("ix_invoices_created_at", table_name="invoices")
    if _has_table(inspector, "payments") and _has_index(inspector, "payments", "ix_payments_created_at"):
        op.drop_index("ix_payments_created_at", table_name="payments")
