import os
import json
import datetime
from typing import Dict, List, Optional
import base64
import io

import streamlit as st
import requests
from datetime import datetime as dt

st.set_page_config(page_title="HIVE Admin Panel", page_icon="image.ico", layout="wide")

# -----------------------------
# User Authentication & Data Isolation
# -----------------------------
def get_user_identity():
    """Get or create user identity for data isolation"""
    if 'user_id' not in st.session_state:
        st.session_state.user_id = None
    
    if not st.session_state.user_id:
        with st.container():
            st.markdown("### 🔐 Welcome to HIVE Admin Panel")
            st.markdown("Enter your username to access your personal workspace:")
            user_input = st.text_input("Username:", key="user_login", placeholder="Enter your username")
            if st.button("Login", key="login_btn"):
                if user_input.strip():
                    st.session_state.user_id = user_input.strip().lower()
                    st.rerun()
                else:
                    st.error("Please enter a valid username")
        return None
    
    return st.session_state.user_id

def get_user_data_key(key_name):
    """Get user-specific data key"""
    user_id = st.session_state.get('user_id', 'default')
    return f"{key_name}_{user_id}"

def get_user_data(key_name, default=None):
    """Get user-specific data from session state"""
    data_key = get_user_data_key(key_name)
    return st.session_state.get(data_key, default)

def set_user_data(key_name, data):
    """Set user-specific data in session state"""
    data_key = get_user_data_key(key_name)
    st.session_state[data_key] = data

def logout_user():
    """Logout current user"""
    st.session_state.user_id = None
    st.rerun()

# -----------------------------
# Helpers: rerun + per-member cache
# -----------------------------
def safe_rerun():
    if getattr(st, "rerun", None):
        st.rerun()
    elif getattr(st, "experimental_rerun", None):
        st.experimental_rerun()
    else:
        st.session_state["_force_rerun"] = st.session_state.get("_force_rerun", 0) + 1

def get_member_cache():
    """Get user-specific member cache"""
    cache_key = get_user_data_key("member_cache")
    if cache_key not in st.session_state:
        st.session_state[cache_key] = {}
    return st.session_state[cache_key]

def member_key(member: dict) -> str:
    return (member.get("email") or member.get("name") or "unknown").lower().strip()

# -----------------------------
# Minimal CSS
# -----------------------------
st.markdown(
    """
    <style>
      .app-title { font-size: 32px; font-weight: 800; letter-spacing: 0.5px; }
      .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      .grid-cell { border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 16px; }
      .grid-title { font-weight: 800; font-size: 20px; margin-bottom: 10px; display:flex; gap:8px; align-items:center;}
      .scrollbox { max-height: 360px; overflow-y:auto; }
      .card { padding: 1rem 1.25rem; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
              background: rgba(255,255,255,0.03); margin-bottom: 1rem; width: 100%; box-sizing: border-box; }
      .card h4 { margin: 0 0 .5rem 0; font-weight: 700; }
      .stDownloadButton button { width: 100%; }
      .user-info { background: rgba(0,255,0,0.1); padding: 8px; border-radius: 8px; margin-bottom: 16px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Data Export/Import Functions
# -----------------------------
def export_user_data():
    """Export all user data as JSON"""
    user_id = st.session_state.get('user_id', 'default')
    export_data = {
        'user_id': user_id,
        'exported_at': dt.now().isoformat(),
        'team_members': get_user_data('team_members', {"team_members": []}),
        'settings': get_user_data('settings', {"notion_api_key": "", "groq_api_key": ""}),
        'member_cache': get_member_cache()
    }
    return json.dumps(export_data, indent=2)

def import_user_data(uploaded_file):
    """Import user data from JSON file"""
    try:
        content = uploaded_file.read().decode('utf-8')
        data = json.loads(content)
        
        # Validate data structure
        if 'team_members' in data:
            set_user_data('team_members', data['team_members'])
        if 'settings' in data:
            set_user_data('settings', data['settings'])
        if 'member_cache' in data:
            cache_key = get_user_data_key("member_cache")
            st.session_state[cache_key] = data['member_cache']
        
        return True, "Data imported successfully!"
    except Exception as e:
        return False, f"Error importing data: {str(e)}"

def clear_user_data():
    """Clear all user data"""
    user_id = st.session_state.get('user_id', 'default')
    keys_to_remove = [key for key in st.session_state.keys() if key.endswith(f"_{user_id}")]
    for key in keys_to_remove:
        del st.session_state[key]
    return True

# -----------------------------
# Calendar logic with Google API support
# -----------------------------
class TeamCalendarAdmin:
    def __init__(self):
        self.service = None
        self.team_members = {}
        self.initialize_service()
        self.load_team_members()
    
    def initialize_service(self):
        """Initialize Google Calendar service with user's credentials"""
        try:
            # Check if user has uploaded service account credentials
            service_creds = get_user_data('service_credentials', None)
            if not service_creds:
                return False
            
            # Import Google API libraries only when needed
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
            except ImportError as import_error:
                st.error(f"❌ Missing Google API dependencies: {str(import_error)}")
                st.info("Please ensure all Google API packages are installed. Check your requirements.txt file.")
                return False
            
            # Validate service account structure
            required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
            for field in required_fields:
                if field not in service_creds:
                    st.error(f"❌ Invalid service account: missing {field}")
                    return False
            
            # Create credentials from the stored service account data - use same scopes as original
            SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
            credentials = service_account.Credentials.from_service_account_info(
                service_creds, 
                scopes=SCOPES
            )
            self.service = build('calendar', 'v3', credentials=credentials)
            
            # Don't test calendar listing - just initialize like original app
            # The original app doesn't show calendar count or test access
            
            return True
        except Exception as e:
            st.error(f"❌ Error initializing Google Calendar service: {str(e)}")
            return False
    
    def load_team_members(self):
        team_data = get_user_data('team_members', {"team_members": []})
        self.team_members = team_data
        return True
    
    def get_member_events(self, calendar_id: str, start_date: datetime.datetime, end_date: datetime.datetime):
        """Get calendar events for a member"""
        try:
            if not self.service:
                st.error("❌ Google Calendar service not initialized. Please upload service account credentials.")
                return []
            
            # Use exact same format as original app.py - no timezone conversion, just add 'Z'
            time_min = start_date.isoformat() + 'Z'
            time_max = end_date.isoformat() + 'Z'
            
            # Debug info removed to match original app behavior
            
            # Try the API call with error handling
            try:
                events_result = self.service.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=100,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
            except Exception as api_error:
                st.error(f"❌ API Error: {str(api_error)}")
                # Try with 'primary' calendar ID as fallback
                st.info("Trying with 'primary' calendar ID...")
                try:
                    events_result = self.service.events().list(
                        calendarId='primary',
                        timeMin=time_min,
                        timeMax=time_max,
                        maxResults=100,
                        singleEvents=True,
                        orderBy='startTime'
                    ).execute()
                    st.success("✅ Successfully fetched from primary calendar")
                except Exception as fallback_error:
                    st.error(f"❌ Fallback also failed: {str(fallback_error)}")
                    return []
            
            events = events_result.get('items', [])
            st.success(f"✅ Found {len(events)} events in calendar")
            
            # Debug info removed to match original app behavior
            
            return events
        except Exception as e:
            st.error(f"❌ Error fetching calendar events: {str(e)}")
            return []
    
    def format_event_time(self, event):
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        if 'T' in start:
            start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            start_formatted = start_dt.strftime('%Y-%m-%d %H:%M')
        else:
            start_dt = datetime.datetime.fromisoformat(start)
            start_formatted = start_dt.strftime('%Y-%m-%d (All day)')
        if 'T' in end:
            end_dt = datetime.datetime.fromisoformat(end.replace('Z', '+00:00'))
            end_formatted = end_dt.strftime('%Y-%m-%d %H:%M')
        else:
            end_dt = datetime.datetime.fromisoformat(end)
            end_formatted = end_dt.strftime('%Y-%m-%d (All day)')
        return start_formatted, end_formatted

def get_week_dates(year: int, month: int, week: int):
    first_day = datetime.datetime(year, month, 1)
    week_start = first_day + datetime.timedelta(weeks=week-1)
    week_end = week_start + datetime.timedelta(days=6)
    if month == 12:
        last_day_of_month = datetime.datetime(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        last_day_of_month = datetime.datetime(year, month + 1, 1) - datetime.timedelta(days=1)
    if week_end > last_day_of_month:
        week_end = last_day_of_month
    return week_start, week_end

def get_month_dates(year: int, month: int):
    start_date = datetime.datetime(year, month, 1)
    if month == 12:
        end_date = datetime.datetime(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end_date = datetime.datetime(year, month + 1, 1) - datetime.timedelta(days=1)
    return start_date, end_date

# -----------------------------
# Notion logic (Same as original)
# -----------------------------
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"

def notion_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def fetch_page_last_edited(api_key: str, page_id: str) -> Optional[str]:
    url = f"{BASE_URL}/pages/{page_id}"
    try:
        r = requests.get(url, headers=notion_headers(api_key))
        if r.status_code == 401:
            st.error("❌ Unauthorized Notion API key.")
            return None
        if r.status_code == 404:
            st.error("❌ Notion page not found.")
            return None
        r.raise_for_status()
        data = r.json()
        return data.get("last_edited_time")
    except requests.exceptions.RequestException as e:
        st.error(f"Notion meta error: {e}")
        return None

def fetch_top_level_blocks(api_key, page_id, start_cursor=None):
    url = f"{BASE_URL}/blocks/{page_id}/children"
    params = {}
    if start_cursor:
        params['start_cursor'] = start_cursor
    try:
        response = requests.get(url, headers=notion_headers(api_key), params=params)
        if response.status_code == 401:
            st.error("❌ Unauthorized: Please check your Notion API Key.")
            return [], None
        elif response.status_code == 404:
            st.error("❌ Page not found: Please check your Notion Page ID.")
            return [], None
        response.raise_for_status()
        result = response.json()
        blocks = result.get('results', [])
        next_cursor = result.get('next_cursor', None)
        return blocks, next_cursor
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Error fetching blocks: {str(e)}")
        return [], None

def fetch_children(api_key, block_id):
    """Fetch ALL children for a block, following Notion pagination until exhausted."""
    url = f"{BASE_URL}/blocks/{block_id}/children"
    all_results: List[dict] = []
    start_cursor = None
    try:
        while True:
            params = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor
            response = requests.get(url, headers=notion_headers(api_key), params=params)
            response.raise_for_status()
            data = response.json()
            all_results.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor") if has_more else None
            if not has_more:
                break
        return all_results
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Error fetching children for block {block_id}: {str(e)}")
        return []

def fetch_all_children(api_key, block_id):
    """Recursively fetch all descendants for a block with pagination."""
    children = fetch_children(api_key, block_id)
    all_children: List[dict] = []
    for child in children:
        all_children.append(child)
        if child.get('has_children', False):
            all_children.extend(fetch_all_children(api_key, child['id']))
    return all_children

def process_toggle_blocks(api_key, blocks, progress_container):
    processed_data = []
    total = len(blocks)
    for i, block in enumerate(blocks, start=1):
        if (block["type"] in ("heading_2", "heading_3") 
            and block.get(block["type"], {}).get("is_toggleable", False)):
            title = block['heading_2']['rich_text'][0]['plain_text'] if block["type"] == "heading_2" else block['heading_3']['rich_text'][0]['plain_text']
            progress_container.text(f"📝 Processing toggle {i}/{total}: {title}")
            
            children = fetch_all_children(api_key, block["id"])
            
            week_data = f"{title}\n"
            for child in children:
                if child['type'] == 'paragraph':
                    sentence = ''.join([text['text']['content'] for text in child['paragraph']['rich_text']])
                    week_data += f"Child sentence: {sentence}\n"
                elif child['type'] == 'numbered_list_item':
                    sentence = ''.join([text['text']['content'] for text in child['numbered_list_item']['rich_text']])
                    week_data += f"Child numbered list item: {sentence}\n"
                elif child['type'] == 'to_do':
                    checkbox = ''.join([text['text']['content'] for text in child['to_do']['rich_text']])
                    week_data += f"Child to-do (checkbox): {checkbox}\n"
                elif child['type'] == 'bulleted_list_item':
                    sentence = ''.join([text['text']['content'] for text in child['bulleted_list_item']['rich_text']])
                    week_data += f"Child bullet point: {sentence}\n"
            processed_data.append(week_data)
    return processed_data

def get_all_toggle_blocks(api_key, page_id, progress_container):
    """Fetch ALL top-level blocks across pages and filter toggleable H2/H3 reliably."""
    start_cursor = None
    toggle_blocks: List[dict] = []
    page_count = 0
    while True:
        blocks, start_cursor = fetch_top_level_blocks(api_key, page_id, start_cursor)
        page_count += 1
        progress_container.text(f"🔄 Processing page {page_count}... Found {len(toggle_blocks)} toggle blocks so far")
        for block in blocks:
            btype = block.get('type')
            if btype in ('heading_2', 'heading_3'):
                cfg = block.get(btype, {})
                if cfg.get('is_toggleable', False):
                    toggle_blocks.append(block)
                    title = cfg.get('rich_text', [{}])[0].get('plain_text', '(untitled)') if cfg.get('rich_text') else '(untitled)'
                    progress_container.text(f"✅ Found toggle: {title} (Total: {len(toggle_blocks)})")
        if not start_cursor:
            break
    return toggle_blocks

def process_with_groq(raw_data, groq_api_key, temperature):
    prompt = f"""
You are a data extraction bot. Your task is to process the following raw text and return a single, valid JSON array. DO NOT include any explanatory text, conversational phrases, or code snippets.

The JSON array must contain objects, with each object representing a week from the data. Each week object must have the following keys:
- "week": The week's title (e.g., "Week of March 3rd").
- "weekly_goals": An array of strings containing the weekly goals.
- "daily_logs": A dictionary where keys are days of the week and values are arrays of strings.
- "weekly_review": A string containing the end-of-week reflection.

If a section is missing, use [] or {{}} or "" accordingly.

Text:
{raw_data}
"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {groq_api_key}"}
    endpoint_url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    try:
        with st.spinner("🤖 Processing data with AI..."):
            response = requests.post(endpoint_url, headers=headers, data=json.dumps(payload))
            if response.status_code == 401:
                st.error("❌ **Unauthorized**: Please check your Groq API Key.")
                return None
            elif response.status_code == 429:
                st.error("❌ **Rate Limited**: Too many requests.")
                return None
            elif response.status_code == 400:
                st.error("❌ **Bad Request**: Invalid request format.")
                return None
            response.raise_for_status()
            data = response.json()
            if data.get('choices'):
                content_str = data['choices'][0]['message']['content']
                try:
                    content_json = json.loads(content_str)
                    return content_json
                except json.JSONDecodeError:
                    st.error("❌ AI response is not valid JSON.")
                    st.text(content_str)
                    return None
            else:
                st.error("❌ No choices in AI response.")
                return None
    except requests.exceptions.RequestException:
        st.error("❌ Network Error: Failed to connect to Groq API.")
        return None

# -----------------------------
# Main App Logic
# -----------------------------

# Check user authentication first
user_id = get_user_identity()
if not user_id:
    st.stop()

# Initialize user data if not exists
if not get_user_data('team_members'):
    set_user_data('team_members', {
        "team_members": [
            {
                "name": "Your Name",
                "role": "Your Role",
                "department": "Your Department",
                "email": "your.email@example.com",
                "calendar_id": "your.email@example.com",
                "notion_page_id": "",
            }
        ]
    })

if not get_user_data('settings'):
    # Try to get from Streamlit secrets first, then default
    try:
        settings = {
            "notion_api_key": st.secrets.get("notion_api_key", ""),
            "groq_api_key": st.secrets.get("groq_api_key", "")
        }
    except:
        settings = {"notion_api_key": "", "groq_api_key": ""}
    set_user_data('settings', settings)

# -----------------------------
# Data & Controls
# -----------------------------
settings = get_user_data('settings', {"notion_api_key": "", "groq_api_key": ""})
team = get_user_data('team_members', {"team_members": []})
members = team.get("team_members", [])
names = [m["name"] for m in members]

# User info header
st.markdown(f"""
<div class='user-info'>
    <strong>👤 Logged in as:</strong> {user_id} | 
    <button onclick="window.parent.postMessage('logout', '*')" style="background: none; border: none; color: #ff6b6b; cursor: pointer;">Logout</button>
</div>
""", unsafe_allow_html=True)

top_l, top_c, top_r = st.columns([2, 6, 2])
with top_l:
    st.markdown("<div class='app-title'>🧭 HIVE ADMIN PANEL</div>", unsafe_allow_html=True)

with top_c:
    c1, c2, c3, c4 = st.columns([5, 2, 3, 2])
    sel_name = c1.selectbox("Member", names, index=0 if names else 0)
    current = next((m for m in members if m["name"] == sel_name), None) if sel_name else None

    fetch_clicked = c2.button("🔄 Fetch", use_container_width=True)
    process_clicked = c3.button("🧠 Process with AI", use_container_width=True)
    force_refresh = c4.button("↻ Refresh", help="Ignore cache and refetch", use_container_width=True)

    # cache bucket for current member
    member_cache = get_member_cache()
    bucket = member_cache.setdefault(member_key(current) if current else "unknown", {})

    if current and (fetch_clicked or force_refresh):
        notion_api_key = settings.get("notion_api_key", "")
        page_id = current.get("notion_page_id", "")
        if not notion_api_key or not page_id:
            st.warning("Add Notion API key & Notion page ID in Settings.")
        else:
            last_edited_now = fetch_page_last_edited(notion_api_key, page_id)
            changed = (force_refresh or last_edited_now != bucket.get("last_edited"))
            if not changed and bucket.get("raw_data"):
                st.success("✅ Using cached Notion data (no changes detected).")
            else:
                prog = st.empty()
                prog.info("Fetching toggle blocks…")
                toggles = get_all_toggle_blocks(notion_api_key, page_id, prog)
                if toggles:
                    raw_list = process_toggle_blocks(notion_api_key, toggles, prog)
                    bucket["raw_data"] = "\n".join(raw_list)
                    bucket["last_edited"] = last_edited_now
                    st.success("✅ Notion data fetched.")
                else:
                    st.error("No toggle blocks found.")
                prog.empty()

    # Process phase
    if current and process_clicked:
        groq_api = settings.get("groq_api_key", "")
        if not bucket.get("raw_data"):
            st.warning("Fetch data first.")
        elif not groq_api:
            st.error("Add Groq API key in Settings.")
        else:
            out = process_with_groq(bucket["raw_data"], groq_api, st.session_state.get("temp", 0.5))
            if out:
                bucket["segmented"] = out
                st.success("✅ AI processed and cached.")

# Temperature (optional)
st.session_state.temp = st.slider("Temperature", 0.0, 1.0, st.session_state.get("temp", 0.5))

# Build weeks list from cached segmented data
def weeks_from_bucket(b):
    if not b or "segmented" not in b: return []
    sd = b["segmented"]
    if isinstance(sd, dict) and "weeks" in sd: return sd["weeks"]
    if isinstance(sd, list): return sd
    return [sd]

weeks = weeks_from_bucket(member_cache.get(member_key(current), {}))
week_titles = [w.get("week", f"Week {i+1}") for i, w in enumerate(weeks)] or ["— no AI weeks yet —"]

wk_state_key = f"selected_week::{member_key(current)}"

# Choose a sensible default for this member
default_title = (week_titles[-1] if weeks else week_titles[0])
saved_title = st.session_state.get(wk_state_key, default_title)

# If the saved title isn't valid for this member, fall back to default
if saved_title not in week_titles:
    saved_title = default_title
    st.session_state[wk_state_key] = saved_title

st.markdown("<div style='text-align:center;font-weight:600;'>Week</div>", unsafe_allow_html=True)

if weeks:
    selected_week_title = st.selectbox(
        "Week of",
        week_titles,
        index=week_titles.index(saved_title),
        label_visibility="collapsed",
    )
    st.session_state[wk_state_key] = selected_week_title
    cur_idx = week_titles.index(selected_week_title)
    cur_week = weeks[cur_idx]
    prev_week = weeks[cur_idx - 1] if cur_idx > 0 else None
else:
    selected_week_title = st.selectbox(
        "Week of",
        week_titles,
        index=0,
        label_visibility="collapsed",
    )
    cur_idx = -1
    cur_week = None
    prev_week = None

# -----------------------------
# CSS for layout
# -----------------------------
st.markdown("""
    <style>
        .app-title { font-size: 32px; font-weight: 800; letter-spacing: 0.5px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .grid-cell { border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 16px; }
        .grid-title { font-weight: 800; font-size: 20px; margin-bottom: 10px; display:flex; gap:8px; align-items:center;}
        .scrollbox { max-height: 360px; overflow-y:auto; }
        .card { padding: 1rem 1.25rem; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
                background: rgba(255,255,255,0.03); margin-bottom: 1rem; width: 100%; box-sizing: border-box; }
        .card h4 { margin: 0 0 .5rem 0; font-weight: 700; }
        .stDownloadButton button { width: 100%; }
        .user-info { background: rgba(0,255,0,0.1); padding: 8px; border-radius: 8px; margin-bottom: 16px; }
        .grid-cell { position: relative; height: 500px; overflow: hidden; border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 16px; }
        .scrollbox { max-height: 400px; overflow-y: auto; padding-right: 6px; }
        .calendar-container { max-height: 400px; overflow-y: auto; }
        .calendar-selector-container { display: flex; flex-direction: column; gap: 10px; padding-bottom: 10px; }
        .stSelectbox, .stRadio { width: 100%; }
        .stButton button { width: 100%; padding: 10px 0; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------
# 2×2 Grid: Weekly Review | Weekly Goals | Daily Logs | Calendar
# -----------------------------
st.markdown("<div class='grid'>", unsafe_allow_html=True)

# Weekly Review Section
c1, c2 = st.columns(2)
with c1:
    review_panel = st.container(border=True)
    with review_panel:
        st.markdown("**📝 Weekly Review**")
        review_body = st.container(height=360, border=False)
        with review_body:
            if prev_week and prev_week.get("weekly_review", "").strip():
                st.write(prev_week["weekly_review"])
                st.caption("For previous week")
            elif cur_week:
                st.info("No review for the previous week.")
            else:
                st.info("Run Fetch → Process to populate.")

st.markdown("</div>", unsafe_allow_html=True)

# Weekly Goals Section
with c2:
    goals_panel = st.container(border=True)
    with goals_panel:
        st.markdown("**🎯 Weekly Goals**")
        goals_body = st.container(height=360, border=False)
        with goals_body:
            if cur_week:
                goals = cur_week.get("weekly_goals", [])
                if goals:
                    for g in goals:
                        st.write(f"• {g}")
                else:
                    st.write("No goals set.")
            else:
                st.info("Run Fetch → Process to populate.")

# Daily Logs Section
c3, c4 = st.columns(2)
with c3:
    logs_panel = st.container(border=True)
    with logs_panel:
        st.markdown("**📈 Daily Logs**")
        logs_body = st.container(height=360, border=False)
        with logs_body:
            if cur_week:
                logs = cur_week.get("daily_logs", {})
                if logs:
                    for day, acts in logs.items():
                        st.markdown(f"**{day}:**")
                        for a in acts:
                            st.write(f"• {a}")
                else:
                    st.write("No logs this week.")
            else:
                st.info("Run Fetch → Process to populate.")

# Calendar Section
with c4:
    cal_panel = st.container(border=True)
    with cal_panel:
        st.markdown("**📅 Calendar**")
        inner = st.container(height=460, border=False)
        with inner:
            cal_admin = TeamCalendarAdmin()
            if not cal_admin.service:
                st.info("Upload Google service account in Settings.")
            else:
                colA, colB = st.columns(2)
                with colA:
                    view_type = st.radio("View", ["Weekly View", "Monthly View"], index=0)
                    year = st.selectbox("Year", range(2024, 2027), index=1 if dt.now().year >= 2024 else 0)
                with colB:
                    month = st.selectbox("Month", range(1, 13), format_func=lambda x: dt(1, x, 1).strftime('%B'), index=dt.now().month - 1)
                    week_no = st.selectbox("Week of Month", range(1, 6), index=0) if view_type == "Weekly View" else None

                if st.button("🔍 Fetch Calendar Data", use_container_width=True):
                    if current:
                        if view_type == "Weekly View":
                            start_date, end_date = get_week_dates(year, month, week_no)
                        else:
                            start_date, end_date = get_month_dates(year, month)
                        
                        # Check if using default email
                        if current['calendar_id'] == "your.email@example.com":
                            st.warning("⚠️ Please update your team member email to use calendar features.")
                            st.info("💡 **How to fix:**\n1. Go to the **Team** section in the sidebar\n2. Click **✏️ Edit Member** under the selected member\n3. Update the **Email** field with your actual email address\n4. Click **Update Member**")
                            st.stop()
                        
                        with st.spinner("Reading calendar…"):
                            events = cal_admin.get_member_events(current["calendar_id"], start_date, end_date)
                        
                        if not events:
                            st.info("No events in this period.")
                        else:
                            by_day: Dict[datetime.date, list] = {}
                            for e in events:
                                # Handle both dateTime and date formats
                                start_info = e["start"]
                                if "dateTime" in start_info:
                                    # Event with specific time
                                    s = start_info["dateTime"]
                                    if s.endswith('Z'):
                                        day = datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).date()
                                    else:
                                        day = datetime.datetime.fromisoformat(s).date()
                                else:
                                    # All-day event
                                    s = start_info["date"]
                                    day = datetime.datetime.fromisoformat(s).date()
                                
                                by_day.setdefault(day, []).append(e)
                            
                            # Display events by day
                            for day in sorted(by_day.keys()):
                                st.markdown(f"**{day.strftime('%A, %b %d, %Y')}**")
                                for e in by_day[day]:
                                    # Get event details with better fallbacks
                                    title = e.get('summary') or e.get('title') or e.get('subject') or 'Untitled Event'
                                    start_info = e.get("start", {})
                                    
                                    if "dateTime" in start_info:
                                        # Event with specific time
                                        start_time = start_info["dateTime"]
                                        end_time = e.get("end", {}).get("dateTime", "")
                                        
                                        # Format times
                                        if start_time.endswith('Z'):
                                            start_dt = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                        else:
                                            start_dt = datetime.datetime.fromisoformat(start_time)
                                        
                                        if end_time:
                                            if end_time.endswith('Z'):
                                                end_dt = datetime.datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                                            else:
                                                end_dt = datetime.datetime.fromisoformat(end_time)
                                            time_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                                        else:
                                            time_str = start_dt.strftime('%H:%M')
                                        
                                        st.write(f"• **{title}** ({time_str})")
                                    else:
                                        # All-day event
                                        st.write(f"• **{title}** (All day)")
                                    
                                    # Show location if available
                                    location = e.get('location') or e.get('where')
                                    if location:
                                        st.write(f"  📍 {location}")
                                    
                                    # Show description if available
                                    description = e.get('description') or e.get('details') or e.get('notes')
                                    if description:
                                        # Clean up description (remove HTML tags if any)
                                        import re
                                        clean_desc = re.sub(r'<[^>]+>', '', str(description))
                                        clean_desc = clean_desc.strip()
                                        if clean_desc:
                                            desc = clean_desc[:100] + "..." if len(clean_desc) > 100 else clean_desc
                                            st.write(f"  📝 {desc}")
                                    
                                    # Debug info removed to match original app behavior

# -----------------------------
# Summary / Export - FIXED TABLE LAYOUT
# -----------------------------
bucket = member_cache.get(member_key(current), {})
sd = bucket.get("segmented")
if sd:
    if isinstance(sd, dict) and "weeks" in sd:
        weeks_data = sd["weeks"]
    elif isinstance(sd, list):
        weeks_data = sd
    else:
        weeks_data = [sd]

    if weeks_data:
        st.markdown("### 📊 Data Summary")
        
        # Create proper table data
        summary_data = []
        for w in weeks_data:
            summary_data.append({
                'Week': w.get('week', 'N/A'),
                'Goals Count': len(w.get('weekly_goals', [])),
                'Days Logged': len(w.get('daily_logs', {})),
                'Has Review': 'Yes' if w.get('weekly_review', '').strip() else 'No'
            })
        
        # Display as proper table using st.table
        if summary_data:
            # Convert to format that st.table can display
            table_data = []
            for row in summary_data:
                table_data.append([row['Week'], row['Goals Count'], row['Days Logged'], row['Has Review']])
            
            st.table({
                'Week': [row[0] for row in table_data],
                'Goals Count': [row[1] for row in table_data],
                'Days Logged': [row[2] for row in table_data],
                'Has Review': [row[3] for row in table_data]
            })

        exp_c1, exp_c2 = st.columns(2)
        with exp_c1:
            st.download_button(
                label="📄 Download JSON",
                data=json.dumps(weeks_data, indent=2),
                file_name=f"notion_data_{dt.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True
            )
        with exp_c2:
            # Create CSV manually without pandas
            csv_content = "Week,Weekly_Goals,Weekly_Review,Daily_Logs\n"
            for w in weeks_data:
                goals_str = '; '.join(w.get('weekly_goals', []))
                review_str = w.get('weekly_review', '').replace('\n', ' ').replace(',', ';')
                logs_str = json.dumps(w.get('daily_logs', {})).replace(',', ';')
                csv_content += f'"{w.get("week", "")}","{goals_str}","{review_str}","{logs_str}"\n'
            
            st.download_button(
                label="📊 Download CSV",
                data=csv_content,
                file_name=f"notion_data_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True
            )

# -----------------------------
# Sidebar: settings & team
# -----------------------------
with st.sidebar:
    st.header("⚙️ Settings & Team")
    
    # Data Management Section
    with st.expander("💾 Data Management", expanded=True):
        st.markdown("**Export/Import your data:**")
        
        # Export button
        if st.button("📤 Export All Data", use_container_width=True):
            export_data = export_user_data()
            st.download_button(
                label="💾 Download Backup",
                data=export_data,
                file_name=f"hive_backup_{user_id}_{dt.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True
            )
        
        # Import section - FIXED to prevent refresh issues
        st.markdown("**Import Data:**")
        uploaded_file = st.file_uploader("📥 Upload backup file", type=['json'], help="Upload a previously exported backup file", key="import_file")
        
        # Only process import when file is uploaded and not already processed
        if uploaded_file is not None and not st.session_state.get('file_processed', False):
            success, message = import_user_data(uploaded_file)
            if success:
                st.success(message)
                st.session_state.file_processed = True
                st.rerun()
            else:
                st.error(message)
        
        # Reset file processed flag when no file is uploaded
        if uploaded_file is None:
            st.session_state.file_processed = False
        
        # Clear data button
        if st.button("🗑️ Clear All Data", use_container_width=True):
            if st.session_state.get('confirm_clear', False):
                clear_user_data()
                st.success("All data cleared!")
                st.session_state.confirm_clear = False
                st.rerun()
            else:
                st.session_state.confirm_clear = True
                st.warning("Click again to confirm clearing all data")
    
    with st.expander("🔑 API Keys & Settings", expanded=True):
        s = get_user_data('settings', {"notion_api_key": "", "groq_api_key": ""})
        with st.form("settings_form_sidebar"):
            notion_api = st.text_input("Notion API Key", value=s.get("notion_api_key", ""), type="password")
            groq_api = st.text_input("Groq LLM API Key", value=s.get("groq_api_key", ""), type="password")
            
            # Google Service Account Upload
            st.markdown("**Google Service Account:**")
            service_upload = st.file_uploader("Upload Service Account JSON", type=["json"], key="service_upload")
            
            if st.form_submit_button("💾 Save"):
                s["notion_api_key"] = notion_api.strip()
                s["groq_api_key"] = groq_api.strip()
                set_user_data('settings', s)
                
                # Handle service account upload
                if service_upload is not None:
                    try:
                        content = service_upload.read()
                        service_data = json.loads(content)
                        set_user_data('service_credentials', service_data)
                        st.success("Service account credentials saved!")
                    except Exception as e:
                        st.error(f"Invalid service account file: {str(e)}")
                
                st.success("Settings saved.")

    with st.expander("👥 Team", expanded=True):
        if current:
            st.markdown(f"**Selected:** {current.get('name','')} — {current.get('role','')}")
            st.caption(f"Email: {current.get('email', 'Not set')}")
            
            # Edit current member - using form instead of nested expander
            if st.button("✏️ Edit Member", use_container_width=True):
                st.session_state.edit_mode = True
            
            if st.session_state.get('edit_mode', False):
                st.markdown("**Edit Member:**")
                with st.form("edit_member_form"):
                    edit_name = st.text_input("Full Name", value=current.get('name', ''))
                    edit_email = st.text_input("Email", value=current.get('email', ''))
                    col1, col2 = st.columns(2)
                    with col1:
                        edit_role = st.text_input("Role", value=current.get('role', ''))
                    with col2:
                        edit_department = st.text_input("Department", value=current.get('department', ''))
                    edit_notion_page_id = st.text_input("Notion Page ID", value=current.get('notion_page_id', ''))
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.form_submit_button("Update Member"):
                            if edit_name.strip() and edit_email.strip():
                                team = get_user_data('team_members', {"team_members": []})
                                # Update the member
                                for i, member in enumerate(team["team_members"]):
                                    if member["name"] == current["name"]:
                                        team["team_members"][i] = {
                                            "name": edit_name.strip(),
                                            "email": edit_email.strip(),
                                            "calendar_id": edit_email.strip(),
                                            "role": edit_role.strip(),
                                            "department": edit_department.strip(),
                                            "notion_page_id": edit_notion_page_id.strip(),
                                        }
                                        break
                                set_user_data('team_members', team)
                                st.session_state.edit_mode = False
                                st.success(f"Updated {edit_name}.")
                                safe_rerun()
                            else:
                                st.error("Name and Email are required!")
                    with col2:
                        if st.form_submit_button("Cancel"):
                            st.session_state.edit_mode = False
                            st.rerun()
            
            if st.button("🗑️ Delete selected member", use_container_width=True):
                team = get_user_data('team_members', {"team_members": []})
                team["team_members"] = [m for m in team["team_members"] if m["name"] != current["name"]]
                set_user_data('team_members', team)
                st.success("Deleted. Reloading…")
                safe_rerun()

        # Add Member section - always visible
        st.markdown("---")
        st.markdown("**➕ Add New Member**")
        with st.form("add_member_form_sidebar"):
            name = st.text_input("Full Name", placeholder="Enter full name")
            email = st.text_input("Email", placeholder="Enter email address")
            col1, col2 = st.columns(2)
            with col1:
                role = st.text_input("Role", placeholder="e.g., Developer")
            with col2:
                department = st.text_input("Department", placeholder="e.g., Engineering")
            notion_page_id = st.text_input("Notion Page ID", placeholder="Optional: Notion page ID")

            if st.form_submit_button("➕ Add Member", use_container_width=True):
                if name.strip() and email.strip():
                    team = get_user_data('team_members', {"team_members": []})
                    new_member = {
                        "name": name.strip(),
                        "email": email.strip(),
                        "calendar_id": email.strip(),
                        "role": role.strip(),
                        "department": department.strip(),
                        "notion_page_id": notion_page_id.strip(),
                    }
                    team["team_members"].append(new_member)
                    set_user_data('team_members', team)
                    st.success(f"✅ Added {name} successfully!")
                    safe_rerun()
                else:
                    st.error("❌ Name and Email are required!")

# Add logout functionality
if st.session_state.get('confirm_clear', False):
    st.session_state.confirm_clear = False
