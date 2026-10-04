from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import Db, SignedInUser
from insightforge.api.schemas import ShareCreated, ShareIn, ShareOut, ShareView
from insightforge.db.models import ShareLink
from insightforge.services import shares as service
from insightforge.services.shares import ShareError

router = APIRouter(tags=["share links"])


def _guard(error: ShareError) -> HTTPException:
    return HTTPException(error.status, str(error))


@router.get("/shares", response_model=list[ShareOut])
def list_links(db: Db, user: SignedInUser):
    return db.scalars(
        select(ShareLink).where(ShareLink.owner_id == user.id).order_by(ShareLink.created_at.desc())
    ).all()


@router.post("/shares", response_model=ShareCreated, status_code=201)
def create_link(body: ShareIn, db: Db, user: SignedInUser):
    """Create a read-only link. Anyone with it can see the dashboard or answer until it expires."""
    try:
        link, secret = service.create(db, user.id, body.kind, body.target_id, body.expires_in_days)
    except ShareError as error:
        raise _guard(error) from error
    return ShareCreated(**ShareOut.model_validate(link).model_dump(), secret=secret)


@router.delete("/shares/{link_id}", status_code=204)
def revoke_link(link_id: str, db: Db, user: SignedInUser):
    link = db.scalar(select(ShareLink).where(ShareLink.id == link_id, ShareLink.owner_id == user.id))
    if link is None:
        raise HTTPException(404, "Link not found")
    if link.revoked_at is None:
        link.revoked_at = datetime.now(UTC)
        db.commit()


@router.post("/public/shares/view")
def view_link(body: ShareView, db: Db):
    """The shared content, for anyone with the link. No sign-in; read-only.

    The secret travels in the body, not the URL, so it never appears in access logs.
    """
    try:
        return service.public_view(db, service.resolve(db, body.secret))
    except ShareError as error:
        raise _guard(error) from error
