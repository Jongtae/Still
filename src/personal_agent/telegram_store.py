"""Telegram's own tables behind one surface-owned store interface (#21 slice 3).

The Telegram surface (``telegram_surface``) keeps five tables in the owner's
one SQLite file (``QuickStore.path``): ``telegram_task_cards``,
``telegram_photo_attachments``, ``telegram_photo_albums``,
``telegram_notifications`` and ``telegram_context_choices``.  This module is the
interface to them: photo handles, context-choice buttons, task cards,
notifications and photo albums.

There is no schema or data migration.  The tables, their triggers and their
``CREATE TABLE`` statements stay where they were (``QuickStore``'s bootstrap
script) and this module opens connections through the same ``QuickStore.db()``,
so transactions, locking and durability are unchanged.  A method that takes
``db`` runs inside the caller's transaction, exactly as the inline statement
it replaced did.  ``QuickStore`` keeps a forwarding method of the same name
for each moved method, so its callers and test patch targets are unchanged.

The Telegram surface and ``AgentService`` reach these tables only through this
interface.  A few statements still touch them from elsewhere, each because it
belongs to another owner's transaction or join: restart recovery
(``QuickStore.recover`` and ``AgentService.recover_interrupted_work``, joined
with ``jobs``), the held Memory notice written with the Memory row
(``QuickStore.save_memory``), preparation delivery
(``preparations``), context-observation photo cleanup
(``context_observations``) and the terminal-status photo trigger in the schema.
``telegram_turns`` belongs to ``telegram_presence.TelegramTurnAddressing``.
"""
import json
import secrets
import time
import uuid

#: The tables this interface owns, in the owner's one SQLite file.
TABLES = ('telegram_task_cards', 'telegram_photo_attachments', 'telegram_photo_albums',
          'telegram_notifications', 'telegram_context_choices')


class TelegramStore:
    """Telegram table access for one ``QuickStore`` (same file, same connections)."""

    def __init__(self, store):
        self.store = store

    def held_notifications(self, kind):
        """Held notices of ``kind`` with their Work's delivery state, oldest first (#918 review: release sweep)."""
        store=self.store
        with store.db() as db:
            rows=db.execute('SELECT n.*, j.delivery AS job_delivery FROM telegram_notifications n LEFT JOIN jobs j ON j.id=n.job_id '
                            'WHERE n.kind=? AND n.state=? ORDER BY n.created',(kind,store.NOTICE_HELD)).fetchall()
            return [dict(row) for row in rows]

    def attach_telegram_photo(self, job_id, file_id, db=None):
        """Keep Telegram fetch handles until this Work attempts its image read(s)."""
        store=self.store
        if not isinstance(job_id, str) or not job_id or not isinstance(file_id, str) or not 1 <= len(file_id) <= 512:
            raise ValueError('Telegram 사진 정보를 확인하세요.')
        if db is None:
            with store.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return store.attach_telegram_photo(job_id,file_id,conn)
        row=db.execute('SELECT file_id FROM telegram_photo_attachments WHERE job_id=?',(job_id,)).fetchone()
        try:values=json.loads(row['file_id']) if row and row['file_id'].startswith('[') else ([row['file_id']] if row else [])
        except (TypeError,ValueError):values=[row['file_id']] if row else []
        values=[value for value in values if isinstance(value,str) and value]
        if file_id not in values:values.append(file_id)
        if len(values)>10:raise ValueError('Telegram 사진은 한 번에 최대 10장까지 처리할 수 있습니다.')
        db.execute('INSERT INTO telegram_photo_attachments VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET file_id=excluded.file_id,created=excluded.created',
                   (job_id,json.dumps(values),time.time()))

    def telegram_photo_file_id(self, job_id):
        store=self.store
        values=store.telegram_photo_file_ids(job_id)
        return values[0] if values else None

    def telegram_photo_file_ids(self, job_id):
        """Return all Telegram photo handles for this Work, including legacy single-photo rows."""
        store=self.store
        with store.db() as db:
            row = db.execute('SELECT file_id FROM telegram_photo_attachments WHERE job_id=?', (job_id,)).fetchone()
        if not row:return []
        value=row['file_id']
        try:values=json.loads(value) if value.startswith('[') else [value]
        except (TypeError,ValueError):values=[value]
        return [item for item in values if isinstance(item,str) and item][:10] if isinstance(values,list) else [value]

    def remove_telegram_photo(self, job_id):
        store=self.store
        with store.db() as db:
            db.execute('DELETE FROM telegram_photo_attachments WHERE job_id=?', (job_id,))

    def transfer_telegram_photo(self, source_job_id, target_job_id, db=None):
        """Move an owner-supplied Telegram photo handle to its one continuation Work."""
        store=self.store
        if not isinstance(source_job_id,str) or not isinstance(target_job_id,str) or source_job_id==target_job_id:
            return False
        if db is None:
            with store.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return store.transfer_telegram_photo(source_job_id,target_job_id,conn)
        row=db.execute('SELECT file_id,created FROM telegram_photo_attachments WHERE job_id=?',(source_job_id,)).fetchone()
        if not row:return False
        db.execute('INSERT INTO telegram_photo_attachments(job_id,file_id,created) VALUES (?,?,?) '
                   'ON CONFLICT(job_id) DO UPDATE SET file_id=excluded.file_id,created=excluded.created',
                   (target_job_id,row['file_id'],row['created']))
        store.mark_telegram_photo_attached(target_job_id,db=db)
        db.execute('DELETE FROM telegram_photo_attachments WHERE job_id=?',(source_job_id,))
        return True

    def create_telegram_context_choice(self, job_id, event_id, chat_id, generation):
        store=self.store
        token=secrets.token_urlsafe(9)
        with store.db() as db:
            db.execute('INSERT INTO telegram_context_choices VALUES (?,?,?,?,?,?,?,?)',(token,job_id,event_id,chat_id,generation,None,'offered',time.time()))
        return token

    def save_telegram_context_choice_message(self, tokens, message_id):
        store=self.store
        if not isinstance(message_id,int):return
        with store.db() as db:
            marks=','.join('?' for _ in tokens)
            if marks:db.execute(f"UPDATE telegram_context_choices SET message_id=? WHERE token IN ({marks})",(message_id,*tokens))

    def telegram_context_choice(self, token):
        store=self.store
        with store.db() as db:
            row=db.execute('SELECT * FROM telegram_context_choices WHERE token=?',(token,)).fetchone()
        return dict(row) if row else None

    def telegram_context_choice_for_job(self, job_id):
        store=self.store
        with store.db() as db:
            row=db.execute("SELECT * FROM telegram_context_choices WHERE job_id=? AND state='offered' ORDER BY created LIMIT 1",(job_id,)).fetchone()
        return dict(row) if row else None

    def select_telegram_context_choice(self, token):
        store=self.store
        with store.db() as db:
            db.execute("UPDATE telegram_context_choices SET state='selected' WHERE token=? AND state='offered'",(token,))
            row=db.execute('SELECT * FROM telegram_context_choices WHERE token=?',(token,)).fetchone()
        return dict(row) if row else None

    def task_notifications(self, job_id):
        store=self.store
        with store.db() as db:
            return [dict(row) for row in db.execute('SELECT kind,state,created FROM telegram_notifications WHERE job_id=? ORDER BY created',(job_id,))]

    def task_card(self, job_id):
        store=self.store
        with store.db() as db:
            row=db.execute('SELECT * FROM telegram_task_cards WHERE job_id=?',(job_id,)).fetchone()
            return dict(row) if row else None

    def save_task_card(self, job_id, chat_id, message_id, state):
        store=self.store
        with store.db() as db:
            db.execute('INSERT INTO telegram_task_cards VALUES (?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET chat_id=excluded.chat_id,message_id=excluded.message_id,state=excluded.state,created=CASE WHEN telegram_task_cards.message_id=-1 THEN excluded.created ELSE telegram_task_cards.created END',
                       (job_id,chat_id,message_id,state,time.time()))

    def mark_task_card_deleting(self, job_id, message_id):
        """Persist deletion intent before the Telegram side effect can race restart."""
        store=self.store
        with store.db() as db:
            return db.execute("UPDATE telegram_task_cards SET state='deleting' WHERE job_id=? AND message_id=?",
                              (job_id,message_id)).rowcount == 1

    def delete_task_card(self, job_id, message_id):
        """Forget only the Telegram card whose deletion was confirmed."""
        store=self.store
        with store.db() as db:
            return db.execute('DELETE FROM telegram_task_cards WHERE job_id=? AND message_id=?',
                              (job_id,message_id)).rowcount == 1

    def notification_of(self, job_id, kind):
        """The one notification of ``kind`` on ``job_id`` (``UNIQUE(job_id, kind)``), or None."""
        store=self.store
        with store.db() as db:
            row=db.execute('SELECT * FROM telegram_notifications WHERE job_id=? AND kind=?',(job_id,kind)).fetchone()
            return dict(row) if row else None

    def rebind_notification(self, job_id, kind, fingerprint):
        """Point a Work's one notification of ``kind`` at a new binding and send it again (#1468).

        ``UNIQUE(job_id, kind)`` keeps one row per Work, so a replaced request would otherwise
        leave the row bound to the old fingerprint, cancelled as stale and never sent.  A row
        being sent right now is left alone.  Returns whether a row was rebound.
        """
        store=self.store
        with store.db() as db:
            return db.execute("UPDATE telegram_notifications SET fingerprint=?,state='queued',message_id=NULL,sent_at=NULL "
                              "WHERE job_id=? AND kind=? AND state!='sending'", (fingerprint, job_id, kind)).rowcount == 1

    def notification(self, notification_id):
        store=self.store
        with store.db() as db:
            row=db.execute('SELECT * FROM telegram_notifications WHERE id=?',(notification_id,)).fetchone()
            return dict(row) if row else None

    def queue_notification(self, job_id, chat_id, generation, kind, fingerprint=None):
        store=self.store
        notification_id=str(uuid.uuid4())
        with store.db() as db:
            db.execute('INSERT OR IGNORE INTO telegram_notifications(id,job_id,chat_id,generation,kind,fingerprint,state,message_id,created) VALUES (?,?,?,?,?,?,?,?,?)',
                       (notification_id,job_id,chat_id,generation,kind,fingerprint,'queued',None,time.time()))
            row=db.execute('SELECT * FROM telegram_notifications WHERE job_id=? AND kind=?',(job_id,kind)).fetchone()
            return dict(row)

    def next_notification(self):
        store=self.store
        with store.db() as db:
            row=db.execute("SELECT * FROM telegram_notifications WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            return dict(row) if row else None

    def update_notification(self, notification_id, state, message_id=None, fingerprint=None, delivered=False):
        """``delivered``: this call records a completed Telegram send, so its time becomes ``sent_at`` (#188).

        Every other call - a memory notice kept ``sent`` after a partial decision, withdrawal,
        join or edit - leaves ``sent_at`` as the real delivery recorded it.
        """
        store=self.store
        with store.db() as db:
            if fingerprint is not None:
                # #818: a memory prompt's binding is recorded with what the send showed.
                db.execute('UPDATE telegram_notifications SET fingerprint=? WHERE id=?',(fingerprint,notification_id))
            if message_id is None:
                db.execute('UPDATE telegram_notifications SET state=? WHERE id=?',(state,notification_id))
            else:
                db.execute('UPDATE telegram_notifications SET state=?,message_id=? WHERE id=?',(state,message_id,notification_id))
            if delivered:
                # #188: recorded once the send returned; an owner act dated before it never answers it.
                db.execute('UPDATE telegram_notifications SET sent_at=? WHERE id=?',(time.time(),notification_id))

    # -- statements the Telegram surface runs, some inside its own transaction (``db``) --

    @staticmethod
    def card_row(db, job_id):
        """The task card row of ``job_id`` in the caller's transaction, or None."""
        return db.execute('SELECT * FROM telegram_task_cards WHERE job_id=?',(job_id,)).fetchone()

    @staticmethod
    def confirm_unknown_card(db, job_id, message_id):
        """Bind a card whose send outcome was unknown (``message_id=-2``) to the message the owner tapped."""
        db.execute("UPDATE telegram_task_cards SET message_id=?,state='queued',created=? WHERE job_id=? AND message_id=-2",
                   (message_id,time.time(),job_id))

    def mark_login_reopened(self, notification_id):
        """Only while still the expired prompt: the work loop may already have re-armed it."""
        with self.store.db() as db:
            db.execute("UPDATE telegram_notifications SET state='browser_login_reopened' "
                       "WHERE id=? AND state='browser_login_expired'",(notification_id,))

    @staticmethod
    def album(db, generation, chat_id, media_group_id):
        """The open photo album of this chat and media group, in the caller's transaction."""
        return db.execute('SELECT * FROM telegram_photo_albums WHERE generation=? AND chat_id=? AND media_group_id=?',
                          (generation,chat_id,media_group_id)).fetchone()

    @staticmethod
    def add_album(db, job_id, generation, chat_id, media_group_id, caption):
        db.execute('INSERT INTO telegram_photo_albums(job_id,generation,chat_id,media_group_id,last_received,caption) VALUES (?,?,?,?,?,?)',
                   (job_id,generation,chat_id,media_group_id,time.time(),caption))

    @staticmethod
    def album_caption(db, job_id):
        return db.execute('SELECT caption FROM telegram_photo_albums WHERE job_id=?',(job_id,)).fetchone()

    @staticmethod
    def touch_album(db, job_id, caption):
        db.execute('UPDATE telegram_photo_albums SET last_received=?,caption=? WHERE job_id=?',
                   (time.time(),caption,job_id))

    def settle_albums(self, cutoff):
        """Release album Works whose last photo arrived at or before ``cutoff``."""
        with self.store.db() as db:
            db.execute('DELETE FROM telegram_photo_albums WHERE last_received<=?',(cutoff,))

    @staticmethod
    def notice_on_message(db, chat_id, message_id, kinds):
        """The newest notification of one of ``kinds`` sent as ``message_id`` in this chat, or None."""
        return db.execute('SELECT * FROM telegram_notifications WHERE chat_id=? AND message_id=? AND kind IN (%s) '
                          'ORDER BY created DESC LIMIT 1'%','.join('?'*len(kinds)),
                          (chat_id,message_id,*kinds)).fetchone()

    # -- statements the service runs on notifications, photos and cards --

    def notifications_in_state(self, kinds, state):
        """Every notification of one of ``kinds`` in ``state``, as dicts."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM telegram_notifications WHERE kind IN (%s) AND state=?'
                                                    %','.join('?'*len(kinds)),(*kinds,state))]

    def notifications_of_kinds(self, job_id, kinds):
        """This Work's notifications of ``kinds`` (one per kind), as dicts."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM telegram_notifications WHERE job_id=? AND kind IN (%s)'
                                                    %','.join('?'*len(kinds)),(job_id,*kinds))]

    def open_prompts(self, job_id, kind):
        """This Work's ``kind`` prompts still queued or sent: id, state, chat and message."""
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,state,chat_id,message_id FROM telegram_notifications WHERE job_id=? AND kind=? AND state IN ('queued','sent')",
                (job_id,kind))]

    @staticmethod
    def sent_asks(db, kinds, except_job_id):
        """Sent prompts of ``kinds`` from other Works, oldest first, in the caller's transaction."""
        return [dict(row) for row in db.execute(
            "SELECT job_id,kind,fingerprint,created FROM telegram_notifications WHERE kind IN (%s) AND state='sent' AND job_id IS NOT ? "
            "ORDER BY created"%','.join('?'*len(kinds)),(*kinds,except_job_id)).fetchall()]

    def sent_of_kinds(self, chat_id, kinds, limit=10, message_id=None):
        """The sent notifications of ``kinds`` in this chat, newest first, at most ``limit`` (#172).

        ``message_id``: only the one(s) sent as that Telegram message (#188).
        """
        where,extra=('AND message_id=? ',(message_id,)) if message_id is not None else ('',())
        with self.store.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM telegram_notifications WHERE chat_id=? AND state='sent' AND kind IN (%s) %s"
                "ORDER BY created DESC LIMIT ?"%(','.join('?'*len(kinds)),where),(chat_id,*kinds,*extra,int(limit)))]

    def rearm(self, job_id, kind, fingerprint):
        """Re-queue this Work's ``kind`` prompt for a new binding unless it is already queued or sending."""
        with self.store.db() as db:
            db.execute("UPDATE telegram_notifications SET fingerprint=?,state='queued',message_id=NULL,sent_at=NULL,created=? "
                       "WHERE job_id=? AND kind=? AND fingerprint!=? AND state NOT IN ('queued','sending')",
                       (fingerprint,time.time(),job_id,kind,fingerprint))

    def expire_reopened_login(self, job_id, nonce):
        """Mark a reopened login prompt expired for ``nonce``; returns the prompt row as it was, or None."""
        with self.store.db() as db:
            note=db.execute("SELECT * FROM telegram_notifications WHERE job_id=? AND kind='browser_login_needed' "
                            "AND state='browser_login_reopened'",(job_id,)).fetchone()
            if note:db.execute("UPDATE telegram_notifications SET state='browser_login_expired',fingerprint=? WHERE id=?",
                               (nonce,note['id']))
            return note

    @staticmethod
    def drop_photo(db, job_id):
        """Forget this Work's photo handles, in the caller's transaction."""
        db.execute('DELETE FROM telegram_photo_attachments WHERE job_id=?',(job_id,))

    @staticmethod
    def album_open(db, job_id):
        """Whether this Work is still a photo album collecting updates, in the caller's transaction."""
        return db.execute('SELECT 1 FROM telegram_photo_albums WHERE job_id=?',(job_id,)).fetchone()
