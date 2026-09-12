"""blank the bracketed labels stored as the text of media messages

Media messages used to be stored with a label as their text ("[Mijoz rasm
yubordi]", "[Template yuborildi]"). The dashboard shows the media itself, and
the model read — and quoted back to customers — those labels. New messages are
stored without them; this clears them from existing rows. Voice notes keep
their placeholder: it's the "not transcribed yet" state.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE messages
           SET content = ''
         WHERE attachment_type IS NOT NULL
           AND attachment_type <> 'audio'
           AND (
                content IN ('[Mijoz rasm yubordi]', '[Rasm yuborildi]', '[Video yuborildi]',
                            '[Reels / Story ulashildi]')
                OR content ~ '^\[[A-Za-z_]+ yuborildi\]$'
           )
        """
    )


def downgrade() -> None:
    # The labels carried nothing the attachment_type doesn't.
    pass
