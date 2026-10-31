"""Store the selected tax rate on each subscription for recurring invoices."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_subscription_tax_rate"
down_revision = "0002_analytics"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    if "subscriptions" in inspector.get_table_names() and not any(
        column["name"] == "tax_rate_percent"
        for column in inspector.get_columns("subscriptions")
    ):
        with op.batch_alter_table("subscriptions") as batch:
            batch.add_column(
                sa.Column(
                    "tax_rate_percent",
                    sa.Numeric(precision=5, scale=2),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade():
    inspector = inspect(op.get_bind())
    if "subscriptions" in inspector.get_table_names() and any(
        column["name"] == "tax_rate_percent"
        for column in inspector.get_columns("subscriptions")
    ):
        with op.batch_alter_table("subscriptions") as batch:
            batch.drop_column("tax_rate_percent")
