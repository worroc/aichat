"""Files stored on the hub (``aichat send --attach``).

A blob is uploaded first, then a message points to it with a ``blob`` ref.
A reader downloads it through that message, so a blob has the same
visibility as the messages that point to it. Blobs live until their
namespace is deleted; an uploaded blob that no message points to is
never shown to anyone else. Blobs have their own quota
(``ns_max_blob_bytes``), so full storage for files never blocks text.
"""

import hashlib
import uuid
from dataclasses import replace
from pathlib import PurePosixPath

from aichat_protocol import models as wire
from aichat_protocol.errors import ErrorCode

from ..domain import Blob, DomainError, Ref, invalid, not_found
from ..security import Principal
from .access import Access
from .context import Ctx, now
from .present import blob_out

NAME_MAX = 200


def clean_name(raw: str) -> str:
    """Only the last path part: a name never points outside the reader's folder."""
    name = PurePosixPath(raw.replace("\\", "/")).name.strip()[:NAME_MAX]
    return name if name not in {"", ".", ".."} else "file"


def upload_meta(name: str, data: bytes) -> wire.BlobOut:
    """What an upload creates, without the id: the body an Idempotency-Key is checked against."""
    return wire.BlobOut(
        id="", name=clean_name(name), size=len(data), sha256=hashlib.sha256(data).hexdigest()
    )


class BlobService:
    def __init__(self, ctx: Ctx, access: Access) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.access = access

    def check_size(self, size: int) -> None:
        limit = self.ctx.settings.max_attach_bytes
        if size > limit:
            raise DomainError(
                ErrorCode.PAYLOAD_TOO_LARGE, f"file is {size} bytes; the hub accepts {limit}"
            )

    async def upload(self, p: Principal, name: str, data: bytes) -> wire.BlobOut:
        await self.ctx.identity(p)
        self.check_size(len(data))
        await self._check_quota(p.ns, len(data))
        meta = upload_meta(name, data)
        blob = Blob(
            ns=p.ns, id=str(uuid.uuid4()), name=meta.name, size=meta.size, sha256=meta.sha256,
            uploader=p.name, created_at=now(), data=data,
        )
        stored = await self.store.blobs.add(p.ns, blob)
        await self.ctx.audit(p.ns, p.name, "blob.upload", stored.name, detail=stored.id)
        return blob_out(replace(stored, name=meta.name))

    async def _check_quota(self, ns: str, size: int) -> None:
        if await self.store.blobs.usage(ns) + size > self.ctx.settings.ns_max_blob_bytes:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "namespace file storage quota exceeded")

    async def resolve(self, ns: str, ref: Ref) -> Ref:
        """A blob ref from a client: it must exist; name, size and sha256 come from the hub."""
        if not ref.id:
            raise invalid("a blob ref needs an id (upload the file first)")
        blob = await self.store.blobs.get(ns, ref.id)
        if blob is None:
            raise not_found("blob")
        name = clean_name(ref.name) if ref.name else blob.name
        return Ref(type="blob", id=blob.id, name=name, size=blob.size, sha256=blob.sha256)

    async def download(self, p: Principal, message_id: str, blob_id: str) -> Blob:
        await self.ctx.identity(p)
        message, _, _ = await self.access.message(p, message_id)
        if blob_id not in {r.id for r in message.refs if r.is_blob}:
            raise not_found("blob")
        blob = await self.store.blobs.read(p.ns, blob_id)
        if blob is None:
            raise not_found("blob")
        return blob
