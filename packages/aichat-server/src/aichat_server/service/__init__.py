"""Service layer: all chat rules live here. Routes stay thin."""

from dataclasses import dataclass

from ..config import Settings
from ..notify import Notifier
from ..store.base import Store
from .access import Access
from .context import Ctx
from .identity import IdentityService
from .lifetime import Lifetime
from .messaging import MessagingService
from .ops import OpsService
from .present import Presenter
from .reading import ReadingService
from .rooms import RoomService


@dataclass
class Services:
    ctx: Ctx
    identity: IdentityService
    rooms: RoomService
    messaging: MessagingService
    reading: ReadingService
    ops: OpsService


def build_services(store: Store, notifier: Notifier, settings: Settings) -> Services:
    ctx = Ctx(store=store, notifier=notifier, settings=settings)
    access = Access(ctx)
    presenter = Presenter(store)
    reading = ReadingService(ctx, access, presenter)
    identity = IdentityService(ctx, access, Lifetime(ctx, reading))
    return Services(
        ctx=ctx,
        identity=identity,
        rooms=RoomService(ctx, identity),
        messaging=MessagingService(ctx, access, presenter, reading),
        reading=reading,
        ops=OpsService(ctx, presenter),
    )
