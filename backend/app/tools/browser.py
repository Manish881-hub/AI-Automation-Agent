from dataclasses import dataclass, field
from pathlib import Path
from playwright.async_api import async_playwright, Page, ConsoleMessage, Response


@dataclass
class BrowserObservation:
    url: str = ""
    title: str = ""
    text: str = ""
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)
    screenshot_path: str | None = None


class BrowserTool:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page: Page | None = None
        self.console_errors: list[str] = []
        self.network_errors: list[str] = []

    async def start(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 1440, "height": 900})
        self.page.on("console", self._on_console)
        self.page.on("response", self._on_response)

    async def _on_console(self, msg: ConsoleMessage):
        if msg.type == "error":
            self.console_errors.append(msg.text)

    async def _on_response(self, response: Response):
        if response.status >= 400:
            self.network_errors.append(f"{response.status} {response.request.method} {response.url}")

    async def navigate(self, url: str):
        assert self.page
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

    async def _locator(self, target: str, action: str = "click"):
        """Resolve a SEMANTIC target into a deterministic Playwright locator.

        Strategy order (LLM produces semantic targets, adapter resolves):
          role -> label -> text -> placeholder -> css fallback.
        Fill steps prefer label/textbox-role/placeholder (form semantics);
        click/press prefer button/link roles first. Only the final fallback
        treats the target as a raw CSS selector.
        """
        assert self.page
        if action == "fill":
            candidates = [
                self.page.get_by_label(target, exact=True),
                self.page.get_by_role("textbox", name=target, exact=True),
                self.page.get_by_role("searchbox", name=target, exact=True),
                self.page.get_by_role("combobox", name=target, exact=True),
                self.page.get_by_placeholder(target, exact=True),
                self.page.get_by_text(target, exact=True),
            ]
        else:
            candidates = [
                self.page.get_by_role("button", name=target, exact=True),
                self.page.get_by_role("link", name=target, exact=True),
                self.page.get_by_label(target, exact=True),
                self.page.get_by_text(target, exact=True),
                self.page.get_by_placeholder(target, exact=True),
            ]
        for locator in candidates:
            try:
                if await locator.first.is_visible(timeout=1000):
                    return locator.first
            except Exception:
                pass
        # CSS fallback only — the model should not need to produce selectors.
        return self.page.locator(target).first

    async def ensure_visible(self, target: str) -> bool:
        """True when a semantic target resolves to a visible element."""
        assert self.page
        try:
            locator = await self._locator(target, action="assert")
            return await locator.is_visible(timeout=3000)
        except Exception:
            return False

    async def click(self, target: str):
        locator = await self._locator(target, action="click")
        await locator.click(timeout=10000)

    async def fill(self, target: str, value: str):
        locator = await self._locator(target, action="fill")
        await locator.fill(value, timeout=10000)

    async def press(self, target: str, key: str):
        locator = await self._locator(target, action="press")
        await locator.press(key, timeout=10000)

    async def observe(self, screenshot_path: str | None = None) -> BrowserObservation:
        assert self.page
        if screenshot_path:
            await self.page.screenshot(path=screenshot_path, full_page=True)
        text = (await self.page.locator("body").inner_text())[:12000]
        return BrowserObservation(
            url=self.page.url,
            title=await self.page.title(),
            text=text,
            console_errors=self.console_errors[-20:],
            network_errors=self.network_errors[-20:],
            screenshot_path=screenshot_path,
        )

    async def _extract_interactives(self) -> dict:
        """Return visible interactive elements only — never the raw DOM.

        Caps keep LLM context small: 15 headings, 25 links/buttons,
        20 inputs, 10 forms. Hidden elements are excluded so the planner
        only targets what a user could actually touch.
        """
        assert self.page
        return await self.page.evaluate(
            """() => {
              const visible = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 &&
                  getComputedStyle(el).visibility !== 'hidden';
              };
              const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
              const labelOf = (el) => {
                if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
                const id = el.id;
                if (id) {
                  const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                  if (lab) return txt(lab);
                }
                const wrap = el.closest('label');
                if (wrap) return txt(wrap);
                return '';
              };
              const take = (arr, n) => arr.slice(0, n);
              const headings = take(
                [...document.querySelectorAll('h1, h2, h3')]
                  .filter(visible).map(txt).filter(Boolean), 15);
              const links = take(
                [...document.querySelectorAll('a[href]')]
                  .filter(visible)
                  .map(a => ({text: txt(a), href: a.getAttribute('href') || ''}))
                  .filter(l => l.text), 25);
              const buttons = take(
                [...document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]')]
                  .filter(visible)
                  .map(el => txt(el) || el.getAttribute('value') || el.getAttribute('aria-label') || '')
                  .map(s => s.trim()).filter(Boolean), 25);
              const inputs = take(
                [...document.querySelectorAll('input, textarea, select')]
                  .filter(el => visible(el) && !['hidden', 'submit', 'button'].includes(el.type))
                  .map(el => ({
                    label: labelOf(el),
                    name: el.name || '',
                    type: el.type || el.tagName.toLowerCase(),
                    placeholder: el.getAttribute('placeholder') || '',
                  })), 20);
              const forms = take(
                [...document.querySelectorAll('form')]
                  .map(f => ({
                    action: f.getAttribute('action') || '',
                    fields: [...f.querySelectorAll('input, textarea, select')]
                      .map(el => el.name || el.id || el.type).filter(Boolean),
                  })), 10);
              return {headings, links, buttons, inputs, forms};
            }"""
        )

    async def available_targets(self) -> dict:
        """Lightweight listing for the recovery agent: what exists to click/fill."""
        data = await self._extract_interactives()
        return {
            "buttons": data.get("buttons", []),
            "links": [l.get("text", "") for l in data.get("links", [])],
            "inputs": [
                i.get("label") or i.get("placeholder") or i.get("name", "")
                for i in data.get("inputs", [])
            ],
        }

    async def snapshot(self, screenshot_path: str | None = None):
        """Full compact page model for reconnaissance + planning."""
        from ..schemas import WebsiteSnapshot

        assert self.page
        data = await self._extract_interactives()
        if screenshot_path:
            await self.page.screenshot(path=screenshot_path, full_page=True)
        text = (await self.page.locator("body").inner_text())[:4000]
        return WebsiteSnapshot(
            url=self.page.url,
            title=await self.page.title(),
            headings=data.get("headings", []),
            links=data.get("links", []),
            buttons=data.get("buttons", []),
            inputs=data.get("inputs", []),
            forms=data.get("forms", []),
            visible_text=text,
            screenshot=screenshot_path,
        )

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
