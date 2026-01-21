"""
Splitwise Integration App for Omi

This app provides Splitwise integration through OAuth2 authentication
and chat tools for creating expenses and splitting costs with friends.
"""
import os
import secrets
import difflib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal, ROUND_DOWN

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from splitwise import Splitwise
from splitwise.expense import Expense
from splitwise.user import ExpenseUser

from db import (
    store_splitwise_tokens,
    get_splitwise_tokens,
    delete_splitwise_tokens,
    store_oauth_state,
    get_oauth_state,
    delete_oauth_state,
    get_user_settings,
)
from models import (
    ChatToolResponse,
    CreateExpenseRequest,
    SplitwiseFriend,
    SplitwiseGroup,
    SplitwiseUser,
)

load_dotenv()

# Splitwise API Configuration
SPLITWISE_CONSUMER_KEY = os.getenv("SPLITWISE_CONSUMER_KEY", "")
SPLITWISE_CONSUMER_SECRET = os.getenv("SPLITWISE_CONSUMER_SECRET", "")
SPLITWISE_REDIRECT_URI = os.getenv("SPLITWISE_REDIRECT_URI", "http://localhost:8080/auth/splitwise/callback")

app = FastAPI(
    title="Splitwise Omi Integration",
    description="Splitwise integration for Omi - Split expenses with friends using voice",
    version="1.0.0"
)

# Mount static files and templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
if os.path.exists(templates_dir):
    static_dir = os.path.join(templates_dir, "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


# ============================================
# Helper Functions
# ============================================

def get_splitwise_client(uid: str) -> Optional[Splitwise]:
    """Get an authenticated Splitwise client for a user."""
    tokens = get_splitwise_tokens(uid)
    if not tokens:
        return None
    
    s = Splitwise(SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)
    # setOAuth2AccessToken expects a dict with access_token and token_type
    token_dict = {
        "access_token": tokens["access_token"],
        "token_type": tokens.get("token_type", "Bearer")
    }
    s.setOAuth2AccessToken(token_dict)
    return s


def get_current_user(uid: str) -> Optional[SplitwiseUser]:
    """Get the current Splitwise user info."""
    client = get_splitwise_client(uid)
    if not client:
        return None
    
    try:
        user = client.getCurrentUser()
        return SplitwiseUser(
            id=user.getId(),
            first_name=user.getFirstName() or "",
            last_name=user.getLastName(),
            email=user.getEmail(),
            default_currency=user.getDefaultCurrency() or "USD"
        )
    except Exception as e:
        print(f"Error getting current user: {e}")
        return None


def get_friends_list(uid: str) -> List[SplitwiseFriend]:
    """Get the user's friends list from Splitwise."""
    client = get_splitwise_client(uid)
    if not client:
        return []
    
    try:
        friends = client.getFriends()
        return [
            SplitwiseFriend(
                id=f.getId(),
                first_name=f.getFirstName() or "",
                last_name=f.getLastName(),
                email=f.getEmail()
            )
            for f in friends
        ]
    except Exception as e:
        print(f"Error getting friends: {e}")
        return []


def get_groups_list(uid: str) -> List[SplitwiseGroup]:
    """Get the user's groups list from Splitwise."""
    client = get_splitwise_client(uid)
    if not client:
        return []
    
    try:
        groups = client.getGroups()
        return [
            SplitwiseGroup(
                id=g.getId(),
                name=g.getName() or ""
            )
            for g in groups
            if g.getId() != 0  # Exclude "non-group" group
        ]
    except Exception as e:
        print(f"Error getting groups: {e}")
        return []


def fuzzy_match_friend(name: str, friends: List[SplitwiseFriend], threshold: float = 0.6) -> Tuple[Optional[SplitwiseFriend], float, List[SplitwiseFriend]]:
    """
    Fuzzy match a name against the friends list.
    Returns: (best_match, confidence, top_candidates)
    """
    if not friends:
        return None, 0.0, []
    
    name_lower = name.lower().strip()
    scored_friends = []
    
    for friend in friends:
        # Build variations of the friend's name to match against
        full_name = f"{friend.first_name} {friend.last_name or ''}".strip().lower()
        first_name = friend.first_name.lower() if friend.first_name else ""
        last_name = (friend.last_name or "").lower()
        email_prefix = (friend.email or "").split("@")[0].lower() if friend.email else ""
        
        # Calculate similarity scores for different name variations
        scores = [
            difflib.SequenceMatcher(None, name_lower, full_name).ratio(),
            difflib.SequenceMatcher(None, name_lower, first_name).ratio(),
            difflib.SequenceMatcher(None, name_lower, last_name).ratio() if last_name else 0,
            difflib.SequenceMatcher(None, name_lower, email_prefix).ratio() if email_prefix else 0,
        ]
        
        # Also check if input is a substring or prefix
        if name_lower in full_name or full_name.startswith(name_lower):
            scores.append(0.85)
        if name_lower == first_name or name_lower == last_name:
            scores.append(1.0)
        
        best_score = max(scores)
        scored_friends.append((friend, best_score))
    
    # Sort by score descending
    scored_friends.sort(key=lambda x: x[1], reverse=True)
    
    best_match, best_score = scored_friends[0] if scored_friends else (None, 0.0)
    top_candidates = [f for f, s in scored_friends[:3] if s >= threshold * 0.8]
    
    if best_score >= threshold:
        return best_match, best_score, top_candidates
    else:
        return None, best_score, top_candidates


def fuzzy_match_group(name: str, groups: List[SplitwiseGroup], threshold: float = 0.6) -> Tuple[Optional[SplitwiseGroup], float]:
    """Fuzzy match a group name against the groups list."""
    if not groups:
        return None, 0.0
    
    name_lower = name.lower().strip()
    best_match = None
    best_score = 0.0
    
    for group in groups:
        group_name_lower = group.name.lower()
        score = difflib.SequenceMatcher(None, name_lower, group_name_lower).ratio()
        
        # Boost score if input is substring
        if name_lower in group_name_lower or group_name_lower.startswith(name_lower):
            score = max(score, 0.85)
        
        if score > best_score:
            best_score = score
            best_match = group
    
    if best_score >= threshold:
        return best_match, best_score
    return None, best_score


def parse_date(date_str: Optional[str]) -> datetime:
    """Parse various date formats into datetime object."""
    if not date_str:
        return datetime.utcnow()
    
    date_str = date_str.strip().lower()
    today = datetime.utcnow().date()
    
    # Handle relative dates
    if date_str in ("today", "now"):
        return datetime.utcnow()
    elif date_str == "yesterday":
        return datetime.combine(today - timedelta(days=1), datetime.min.time())
    
    # Try various date formats
    formats = [
        "%Y-%m-%d",           # 2026-01-20
        "%m/%d/%Y",           # 01/20/2026
        "%d/%m/%Y",           # 20/01/2026
        "%B %d, %Y",          # January 20, 2026
        "%b %d, %Y",          # Jan 20, 2026
        "%B %d %Y",           # January 20 2026
        "%b %d %Y",           # Jan 20 2026
        "%d %B %Y",           # 20 January 2026
        "%d %b %Y",           # 20 Jan 2026
        "%B %d",              # January 20 (assume current year)
        "%b %d",              # Jan 20 (assume current year)
    ]
    
    for fmt in formats:
        try:
            parsed = datetime.strptime(date_str, fmt)
            # If year not in format, use current year
            if "%Y" not in fmt:
                parsed = parsed.replace(year=today.year)
            return parsed
        except ValueError:
            continue
    
    # Default to today if parsing fails
    return datetime.utcnow()


def parse_amount(amount_str: str) -> Decimal:
    """Parse amount string to Decimal, handling currency symbols."""
    # Remove common currency symbols and whitespace
    cleaned = amount_str.strip()
    for symbol in ["$", "€", "£", "¥", "₹", "dollars", "dollar", "usd", "eur"]:
        cleaned = cleaned.replace(symbol, "").strip()
    
    try:
        return Decimal(cleaned)
    except:
        raise ValueError(f"Invalid amount: {amount_str}")


def compute_equal_shares(total: Decimal, num_people: int) -> List[Decimal]:
    """
    Compute equal shares for splitting, handling rounding properly.
    Returns a list of shares that sum exactly to total.
    """
    # Round to 2 decimal places
    base_share = (total / num_people).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    remainder = total - (base_share * num_people)
    
    # Distribute remainder cents to first N people
    remainder_cents = int(remainder * 100)
    shares = []
    for i in range(num_people):
        share = base_share
        if i < remainder_cents:
            share += Decimal("0.01")
        shares.append(share)
    
    return shares


# ============================================
# OAuth Endpoints
# ============================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, uid: Optional[str] = None):
    """Home page / App settings page."""
    if not uid:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": "Missing user ID"
        })
    
    tokens = get_splitwise_tokens(uid)
    authenticated = tokens is not None
    
    user_info = None
    if authenticated:
        user_info = get_current_user(uid)
    
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "uid": uid,
        "authenticated": authenticated,
        "user_info": user_info,
    })


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "splitwise-omi"}


@app.get("/auth/splitwise")
async def splitwise_auth(uid: str):
    """Initiate Splitwise OAuth2 flow."""
    if not uid:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    if not SPLITWISE_CONSUMER_KEY or not SPLITWISE_CONSUMER_SECRET:
        raise HTTPException(status_code=500, detail="Splitwise credentials not configured")
    
    # Create Splitwise instance and get OAuth2 authorize URL
    s = Splitwise(SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)
    url, state = s.getOAuth2AuthorizeURL(SPLITWISE_REDIRECT_URI)
    
    # Store state for CSRF verification, encode uid in state
    combined_state = f"{uid}:{state}"
    store_oauth_state(uid, combined_state)
    
    # Modify URL to use our combined state
    # The SDK generates a random state, but we need to include uid
    # So we'll use our own state parameter
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)
    query_params["state"] = [combined_state]
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    auth_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
    
    return RedirectResponse(url=auth_url)


@app.get("/auth/splitwise/callback")
async def splitwise_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handle Splitwise OAuth2 callback."""
    if error:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": f"Authorization failed: {error}"
        })
    
    if not code or not state:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": "Invalid callback parameters"
        })
    
    # Extract uid from state
    try:
        uid, original_state = state.split(":", 1)
    except ValueError:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": "Invalid state parameter"
        })
    
    # Verify state matches what we stored
    stored_state = get_oauth_state(uid)
    if stored_state != state:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": "State mismatch - possible CSRF attack"
        })
    
    # Clean up state
    delete_oauth_state(uid)
    
    # Exchange code for access token
    try:
        print(f"DEBUG: Exchanging code for token")
        print(f"DEBUG: SPLITWISE_REDIRECT_URI = {SPLITWISE_REDIRECT_URI}")
        print(f"DEBUG: code = {code[:10]}...")
        
        s = Splitwise(SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)
        token_response = s.getOAuth2AccessToken(code, SPLITWISE_REDIRECT_URI)
        
        print(f"DEBUG: Token response received")
        
        # Store token (full dict including token_type)
        store_splitwise_tokens(
            uid, 
            token_response["access_token"],
            token_response.get("token_type", "Bearer")
        )
        
        # Redirect to home with uid
        return RedirectResponse(url=f"/?uid={uid}")
    
    except Exception as e:
        print(f"OAuth error: {e}")
        print(f"DEBUG: SPLITWISE_REDIRECT_URI was: {SPLITWISE_REDIRECT_URI}")
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "authenticated": False,
            "error": f"Failed to exchange authorization code: {str(e)}"
        })


@app.get("/setup/splitwise", tags=["setup"])
async def check_setup(uid: str):
    """Check if the user has completed Splitwise setup (used by Omi)."""
    tokens = get_splitwise_tokens(uid)
    return {"is_setup_completed": tokens is not None}


@app.get("/disconnect")
async def disconnect_splitwise(uid: str):
    """Disconnect Splitwise account."""
    delete_splitwise_tokens(uid)
    return RedirectResponse(url=f"/?uid={uid}")


# ============================================
# Chat Tool Endpoints
# ============================================

@app.post("/tools/create_expense", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_create_expense(request: Request):
    """
    Create a Splitwise expense.
    Chat tool for Omi - creates an expense split among specified friends.
    """
    try:
        body = await request.json()
        print(f"CREATE_EXPENSE - Received request: {body}")
        
        uid = body.get("uid")
        amount_str = body.get("amount", "")
        description = body.get("description", "Expense")
        date_str = body.get("date")
        person = body.get("person")
        people = body.get("people", [])
        group_name = body.get("group")
        currency_code = body.get("currency_code")
        details = body.get("details")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not amount_str:
            return ChatToolResponse(error="Amount is required")
        
        # Check authentication
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Get current user
        current_user = get_current_user(uid)
        if not current_user:
            return ChatToolResponse(error="Could not get your Splitwise user info. Please reconnect your account.")
        
        # Parse amount
        try:
            amount = parse_amount(amount_str)
            if amount <= 0:
                return ChatToolResponse(error="Amount must be greater than zero")
        except ValueError as e:
            return ChatToolResponse(error=str(e))
        
        # Parse date
        expense_date = parse_date(date_str)
        
        # Normalize people list
        friend_names = []
        if person:
            friend_names.append(person)
        if people:
            friend_names.extend(people)
        
        if not friend_names:
            return ChatToolResponse(error="Please specify at least one person to split with (e.g., 'with John' or 'with Alice and Bob')")
        
        # Get friends list and match names
        friends = get_friends_list(uid)
        if not friends:
            return ChatToolResponse(error="Could not fetch your friends list. Please make sure you have friends on Splitwise.")
        
        matched_friends = []
        for name in friend_names:
            match, confidence, candidates = fuzzy_match_friend(name, friends)
            if not match:
                candidate_names = [f"{c.first_name} {c.last_name or ''}".strip() for c in candidates[:3]]
                if candidate_names:
                    return ChatToolResponse(
                        error=f"Could not find friend '{name}'. Did you mean: {', '.join(candidate_names)}?"
                    )
                else:
                    return ChatToolResponse(error=f"Could not find friend '{name}' in your Splitwise friends list.")
            matched_friends.append(match)
        
        # Check for duplicate friends
        friend_ids = [f.id for f in matched_friends]
        if len(friend_ids) != len(set(friend_ids)):
            return ChatToolResponse(error="Duplicate friends detected. Please specify each person only once.")
        
        # Resolve group if specified
        group_id = 0  # 0 = non-group expense
        group_info = None
        if group_name:
            groups = get_groups_list(uid)
            group_match, group_confidence = fuzzy_match_group(group_name, groups)
            if not group_match:
                group_names = [g.name for g in groups[:5]]
                if group_names:
                    return ChatToolResponse(
                        error=f"Could not find group '{group_name}'. Your groups: {', '.join(group_names)}"
                    )
                else:
                    return ChatToolResponse(error=f"Could not find group '{group_name}'. You don't have any groups.")
            group_id = group_match.id
            group_info = group_match
        
        # Calculate equal shares (you + all friends)
        total_people = 1 + len(matched_friends)  # current user + friends
        shares = compute_equal_shares(amount, total_people)
        
        # Build expense
        expense = Expense()
        expense.setCost(str(amount))
        expense.setDescription(description)
        expense.setDate(expense_date.strftime("%Y-%m-%dT%H:%M:%SZ"))
        expense.setGroupId(group_id)
        
        if currency_code:
            expense.setCurrencyCode(currency_code)
        elif current_user.default_currency:
            expense.setCurrencyCode(current_user.default_currency)
        
        if details:
            expense.setDetails(details)
        
        # Build users list - current user paid full amount, everyone owes their share
        users = []
        
        # Current user (payer)
        payer = ExpenseUser()
        payer.setId(current_user.id)
        payer.setPaidShare(str(amount))  # Paid full amount
        payer.setOwedShare(str(shares[0]))  # Owes their share
        users.append(payer)
        
        # Friends (owe their shares)
        for i, friend in enumerate(matched_friends):
            eu = ExpenseUser()
            eu.setId(friend.id)
            eu.setPaidShare("0.00")
            eu.setOwedShare(str(shares[i + 1]))
            users.append(eu)
        
        expense.setUsers(users)
        
        # Create expense
        print(f"Creating expense: cost={amount}, desc={description}, group_id={group_id}, users={len(users)}")
        created_expense, errors = client.createExpense(expense)
        
        if errors:
            error_msg = str(errors)
            print(f"Splitwise error: {error_msg}")
            return ChatToolResponse(error=f"Failed to create expense: {error_msg}")
        
        # Format success message
        friend_names_str = ", ".join([f"{f.first_name} {f.last_name or ''}".strip() for f in matched_friends])
        share_amount = shares[1] if len(shares) > 1 else shares[0]
        
        result_parts = [
            f"**Expense Created!**",
            f"",
            f"**{description}** - ${amount}",
            f"Split with: {friend_names_str}",
            f"Each person owes: ${share_amount}",
        ]
        
        if group_info:
            result_parts.append(f"Group: {group_info.name}")
        
        result_parts.append(f"Date: {expense_date.strftime('%B %d, %Y')}")
        
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error creating expense: {e}")
        import traceback
        traceback.print_exc()
        return ChatToolResponse(error=f"Failed to create expense: {str(e)}")


# ============================================
# Omi Chat Tools Manifest
# ============================================

@app.get("/.well-known/omi-tools.json")
async def get_omi_tools_manifest():
    """
    Omi Chat Tools Manifest endpoint.
    
    This endpoint returns the chat tools definitions that Omi will fetch
    when the app is created or updated in the Omi App Store.
    """
    return {
        "tools": [
            {
                "name": "create_expense",
                "description": "Create a Splitwise expense and split it with friends. Use this when the user wants to split costs, share expenses, divide bills, or log shared purchases with people. The expense will be split equally among the user and the specified friends. By default creates a non-group expense unless a group is specified.",
                "endpoint": "/tools/create_expense",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "amount": {
                            "type": "string",
                            "description": "The total expense amount (e.g., '25', '25.50', '$30'). Required."
                        },
                        "description": {
                            "type": "string",
                            "description": "What the expense is for (e.g., 'lunch', 'groceries', 'dinner', 'uber'). Defaults to 'Expense' if not provided."
                        },
                        "date": {
                            "type": "string",
                            "description": "When the expense occurred. Supports: 'today', 'yesterday', or dates like '2026-01-20', 'Jan 15', 'January 15, 2026'. Defaults to today."
                        },
                        "person": {
                            "type": "string",
                            "description": "Name of a single person to split with (fuzzy matched to Splitwise friends). Use this OR 'people', not both."
                        },
                        "people": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Names of multiple people to split with (each fuzzy matched to Splitwise friends). Use this when splitting with 2+ people."
                        },
                        "group": {
                            "type": "string",
                            "description": "Name of a Splitwise group to add this expense to (fuzzy matched). If not provided, creates a non-group expense."
                        },
                        "currency_code": {
                            "type": "string",
                            "description": "Currency code (e.g., 'USD', 'EUR', 'GBP'). Defaults to user's Splitwise default currency."
                        },
                        "details": {
                            "type": "string",
                            "description": "Additional notes or details about the expense."
                        }
                    },
                    "required": ["amount"]
                },
                "auth_required": True,
                "status_message": "Creating Splitwise expense..."
            }
        ]
    }


# ============================================
# Run Server
# ============================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
