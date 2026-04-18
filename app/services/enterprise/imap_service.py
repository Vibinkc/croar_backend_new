import email
import imaplib
import re
from email.header import decode_header
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.enterprise.communication import EmailDirection, EmailLog
from app.services.enterprise.hiring_agent import hiring_agent_service

_settings = get_settings()


class ImapService:
    def __init__(self) -> None:
        self.host = str(_settings.imap_address)
        self.port = int(cast("Any", _settings.imap_port))
        self.user = str(_settings.imap_username)
        self.password = str(_settings.imap_password)

    def _decode_mime_words(self, s: str | None) -> str:
        if not s:
            return ""
        return "".join(
            word.decode(encoding or "utf-8") if isinstance(word, bytes) else word
            for word, encoding in decode_header(s)
        )

    async def fetch_and_sync_emails(
        self, session: AsyncSession, background_tasks: object
    ) -> dict[str, object]:
        """
        Connects to IMAP, fetches recent emails, and syncs them to local DB.
        """
        if not self.user or not self.password:
            return {"status": "IMAP credentials not configured"}

        try:
            mail = imaplib.IMAP4_SSL(self.host, self.port)
            mail.login(self.user, self.password)
            mail.select("inbox")

            status, response = mail.search(None, "ALL")
            if status != "OK":
                return {"status": "Failed to search inbox"}

            email_ids = cast("list[bytes]", response[0].split())
            recent_ids = email_ids[-50:] if len(email_ids) > 50 else email_ids
            sync_count = 0

            for e_id in reversed(recent_ids):
                try:
                    status, data = mail.fetch(e_id.decode(), "(RFC822)")
                    if status != "OK" or not data:
                        continue

                    raw_email_part = data[0]
                    if not isinstance(raw_email_part, tuple):
                        continue
                    raw_email = raw_email_part[1]
                    msg = email.message_from_bytes(raw_email)

                    message_id = msg.get("Message-ID")

                    if message_id:
                        stmt = select(EmailLog).where(EmailLog.message_id == str(message_id))
                        res = await session.execute(stmt)
                        if res.scalar_one_or_none():
                            continue

                    subject = self._decode_mime_words(msg.get("Subject"))
                    sender = self._decode_mime_words(msg.get("From"))

                    match = re.search(r"<(.*)>", sender)
                    sender_email = match.group(1) if match else sender

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                payload = part.get_payload(decode=True)
                                if isinstance(payload, bytes):
                                    body = payload.decode("utf-8", errors="ignore")
                                break
                            if part.get_content_type() == "text/html":
                                payload = part.get_payload(decode=True)
                                if isinstance(payload, bytes):
                                    body = payload.decode("utf-8", errors="ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body = payload.decode("utf-8", errors="ignore")

                    new_email = EmailLog(
                        direction=EmailDirection.INBOUND,
                        sender_email=sender_email,
                        recipient_email=self.user,
                        subject=subject,
                        body=body,
                        status="received",
                        is_read=False,
                        message_id=str(message_id) if message_id else None,
                    )
                    session.add(new_email)
                    await session.flush()

                    await hiring_agent_service.process_inbound_email(
                        from_email=sender_email,
                        subject=subject,
                        body=body,
                        session=session,
                        background_tasks=background_tasks,
                    )

                    sync_count += 1
                    if sync_count >= 20:
                        break
                except Exception as e:
                    print(f"Error processing email {e_id.decode()}: {e}")
                    continue

            await session.commit()
            mail.logout()

            return {"status": "success", "synced_count": sync_count}

        except Exception as e:
            print(f"IMAP Error: {e}")
            return {"status": "error", "message": str(e)}


imap_service = ImapService()
