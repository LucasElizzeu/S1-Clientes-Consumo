"""Sessão web autenticada de uma BBU (Playwright async). Um lock por BBU impede keep-alive e coleta simultâneos.
Fluxo (conforme capturas reais): login -> aviso "Yes" -> menu MML -> comando -> Assist -> Exec -> ler resposta."""
from __future__ import annotations
import asyncio, logging, re, time
from pathlib import Path
from playwright.async_api import Error as PwError

from .errors import BbuUnavailable, SelectorsNotConfigured, UnsafeCommand, ConfigError, MmlIncomplete
from .mml_parser import END_RE

log = logging.getLogger(__name__)
REQUIRED = ["username", "password", "login_button", "logged_in_indicator",
            "mml_menu", "mml_command_input", "mml_execute_button", "mml_output"]
SAFE_CMD = re.compile(r"^\s*(DSP|LST)\s+[A-Z0-9_]+[^;\n]*;?\s*$", re.I)   # somente leitura

def assert_read_only(cmd: str):
    if not SAFE_CMD.match(cmd):
        raise UnsafeCommand(f"comando bloqueado (apenas DSP/LST): {cmd!r}")

def slice_response(text: str, cmd: str) -> str | None:
    """Recorta a última resposta do comando: do '+++' que antecede '%%CMD:;%%' até o fim."""
    marker = f"%%{cmd.strip().rstrip(';')}:;%%"
    idx = text.rfind(marker)
    if idx < 0: return None
    start = text.rfind("+++", 0, idx)
    return text[start if start >= 0 else idx:]

class BbuSession:
    def __init__(self, bbu, settings, browser):
        self.bbu, self.s, self.browser = bbu, settings, browser
        self.lock = asyncio.Lock()
        self.ctx = self.page = None
        self.mml_ready = False
        self.last_activity = self.login_time = 0.0
        self.blocked_until = 0.0
        self.trace_dir: Path | None = None

    # ---------- infraestrutura ----------
    def _sel(self):
        miss = [k for k in REQUIRED if not self.bbu.selectors.get(k)]
        if not self.bbu.url: miss.insert(0, "url")
        if miss:
            raise SelectorsNotConfigured(f"BBU {self.bbu.id}: configure em bbus.yaml: {', '.join(miss)}")
        return self.bbu.selectors

    async def _loc(self, sel, visible=True):
        """Procura o seletor na página e em todos os iframes."""
        for fr in self.page.frames:
            try:
                loc = fr.locator(sel).first
                if await loc.count() and (not visible or await loc.is_visible()):
                    return loc
            except PwError:
                continue
        return None

    async def _wait(self, sel, timeout_s=None):
        end = time.monotonic() + (timeout_s or self.s["page_timeout_s"])
        while time.monotonic() < end:
            loc = await self._loc(sel)
            if loc: return loc
            await asyncio.sleep(0.4)
        raise PwError(f"timeout aguardando seletor {sel!r}")

    async def _click(self, sel, timeout_s=None):
        await (await self._wait(sel, timeout_s)).click(timeout=(timeout_s or self.s["page_timeout_s"]) * 1000)

    async def _all_text(self) -> str:
        parts = []
        for fr in self.page.frames:
            try: parts.append(await fr.locator("body").inner_text(timeout=5000))
            except PwError: pass
        return "\n".join(parts)

    async def _trace(self, name):
        """Se trace_dir estiver definido, salva screenshot e HTML de cada frame (para ajustar seletores)."""
        if not self.trace_dir or not self.page: return
        d = self.trace_dir; d.mkdir(parents=True, exist_ok=True)
        base = f"bbu{self.bbu.id}_{name}"
        try: await self.page.screenshot(path=str(d / f"{base}.png"))
        except PwError: pass
        for i, fr in enumerate(self.page.frames):
            try: (d / f"{base}_frame{i}.html").write_text(await fr.content(), encoding="utf-8")
            except PwError: pass
        try: title = await self.page.title()
        except PwError: title = "?"
        info = [f"url_atual={self.page.url}", f"titulo={title}", f"verify_tls={self.bbu.verify_tls}"] + getattr(self, "_diag", [])[:30]
        (d / f"{base}_info.txt").write_text("\n".join(info), encoding="utf-8")

    async def _close(self):
        try:
            if self.ctx: await self.ctx.close()
        except PwError: pass
        self.ctx = self.page = None; self.mml_ready = False

    async def _expired(self) -> bool:
        if self.page is None: return True
        if time.monotonic() - self.login_time > self.s.get("session_max_age_min", 450) * 60: return True
        sel = self.bbu.selectors
        try:
            if sel.get("login_form_indicator") and await self._loc(sel["login_form_indicator"]): return True
            return await self._loc(sel["logged_in_indicator"]) is None
        except PwError:
            return True

    async def _login(self):
        sel = self._sel()
        user, pwd = self.bbu.credentials()
        if not self.bbu.verify_tls:
            log.warning("BBU %s: verify_tls=false (use apenas temporariamente)", self.bbu.id)
        await self._close()
        self.ctx = await self.browser.new_context(ignore_https_errors=not self.bbu.verify_tls)
        self.page = await self.ctx.new_page()
        self.page.set_default_timeout(self.s["page_timeout_s"] * 1000)
        self._diag = []
        self.page.on("requestfailed", lambda r: self._diag.append(f"requestfailed {r.url} {r.failure}"))
        self.page.on("console", lambda m: self._diag.append(f"console.{m.type} {m.text[:200]}") if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self._diag.append(f"pageerror {str(e)[:200]}"))
        try:
            await self.page.goto(self.bbu.url, wait_until="domcontentloaded")
            await (await self._wait(sel["password"])).wait_for(state="visible")
            await self._trace("1_login_page")
            await (await self._wait(sel["username"])).fill(user)
            await (await self._wait(sel["password"])).fill(pwd)
            await self._click(sel["login_button"])
            if sel.get("confirm_yes"):                      # aviso "Only authorized users..." -> Yes
                try: await self._click(sel["confirm_yes"], timeout_s=15)
                except PwError: log.info("BBU %s: aviso de confirmação não apareceu", self.bbu.id)
            await self._wait(sel["logged_in_indicator"])
            await self._trace("2_logged_in")
        except PwError as e:
            detail = (str(e).strip().splitlines() or [""])[0][:220]
            await self._trace("error_login"); await self._close()
            raise BbuUnavailable(f"login falhou: {type(e).__name__}: {detail}") from None
        self.login_time = self.last_activity = time.monotonic()
        log.info("BBU %s: login OK", self.bbu.id)

    async def ensure_session(self):
        self._sel()
        if time.monotonic() < self.blocked_until:
            raise BbuUnavailable("em pausa após falhas consecutivas de login")
        if not await self._expired():
            return
        last = None
        for n in range(1, self.s["login_max_retries"] + 1):
            try:
                await self._login(); return
            except BbuUnavailable as e:
                last = e; log.warning("BBU %s: tentativa de login %d/%d falhou: %s", self.bbu.id, n, self.s["login_max_retries"], e)
                await asyncio.sleep(min(5 * n, 20))
        self.blocked_until = time.monotonic() + self.s["login_cooldown_min"] * 60
        raise last or BbuUnavailable("login indisponível")

    async def _open_mml(self):
        if self.mml_ready: return
        sel = self.bbu.selectors
        try:
            await self._click(sel["mml_menu"])
            await self._wait(sel["mml_command_input"])
            await self._trace("3_mml_open")
        except PwError as e:
            await self._trace("error_mml_open")
            raise BbuUnavailable(f"menu MML indisponível ({type(e).__name__})") from None
        self.mml_ready = True

    # ---------- operações ----------
    async def _run_locked(self, cmd: str) -> str:
        assert_read_only(cmd)
        await self.ensure_session()
        await self._open_mml()
        sel = self.bbu.selectors
        try:
            before = await self._all_text()
            await (await self._wait(sel["mml_command_input"])).fill(cmd)
            if sel.get("mml_assist_button"):                # Assist libera o botão Exec
                await self._click(sel["mml_assist_button"])
            await self._click(sel["mml_execute_button"])
            deadline = time.monotonic() + self.s["mml_response_timeout_s"]
            while True:
                text = await self._all_text()
                resp = slice_response(text, cmd)
                if resp and text != before and END_RE.search(resp):
                    break
                if time.monotonic() > deadline:
                    await self._trace("error_timeout")
                    raise MmlIncomplete("timeout aguardando resposta MML completa")
                await asyncio.sleep(0.5)
            await self._trace("4_response")
        except PwError as e:
            self.mml_ready = False
            await self._trace("error_mml")
            raise BbuUnavailable(f"falha na interface MML ({type(e).__name__})") from None
        self.last_activity = time.monotonic()
        return resp

    async def run_mml(self, cmd: str) -> str:
        async with self.lock:
            return await self._run_locked(cmd)

    async def keepalive(self, force=False) -> str:
        """Rotina A: só age se ocioso; nunca concorre com coleta (lock)."""
        if not self.bbu.keepalive and not force: return "disabled"
        if self.lock.locked(): return "busy"
        idle = time.monotonic() - self.last_activity
        if not force and idle < self.s["keepalive_interval_min"] * 60 * 0.9 and not await self._expired():
            return "recent"
        async with self.lock:
            try:
                await self._run_locked(self.s["commands"]["keepalive"])
                return "sent"
            except ConfigError:
                raise
            except Exception as e:
                self.mml_ready = False
                log.warning("BBU %s: keep-alive falhou: %s", self.bbu.id, type(e).__name__)
                return f"failed:{type(e).__name__}"

    async def snapshot_login_page(self, out_dir):
        if not self.bbu.url: raise ConfigError(f"BBU {self.bbu.id}: url não configurada")
        ctx = await self.browser.new_context(ignore_https_errors=not self.bbu.verify_tls)
        pg = await ctx.new_page(); pg.set_default_timeout(self.s["page_timeout_s"] * 1000)
        await pg.goto(self.bbu.url, wait_until="networkidle")
        await pg.screenshot(path=str(out_dir / f"bbu{self.bbu.id}_login.png"), full_page=True)
        for i, fr in enumerate(pg.frames):
            (out_dir / f"bbu{self.bbu.id}_login_frame{i}.html").write_text(await fr.content(), encoding="utf-8")
        await ctx.close()

    async def close(self):
        await self._close()
