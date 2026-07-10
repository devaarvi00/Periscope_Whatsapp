from pydantic import BaseModel


class DashboardMetrics(BaseModel):
    total_chats: int
    unread_chats: int
    flagged_chats: int
    open_tickets: int
    in_progress_tickets: int
    online_agents: int


class MessageMetrics(BaseModel):
    active_chats: int
    outgoing_messages: int
    incoming_messages: int
    flagged_messages: int
    median_first_response_minutes: float


class TicketMetrics(BaseModel):
    open: int
    in_progress: int
    resolved: int
    unassigned: int
    avg_resolution_hours: float
    sla_breached: int


class AgentPerformance(BaseModel):
    agent_id: int
    agent_name: str
    messages_sent: int
    open_tickets: int
    response_time_minutes: float
    chats_assigned: int
