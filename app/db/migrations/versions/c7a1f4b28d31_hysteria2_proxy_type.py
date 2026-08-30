"""hysteria2 proxy type

Adds Hysteria2 to the values `proxies.type` accepts.

What that takes depends on the engine. MySQL stores the column as a native
ENUM and rejects a value the type does not list, so it needs an ALTER. SQLite
renders the same column as VARCHAR(11) with no CHECK constraint — SQLAlchemy
stops creating those by default — and "Hysteria2" is nine characters, so there
is nothing to change and nothing to rebuild. Rebuilding anyway would mean
copying the table, which is not worth doing to a table of live users for no
effect.

Revision ID: c7a1f4b28d31
Revises: b3d5f81c92ae
Create Date: 2026-08-30 15:10:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'c7a1f4b28d31'
down_revision = 'b3d5f81c92ae'
branch_labels = None
depends_on = None

# The column keeps the member names, not their values, which is what these are.
OLD_VALUES = ("VMess", "VLESS", "Trojan", "Shadowsocks")
NEW_VALUES = (*OLD_VALUES, "Hysteria2")


def _alter(values):
    op.alter_column(
        "proxies",
        "type",
        existing_type=sa.Enum(*OLD_VALUES, name="proxytypes"),
        type_=sa.Enum(*values, name="proxytypes"),
        existing_nullable=False,
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        _alter(NEW_VALUES)


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return

    # A row left behind would not fit the narrowed type. Its user keeps every
    # other proxy; only the hysteria2 one goes, which is what removing the
    # protocol means.
    op.execute(sa.text("DELETE FROM proxies WHERE type = 'Hysteria2'"))
    _alter(OLD_VALUES)
