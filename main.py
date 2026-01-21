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


def detect_currency(amount_str: str) -> Optional[str]:
    """Detect currency from amount string based on symbols or keywords."""
    amount_lower = amount_str.lower().strip()
    
    # Check for currency symbols and keywords
    if "$" in amount_str or "dollar" in amount_lower or "usd" in amount_lower:
        return "USD"
    elif "€" in amount_str or "euro" in amount_lower or "eur" in amount_lower:
        return "EUR"
    elif "£" in amount_str or "pound" in amount_lower or "gbp" in amount_lower:
        return "GBP"
    elif "¥" in amount_str or "yen" in amount_lower or "jpy" in amount_lower:
        return "JPY"
    elif "₹" in amount_str or "rupee" in amount_lower or "inr" in amount_lower:
        return "INR"
    elif "cad" in amount_lower:
        return "CAD"
    elif "aud" in amount_lower:
        return "AUD"
    
    return None  # No currency detected


def parse_amount(amount_str: str) -> Tuple[Decimal, Optional[str]]:
    """Parse amount string to Decimal and detect currency. Returns (amount, currency_code)."""
    # Detect currency first
    detected_currency = detect_currency(amount_str)
    
    # Remove common currency symbols and whitespace
    cleaned = amount_str.strip()
    for symbol in ["$", "€", "£", "¥", "₹", "dollars", "dollar", "usd", "eur", "gbp", "inr", "jpy", "cad", "aud", "rupees", "rupee", "pounds", "euros"]:
        cleaned = cleaned.lower().replace(symbol, "").strip()
    
    try:
        return Decimal(cleaned), detected_currency
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
        
        # Parse amount and detect currency
        try:
            amount, detected_currency = parse_amount(amount_str)
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
        
        # Set currency: explicit param > detected from amount > user default
        if currency_code:
            expense.setCurrencyCode(currency_code)
        elif detected_currency:
            expense.setCurrencyCode(detected_currency)
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
        
        # Determine which currency was used
        used_currency = currency_code or detected_currency or current_user.default_currency or "USD"
        currency_symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}.get(used_currency, used_currency + " ")
        
        result_parts = [
            f"**Expense Created!**",
            f"",
            f"**{description}** - {currency_symbol}{amount}",
            f"Split with: {friend_names_str}",
            f"Each person owes: {currency_symbol}{share_amount}",
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


@app.post("/tools/get_friends", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_get_friends(request: Request):
    """
    Get the user's Splitwise friends list.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        friends = get_friends_list(uid)
        if not friends:
            return ChatToolResponse(result="You don't have any friends on Splitwise yet.")
        
        # Format friends list
        result_parts = [f"**Your Splitwise Friends ({len(friends)})**", ""]
        for i, friend in enumerate(friends, 1):
            name = f"{friend.first_name} {friend.last_name or ''}".strip()
            email_str = f" ({friend.email})" if friend.email else ""
            result_parts.append(f"{i}. {name}{email_str}")
        
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error getting friends: {e}")
        return ChatToolResponse(error=f"Failed to get friends: {str(e)}")


@app.post("/tools/list_expenses", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_list_expenses(request: Request):
    """
    List recent Splitwise expenses.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        limit = body.get("limit", 10)
        group_name = body.get("group")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Get group_id if group name specified
        group_id = None
        if group_name:
            groups = get_groups_list(uid)
            group_match, _ = fuzzy_match_group(group_name, groups)
            if group_match:
                group_id = group_match.id
        
        # Fetch expenses
        if group_id:
            expenses = client.getExpenses(group_id=group_id, limit=limit)
        else:
            expenses = client.getExpenses(limit=limit)
        
        if not expenses:
            return ChatToolResponse(result="No expenses found.")
        
        # Format expenses list
        result_parts = [f"**Recent Expenses ({len(expenses)})**", ""]
        for exp in expenses:
            desc = exp.getDescription() or "No description"
            cost = exp.getCost()
            currency = exp.getCurrencyCode() or "USD"
            date = exp.getDate()
            exp_id = exp.getId()
            
            # Parse date
            try:
                date_obj = datetime.fromisoformat(date.replace('Z', '+00:00'))
                date_str = date_obj.strftime("%b %d, %Y")
            except:
                date_str = date
            
            result_parts.append(f"• **{desc}** - {currency} {cost} ({date_str}) [ID: {exp_id}]")
        
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error listing expenses: {e}")
        return ChatToolResponse(error=f"Failed to list expenses: {str(e)}")


@app.post("/tools/delete_expense", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_delete_expense(request: Request):
    """
    Delete a Splitwise expense.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        expense_id = body.get("expense_id")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not expense_id:
            return ChatToolResponse(error="Expense ID is required. Use 'list expenses' to find expense IDs.")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Get expense details first for confirmation message
        try:
            expense = client.getExpense(expense_id)
            desc = expense.getDescription() or "Expense"
            cost = expense.getCost()
        except:
            desc = "Expense"
            cost = "unknown"
        
        # Delete expense
        success, errors = client.deleteExpense(expense_id)
        
        if errors:
            return ChatToolResponse(error=f"Failed to delete expense: {errors}")
        
        return ChatToolResponse(result=f"**Expense Deleted**\n\nDeleted: {desc} (${cost})")
    
    except Exception as e:
        print(f"Error deleting expense: {e}")
        return ChatToolResponse(error=f"Failed to delete expense: {str(e)}")


@app.post("/tools/update_expense", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_update_expense(request: Request):
    """
    Update a Splitwise expense.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        expense_id = body.get("expense_id")
        new_description = body.get("description")
        new_cost = body.get("cost")
        new_date = body.get("date")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not expense_id:
            return ChatToolResponse(error="Expense ID is required. Use 'list expenses' to find expense IDs.")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Get existing expense
        try:
            expense = client.getExpense(expense_id)
        except Exception as e:
            return ChatToolResponse(error=f"Could not find expense with ID {expense_id}")
        
        # Update fields
        updates = []
        if new_description:
            expense.setDescription(new_description)
            updates.append(f"Description: {new_description}")
        
        if new_cost:
            try:
                cost_decimal, _ = parse_amount(new_cost)
                expense.setCost(str(cost_decimal))
                updates.append(f"Cost: ${cost_decimal}")
            except:
                return ChatToolResponse(error=f"Invalid cost: {new_cost}")
        
        if new_date:
            parsed_date = parse_date(new_date)
            expense.setDate(parsed_date.strftime("%Y-%m-%dT%H:%M:%SZ"))
            updates.append(f"Date: {parsed_date.strftime('%B %d, %Y')}")
        
        if not updates:
            return ChatToolResponse(error="No updates specified. Provide description, cost, or date to update.")
        
        # Save updates
        updated_expense, errors = client.updateExpense(expense)
        
        if errors:
            return ChatToolResponse(error=f"Failed to update expense: {errors}")
        
        result_parts = ["**Expense Updated**", ""] + updates
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error updating expense: {e}")
        return ChatToolResponse(error=f"Failed to update expense: {str(e)}")


@app.post("/tools/get_expense_details", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_get_expense_details(request: Request):
    """
    Get details of a Splitwise expense including participants.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        expense_id = body.get("expense_id")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not expense_id:
            return ChatToolResponse(error="Expense ID is required. Use 'list expenses' to find expense IDs.")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        try:
            expense = client.getExpense(expense_id)
        except Exception as e:
            return ChatToolResponse(error=f"Could not find expense with ID {expense_id}")
        
        desc = expense.getDescription() or "Expense"
        cost = expense.getCost()
        currency = expense.getCurrencyCode() or "USD"
        date = expense.getDate()
        
        # Parse date
        try:
            date_obj = datetime.fromisoformat(date.replace('Z', '+00:00'))
            date_str = date_obj.strftime("%B %d, %Y")
        except:
            date_str = date
        
        result_parts = [
            f"**{desc}**",
            "",
            f"**Amount:** {currency} {cost}",
            f"**Date:** {date_str}",
            ""
        ]
        
        # Get participants
        users = expense.getUsers()
        if users:
            result_parts.append("**Participants:**")
            for user in users:
                name = f"{user.getFirstName()} {user.getLastName() or ''}".strip()
                paid = user.getPaidShare() or "0"
                owed = user.getOwedShare() or "0"
                result_parts.append(f"• {name}: paid {currency} {paid}, owes {currency} {owed}")
        
        # Get group info
        group_id = expense.getGroupId()
        if group_id and group_id != 0:
            result_parts.append(f"\n**Group ID:** {group_id}")
        
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error getting expense details: {e}")
        return ChatToolResponse(error=f"Failed to get expense details: {str(e)}")


@app.post("/tools/get_expense_comments", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_get_expense_comments(request: Request):
    """
    Get comments on a Splitwise expense.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        expense_id = body.get("expense_id")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not expense_id:
            return ChatToolResponse(error="Expense ID is required. Use 'list expenses' to find expense IDs.")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Get expense with comments
        try:
            expense = client.getExpense(expense_id)
            desc = expense.getDescription() or "Expense"
        except:
            return ChatToolResponse(error=f"Could not find expense with ID {expense_id}")
        
        # Get comments
        comments = client.getComments(expense_id)
        
        if not comments:
            return ChatToolResponse(result=f"**{desc}**\n\nNo comments on this expense.")
        
        result_parts = [f"**Comments on: {desc}**", ""]
        for comment in comments:
            # Comment object methods vary - try different approaches
            content = comment.getContent() if hasattr(comment, 'getContent') else str(comment)
            created = comment.getCreatedAt() if hasattr(comment, 'getCreatedAt') else ""
            
            # Try to get user info
            user_name = "Someone"
            try:
                if hasattr(comment, 'getUser'):
                    user = comment.getUser()
                    if user:
                        if hasattr(user, 'getFirstName'):
                            user_name = f"{user.getFirstName()} {user.getLastName() or ''}".strip()
                        elif hasattr(user, 'first_name'):
                            user_name = f"{user.first_name} {user.last_name or ''}".strip()
                        elif isinstance(user, dict):
                            user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            except:
                pass
            
            try:
                if created:
                    date_obj = datetime.fromisoformat(str(created).replace('Z', '+00:00'))
                    date_str = date_obj.strftime("%b %d at %I:%M %p")
                else:
                    date_str = ""
            except:
                date_str = str(created) if created else ""
            
            if date_str:
                result_parts.append(f"**{user_name}** ({date_str}):\n{content}\n")
            else:
                result_parts.append(f"**{user_name}**:\n{content}\n")
        
        return ChatToolResponse(result="\n".join(result_parts))
    
    except Exception as e:
        print(f"Error getting comments: {e}")
        return ChatToolResponse(error=f"Failed to get comments: {str(e)}")


@app.post("/tools/add_expense_comment", tags=["chat_tools"], response_model=ChatToolResponse)
async def tool_add_expense_comment(request: Request):
    """
    Add a comment to a Splitwise expense.
    """
    try:
        body = await request.json()
        uid = body.get("uid")
        expense_id = body.get("expense_id")
        comment_text = body.get("comment")
        
        if not uid:
            return ChatToolResponse(error="User ID is required")
        
        if not expense_id:
            return ChatToolResponse(error="Expense ID is required. Use 'list expenses' to find expense IDs.")
        
        if not comment_text:
            return ChatToolResponse(error="Comment text is required.")
        
        client = get_splitwise_client(uid)
        if not client:
            return ChatToolResponse(error="Please connect your Splitwise account first in the app settings.")
        
        # Add comment
        comment, errors = client.createComment(expense_id, comment_text)
        
        if errors:
            return ChatToolResponse(error=f"Failed to add comment: {errors}")
        
        return ChatToolResponse(result=f"**Comment Added**\n\n\"{comment_text}\"")
    
    except Exception as e:
        print(f"Error adding comment: {e}")
        return ChatToolResponse(error=f"Failed to add comment: {str(e)}")


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
            },
            {
                "name": "get_friends",
                "description": "Get the user's Splitwise friends list. Use this when the user wants to see their friends, check who they can split expenses with, or find someone's name on Splitwise.",
                "endpoint": "/tools/get_friends",
                "method": "POST",
                "parameters": {
                    "properties": {},
                    "required": []
                },
                "auth_required": True,
                "status_message": "Getting your Splitwise friends..."
            },
            {
                "name": "list_expenses",
                "description": "List recent Splitwise expenses. Use this when the user wants to see their expenses, check recent splits, view expense history, or find an expense ID.",
                "endpoint": "/tools/list_expenses",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of expenses to return (default: 10, max: 50)"
                        },
                        "group": {
                            "type": "string",
                            "description": "Filter by group name (fuzzy matched). If not provided, shows all expenses."
                        }
                    },
                    "required": []
                },
                "auth_required": True,
                "status_message": "Getting your expenses..."
            },
            {
                "name": "get_expense_details",
                "description": "Get details of a Splitwise expense including who is involved/participating, amounts paid and owed. Use this when the user wants to know who is in an expense, who paid, who owes what, or get full expense info.",
                "endpoint": "/tools/get_expense_details",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "expense_id": {
                            "type": "string",
                            "description": "The expense ID to get details for. Required. Use 'list expenses' to find IDs."
                        }
                    },
                    "required": ["expense_id"]
                },
                "auth_required": True,
                "status_message": "Getting expense details..."
            },
            {
                "name": "update_expense",
                "description": "Update an existing Splitwise expense. Use this when the user wants to change, edit, or modify an expense's description, amount, or date.",
                "endpoint": "/tools/update_expense",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "expense_id": {
                            "type": "string",
                            "description": "The expense ID to update. Required. Use 'list expenses' to find IDs."
                        },
                        "description": {
                            "type": "string",
                            "description": "New description for the expense."
                        },
                        "cost": {
                            "type": "string",
                            "description": "New cost/amount for the expense (e.g., '25', '25.50')."
                        },
                        "date": {
                            "type": "string",
                            "description": "New date for the expense."
                        }
                    },
                    "required": ["expense_id"]
                },
                "auth_required": True,
                "status_message": "Updating expense..."
            },
            {
                "name": "delete_expense",
                "description": "Delete a Splitwise expense. Use this when the user wants to remove, delete, or cancel an expense.",
                "endpoint": "/tools/delete_expense",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "expense_id": {
                            "type": "string",
                            "description": "The expense ID to delete. Required. Use 'list expenses' to find IDs."
                        }
                    },
                    "required": ["expense_id"]
                },
                "auth_required": True,
                "status_message": "Deleting expense..."
            },
            {
                "name": "get_expense_comments",
                "description": "Get comments on a Splitwise expense. Use this when the user wants to see comments, notes, or discussions on an expense.",
                "endpoint": "/tools/get_expense_comments",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "expense_id": {
                            "type": "string",
                            "description": "The expense ID to get comments for. Required. Use 'list expenses' to find IDs."
                        }
                    },
                    "required": ["expense_id"]
                },
                "auth_required": True,
                "status_message": "Getting expense comments..."
            },
            {
                "name": "add_expense_comment",
                "description": "Add a comment to a Splitwise expense. Use this when the user wants to comment on, note, or add a message to an expense.",
                "endpoint": "/tools/add_expense_comment",
                "method": "POST",
                "parameters": {
                    "properties": {
                        "expense_id": {
                            "type": "string",
                            "description": "The expense ID to comment on. Required. Use 'list expenses' to find IDs."
                        },
                        "comment": {
                            "type": "string",
                            "description": "The comment text to add. Required."
                        }
                    },
                    "required": ["expense_id", "comment"]
                },
                "auth_required": True,
                "status_message": "Adding comment..."
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
