"""
Pydantic models for the Splitwise Omi plugin.
"""
from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


# Omi Chat Tool Models
class ChatToolRequest(BaseModel):
    """Base request model for Omi chat tools."""
    uid: str
    app_id: str
    tool_name: str


class CreateExpenseRequest(ChatToolRequest):
    """Request model for creating an expense."""
    amount: str  # e.g. "25.00" or "25"
    description: str = "Expense"
    date: Optional[str] = None  # e.g. "2026-01-20", "today", "yesterday"
    person: Optional[str] = None  # Single person name
    people: Optional[List[str]] = None  # Multiple person names
    group: Optional[str] = None  # Group name (fuzzy matched)
    currency_code: Optional[str] = None  # e.g. "USD", "EUR"
    details: Optional[str] = None  # Additional expense details


class ChatToolResponse(BaseModel):
    """Response model for Omi chat tools."""
    result: Optional[str] = None
    error: Optional[str] = None


# Splitwise Data Models
class SplitwiseFriend(BaseModel):
    """Splitwise friend information."""
    id: int
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None


class SplitwiseGroup(BaseModel):
    """Splitwise group information."""
    id: int
    name: str


class SplitwiseUser(BaseModel):
    """Current Splitwise user information."""
    id: int
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    default_currency: Optional[str] = "USD"


# Omi Conversation Models (for future memory/webhook integrations)
class TranscriptSegment(BaseModel):
    """Transcript segment from Omi conversation."""
    text: str
    speaker: Optional[str] = "SPEAKER_00"
    is_user: bool
    start: float
    end: float


class Structured(BaseModel):
    """Structured conversation data."""
    title: str
    overview: str
    emoji: str = ""
    category: str = "other"


class Conversation(BaseModel):
    """Omi conversation model."""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    transcript_segments: List[TranscriptSegment] = []
    structured: Structured
    discarded: bool


class EndpointResponse(BaseModel):
    """Standard endpoint response for Omi webhooks."""
    message: str = Field(description="A short message to be sent as notification to the user, if needed.", default="")
