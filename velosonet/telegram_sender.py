from __future__ import annotations
import asyncio, logging, os
import httpx

log = logging.getLogger(__name__)
DEST = "telegram"

async def send_photo(db, run_id, png_path, caption, retries=3) -> bool:
    if db.delivered(run_id, DEST):
        log.info("run %s já entregue; ignorando reenvio", run_id); return True
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        db.add_delivery(run_id, DEST, 0, False, "TELEGRAM_BOT_TOKEN/CHAT_ID ausentes"); return False
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with httpx.AsyncClient(timeout=60) as cli:
        for n in range(1, retries + 1):
            try:
                with open(png_path, "rb") as fh:
                    r = await cli.post(url, data={"chat_id": chat, "caption": caption[:1024]},
                                       files={"photo": ("relatorio.png", fh, "image/png")})
                if r.status_code == 200:
                    db.add_delivery(run_id, DEST, n, True, "ok"); return True
                detail = f"HTTP {r.status_code}"
                wait = r.json().get("parameters", {}).get("retry_after", 0) if r.status_code == 429 else 0
            except httpx.HTTPError as e:
                detail, wait = type(e).__name__, 0
            db.add_delivery(run_id, DEST, n, False, detail)
            log.warning("Telegram tentativa %d/%d falhou: %s", n, retries, detail)
            if n < retries: await asyncio.sleep(max(wait, 5 * 3 ** (n - 1)))
    return False
