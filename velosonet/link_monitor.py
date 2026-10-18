import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright
from .config import ROOT, load_settings, setup_logging

log = logging.getLogger(__name__)
PANELS = (
    ("tefe", "http://45.190.21.181:18080/#tefe", r"^Starlinks Tef[eé]$"),
    ("tabatinga", "http://45.190.21.181:18080/#tabatinga", r"^Links Tabatinga$"),
)

async def capture_link_reports(browser, settings, timestamp):
    paths = []
    if not settings.get("links_monitor_enabled", True):
        return paths
    output = ROOT / "data" / "reports"
    output.mkdir(parents=True, exist_ok=True)
    for city, url, heading in PANELS:
        context = None
        try:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=2,
            )
            page = await context.new_page()
            page.set_default_timeout(45000)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.get_by_text(re.compile(heading, re.I)).wait_for(state="visible")
            await page.get_by_text(
                re.compile(r"Atualizado:\s*\d{2}/\d{2}/\d{4}.*\d{2}:\d{2}:\d{2}")
            ).first.wait_for(state="visible")
            await page.get_by_text(
                re.compile(r"\d+(?:[.,]\d+)?\s*Mbps", re.I)
            ).first.wait_for(state="visible")
            await page.evaluate("document.fonts.ready")
            await page.wait_for_timeout(2000)
            path = output / f"{timestamp:%Y%m%d_%H%M}_{city}.png"
            if city == "tabatinga":
                height = await page.evaluate("""() => {
                    let bottom = 0;
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT
                    );
                    let node;
                    while (node = walker.nextNode()) {
                        const parent = node.parentElement;
                        if (!parent || !node.textContent.trim()) continue;
                        if (parent.closest('script,style,noscript')) continue;
                        const style = getComputedStyle(parent);
                        if (style.visibility === 'hidden' || style.display === 'none')
                            continue;
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        for (const rect of range.getClientRects()) {
                            if (rect.width > 0 && rect.height > 0 && rect.right > 0)
                                bottom = Math.max(bottom, rect.bottom + scrollY);
                        }
                    }
                    for (const el of document.querySelectorAll('canvas,svg,img')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.right > 0)
                            bottom = Math.max(bottom, rect.bottom + scrollY);
                    }
                    return Math.min(
                        document.documentElement.scrollHeight,
                        Math.max(200, Math.ceil(bottom + 24))
                    );
                }""")
                await page.screenshot(
                    path=str(path),
                    clip={"x": 0, "y": 0, "width": 1920, "height": height},
                    animations="disabled",
                )
            else:
                await page.screenshot(
                    path=str(path), full_page=True, animations="disabled"
                )
            paths.append(path)
            log.info("Relatorio de links %s salvo em %s", city, path)
        except Exception as exc:
            log.warning("Captura de %s falhou: %s", city, exc)
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    log.warning("Falha ao fechar contexto de %s", city)
    return paths

async def main():
    setup_logging()
    settings = load_settings()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            paths = await capture_link_reports(
                browser, settings, datetime.now(ZoneInfo(settings["timezone"]))
            )
            return 0 if len(paths) == 2 else 1
        finally:
            await browser.close()

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
