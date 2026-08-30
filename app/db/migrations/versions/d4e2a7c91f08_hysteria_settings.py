"""hysteria settings

Moves the hysteria2 configuration out of .env and into a row an admin can edit
from the panel.

The row is seeded from whatever the .env variables currently say, so an
installation that had already configured hysteria comes up on exactly the
settings it was running on. A fresh installation gets the same defaults it
would have got from the unset variables.

Revision ID: d4e2a7c91f08
Revises: c7a1f4b28d31
Create Date: 2026-08-30 18:20:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'd4e2a7c91f08'
down_revision = 'c7a1f4b28d31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'hysteria_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=253), nullable=True),
        sa.Column('obfs_password', sa.String(length=128), nullable=True),
        sa.Column('up_mbps', sa.Integer(), nullable=False),
        sa.Column('down_mbps', sa.Integer(), nullable=False),
        sa.Column('masquerade_url', sa.String(length=512), nullable=False),
        sa.Column('stats_port', sa.Integer(), nullable=False),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # Seeded from .env, read here rather than hardcoded: the point of this
    # migration is that nobody's running configuration changes because of it.
    from config import (HYSTERIA_DOMAIN, HYSTERIA_DOWN_MBPS, HYSTERIA_ENABLED,
                        HYSTERIA_MASQUERADE_URL, HYSTERIA_OBFS_PASSWORD,
                        HYSTERIA_PORT, HYSTERIA_STATS_PORT, HYSTERIA_UP_MBPS)

    op.execute(
        sa.text(
            "INSERT INTO hysteria_settings "
            "(id, enabled, port, domain, obfs_password, up_mbps, down_mbps, "
            " masquerade_url, stats_port, extra, updated_at) "
            "VALUES (1, :enabled, :port, :domain, :obfs, :up, :down, "
            "        :masquerade, :stats, NULL, NULL)"
        ).bindparams(
            enabled=bool(HYSTERIA_ENABLED),
            port=int(HYSTERIA_PORT),
            domain=HYSTERIA_DOMAIN or None,
            obfs=HYSTERIA_OBFS_PASSWORD or None,
            up=int(HYSTERIA_UP_MBPS),
            down=int(HYSTERIA_DOWN_MBPS),
            masquerade=HYSTERIA_MASQUERADE_URL,
            stats=int(HYSTERIA_STATS_PORT),
        )
    )


def downgrade() -> None:
    op.drop_table('hysteria_settings')
