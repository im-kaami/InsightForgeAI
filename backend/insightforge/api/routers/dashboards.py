import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.schemas import DashboardIn, DashboardItemIn, DashboardItemOut, DashboardOut
from insightforge.db.models import Dashboard
from insightforge.db.session import SessionLocal
from insightforge.services import dashboards as service
from insightforge.services.dashboards import DashboardError

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _guard(error: DashboardError) -> HTTPException:
    return HTTPException(error.status, str(error))


def _out(db: Db, dashboard: Dashboard, with_items: bool = True) -> DashboardOut:
    items = service.items_of(db, dashboard)
    return DashboardOut(
        id=dashboard.id,
        name=dashboard.name,
        description=dashboard.description or "",
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
        items=[DashboardItemOut.model_validate(item) for item in items] if with_items else [],
        item_count=len(items),
    )


def _owned(db: Db, user: CurrentUser, dashboard_id: str) -> Dashboard:
    try:
        return service.owned_dashboard(db, user.id, dashboard_id)
    except DashboardError as error:
        raise _guard(error) from error


@router.get("", response_model=list[DashboardOut])
def list_dashboards(db: Db, user: CurrentUser):
    values = db.scalars(
        select(Dashboard).where(Dashboard.owner_id == user.id).order_by(Dashboard.updated_at.desc())
    ).all()
    return [_out(db, value, with_items=False) for value in values]


@router.post("", response_model=DashboardOut, status_code=201)
def create_dashboard(body: DashboardIn, db: Db, user: CurrentUser):
    dashboard = Dashboard(owner_id=user.id, name=body.name.strip(), description=body.description.strip())
    db.add(dashboard)
    db.commit()
    db.refresh(dashboard)
    return _out(db, dashboard)


@router.get("/{dashboard_id}", response_model=DashboardOut)
def get_dashboard(dashboard_id: str, db: Db, user: CurrentUser):
    return _out(db, _owned(db, user, dashboard_id))


@router.patch("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: str, body: DashboardIn, db: Db, user: CurrentUser):
    dashboard = _owned(db, user, dashboard_id)
    dashboard.name = body.name.strip()
    dashboard.description = body.description.strip()
    dashboard.updated_at = datetime.now(UTC)
    db.commit()
    return _out(db, dashboard)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, db: Db, user: CurrentUser):
    service.delete_dashboard(db, _owned(db, user, dashboard_id))


@router.post("/{dashboard_id}/items", response_model=DashboardItemOut, status_code=201)
async def add_item(dashboard_id: str, body: DashboardItemIn, db: Db, user: CurrentUser):
    dashboard = _owned(db, user, dashboard_id)
    if body.kind == "pinned":
        if body.run_id is None or body.position is None:
            raise HTTPException(422, "Pinning needs run_id and position")
        try:
            return service.pin(db, user.id, dashboard, body.run_id, body.position, body.title)
        except DashboardError as error:
            raise _guard(error) from error
    if (
        not body.dataset_id
        or (body.kind == "metric" and not body.metric)
        or (body.kind == "question" and not body.query_id)
    ):
        raise HTTPException(
            422, "A metric tile needs dataset_id and metric; a question tile dataset_id and query_id"
        )

    def create() -> str:
        session = SessionLocal()
        try:
            owned = service.owned_dashboard(session, user.id, dashboard.id)
            if body.kind == "metric":
                item = service.add_metric(
                    session,
                    user.id,
                    owned,
                    body.dataset_id,
                    body.metric,
                    body.days,
                    body.group_by,
                    body.title,
                )
            else:
                item = service.add_question(
                    session, user.id, owned, body.dataset_id, body.query_id, body.title
                )
            return item.id
        finally:
            session.close()

    try:
        item_id = await asyncio.to_thread(create)
    except DashboardError as error:
        raise _guard(error) from error
    db.expire_all()
    return service.owned_item(db, dashboard, item_id)


@router.post("/{dashboard_id}/refresh", response_model=DashboardOut)
async def refresh_dashboard(dashboard_id: str, db: Db, user: CurrentUser):
    """Re-run every metric and question tile on the current data (code only; pinned tiles stay)."""
    dashboard = _owned(db, user, dashboard_id)

    def refresh_all() -> None:
        session = SessionLocal()
        try:
            owned = service.owned_dashboard(session, user.id, dashboard.id)
            for item in service.items_of(session, owned):
                service.refresh(session, item)
        finally:
            session.close()

    await asyncio.to_thread(refresh_all)
    db.expire_all()
    return _out(db, dashboard)


@router.post("/{dashboard_id}/items/{item_id}/refresh", response_model=DashboardItemOut)
async def refresh_item(dashboard_id: str, item_id: str, db: Db, user: CurrentUser):
    dashboard = _owned(db, user, dashboard_id)
    try:
        service.owned_item(db, dashboard, item_id)
    except DashboardError as error:
        raise _guard(error) from error

    def run() -> None:
        session = SessionLocal()
        try:
            owned = service.owned_dashboard(session, user.id, dashboard.id)
            service.refresh(session, service.owned_item(session, owned, item_id))
        finally:
            session.close()

    await asyncio.to_thread(run)
    db.expire_all()
    return service.owned_item(db, dashboard, item_id)


@router.post("/{dashboard_id}/items/{item_id}/move", response_model=DashboardOut)
def move_item(dashboard_id: str, item_id: str, direction: int, db: Db, user: CurrentUser):
    dashboard = _owned(db, user, dashboard_id)
    if direction not in (-1, 1):
        raise HTTPException(422, "direction must be -1 (up) or 1 (down)")
    try:
        service.move(db, dashboard, service.owned_item(db, dashboard, item_id), direction)
    except DashboardError as error:
        raise _guard(error) from error
    return _out(db, dashboard)


@router.delete("/{dashboard_id}/items/{item_id}", status_code=204)
def delete_item(dashboard_id: str, item_id: str, db: Db, user: CurrentUser):
    dashboard = _owned(db, user, dashboard_id)
    try:
        item = service.owned_item(db, dashboard, item_id)
    except DashboardError as error:
        raise _guard(error) from error
    db.delete(item)
    db.commit()
