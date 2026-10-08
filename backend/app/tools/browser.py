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

    async def _locator(self, target: str):
        assert self.page
        # Try accessible text/role-like strategies before CSS.
        locators = [
            self.page.get_by_role("button", name=target, exact=True),
            self.page.get_by_role("link", name=target, exact=True),
            self.page.get_by_label(target, exact=True),
            self.page.get_by_placeholder(target, exact=True),
            self.page.get_by_text(target, exact=True),
            self.page.locator(target),
        ]
        for locator in locators:
            try:
                if await locator.first.is_visible(timeout=1000):
                    return locator.first
            except Exception:
                pass
        return self.page.locator(target).first

    async def click(self, target: str):
        locator = await self._locator(target)
        await locator.click(timeout=10000)

    async def fill(self, target: str, value: str):
        locator = await self._locator(target)
        await locator.fill(value, timeout=10000)

    async def press(self, target: str, key: str):
        locator = await self._locator(target)
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

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
