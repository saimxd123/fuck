"""
Daily file backup — KIORA AUTOSHOPI
Every 24h at 00:00 UTC:
  1. Zips all data/stats .txt and .json files
  2. Sends the zip to both admins (DM)
  3. Pins the message in the DM chat
  4. Prunes local copies older than RETENTION_DAYS

No PostgreSQL / pg_dump required. File-only backup.
Dev: OGGY (@OggyProo)
"""

import os
import io
import asyncio
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
BACKUP_DIR     = Path(__file__).resolve().parent / "backups"
RETENTION_DAYS = 7
CATCHUP_AFTER_H = 20

ADMIN_IDS = [8919487892, 8757857854]

BACKUP_FILES = [
    "api_endpoints.json",
    "sites.txt",
    "mshh.txt",
    "mst.txt",
    "mchk.txt",
    "mrz.txt",
    "msk.txt",
    "mpp.txt",
]

# ─────────────────────────────────────────────────────────────────────────────

def _make_zip_bytes() -> tuple[bytes, list[str]]:
    """Build the zip in memory, return (zip_bytes, included_filenames)."""
    buf = io.BytesIO()
    included = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in BACKUP_FILES:
            p = Path(name)
            if p.exists() and p.is_file():
                zf.write(p, arcname=name)
                included.append(name)
    return buf.getvalue(), included


def _prune():
    removed = 0
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    for f in BACKUP_DIR.glob("kiora_backup_*.zip"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    return removed


def _latest_age_hours() -> float:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    newest = 0.0
    for f in BACKUP_DIR.glob("kiora_backup_*.zip"):
        try:
            newest = max(newest, f.stat().st_mtime)
        except Exception:
            pass
    if not newest:
        return 1e9
    return (datetime.now() - datetime.fromtimestamp(newest)).total_seconds() / 3600


async def run_backup(bot, reason: str = "scheduled"):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp    = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"kiora_backup_{stamp}.zip"
    local    = BACKUP_DIR / filename

    logger.info(f"[Backup] Starting ({reason}) …")

    zip_bytes, included = await asyncio.to_thread(_make_zip_bytes)

    # Save local copy
    local.write_bytes(zip_bytes)

    now_str   = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    file_list = "\n".join(f"• <code>{n}</code>" for n in included) or "• (no data files found)"
    caption   = (
        "<b>📦 𝑲𝑰𝑶𝑹𝑨 𝑨𝑼𝑻𝑶𝑺𝑯𝑶𝑷𝑰 — Daily Backup</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"📅 <b>Date:</b> {now_str}\n"
        f"📁 <b>Files:</b>\n{file_list}\n"
        "━━━━━━━━━━━━━━━\n"
        f"<b>Reason:</b> <code>{reason}</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            doc = BufferedInputFile(zip_bytes, filename=filename)
            msg = await bot.send_document(
                chat_id=admin_id,
                document=doc,
                caption=caption,
                parse_mode="HTML",
            )
            # Pin in the DM chat
            try:
                await bot.pin_chat_message(
                    chat_id=admin_id,
                    message_id=msg.message_id,
                    disable_notification=True,
                )
            except Exception as e:
                logger.warning(f"[Backup] Could not pin for {admin_id}: {e}")
        except Exception as e:
            logger.error(f"[Backup] Could not send to {admin_id}: {e}")

    removed = await asyncio.to_thread(_prune)
    if removed:
        logger.info(f"[Backup] Pruned {removed} old file(s)")
    logger.info("[Backup] Done ✅")


def _seconds_until_midnight() -> float:
    now      = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (tomorrow - now).total_seconds())


async def daily_backup_scheduler(bot):
    """Catch-up on boot if needed, then run every 24h at 00:00."""
    try:
        age = await asyncio.to_thread(_latest_age_hours)
        if age > CATCHUP_AFTER_H:
            logger.info(f"[Backup] Last backup {age:.0f}h ago — catch-up now.")
            await run_backup(bot, reason="catch-up")
    except Exception as e:
        logger.error(f"[Backup] catch-up error: {e}")

    while True:
        wait = _seconds_until_midnight()
        logger.info(f"[Backup] Next run in {wait/3600:.2f}h")
        await asyncio.sleep(wait)
        try:
            await run_backup(bot, reason="scheduled")
        except Exception as e:
            logger.error(f"[Backup] scheduled error: {e}")
