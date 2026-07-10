from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
def dashboard_metrics(db: Session = Depends(get_db)):
    return AnalyticsService(db).get_dashboard_metrics()


@router.get("/messages")
def message_metrics(days: int = 7, db: Session = Depends(get_db)):
    return AnalyticsService(db).get_message_metrics(days=days)


@router.get("/tickets")
def ticket_metrics(db: Session = Depends(get_db)):
    return AnalyticsService(db).get_ticket_metrics()


@router.get("/agents")
def agent_performance(days: int = 7, db: Session = Depends(get_db)):
    return AnalyticsService(db).get_agent_performance(days=days)
