"""Ephemeral Playwright fallback for the locked QAS WebUI contract."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..command import CommandRunner
from ..errors import DeploymentError
from .qas_v1 import QasDesiredState


@dataclass(frozen=True)
class BrowserResult:
    status: str
    next_action: str
    details: dict[str, object]


_BROWSER_SCRIPT = r'''
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
base_url = sys.argv[1]
state = json.loads(Path('/work/state.json').read_text(encoding='utf-8'))
result = {'status': 'manual_action_required', 'nextAction': 'open_qas_webui', 'details': {}}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(base_url, wait_until='domcontentloaded', timeout=30000)
    text = page.locator('body').inner_text().casefold()
    if page.locator('input[name="username"], input[name="password"]').count() or any(term in text for term in ('二维码', '扫码', 'captcha', '验证码')):
        result['details'] = {'gate': 'login_or_identity_challenge'}
    else:
        selectors = {
            'aria2HostPort': '[data-qas-field="aria2-host-port"]',
            'aria2Dir': '[data-qas-field="aria2-dir"]',
        }
        for key, selector in selectors.items():
            if page.locator(selector).count():
                page.locator(selector).fill(str(state[key]))
        save = page.locator('[data-qas-action="save"]')
        if save.count():
            save.click()
            result = {'status': 'ready', 'nextAction': 'verify_qas', 'details': {'submitted': True}}
        else:
            result['details'] = {'gate': 'unsupported_ui_contract'}
    browser.close()
print(json.dumps(result, separators=(',', ':')))
'''


class QasBrowserFallback:
    def __init__(self, playwright_image: str) -> None:
        self.playwright_image = playwright_image

    def run(
        self,
        base_url: str,
        desired_state: QasDesiredState,
        runner: CommandRunner,
    ) -> BrowserResult:
        with tempfile.TemporaryDirectory(prefix="qas-browser-") as temp:
            root = Path(temp)
            script = root / "run.py"
            state = root / "state.json"
            script.write_text(_BROWSER_SCRIPT, encoding="utf-8")
            state.write_text(
                json.dumps(
                    {
                        "aria2HostPort": desired_state.aria2_host_port,
                        "aria2Dir": desired_state.aria2_dir,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.chmod(script, 0o600)
            os.chmod(state, 0o600)
            result = runner.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "openclaw-media",
                    "--mount",
                    f"type=bind,src={script},dst=/work/run.py,readonly",
                    "--mount",
                    f"type=bind,src={state},dst=/work/state.json,readonly",
                    self.playwright_image,
                    "python",
                    "/work/run.py",
                    str(base_url),
                ],
                timeout=90,
            )
        if result.returncode != 0:
            raise DeploymentError(
                "QAS_BROWSER_FAILED",
                "QAS browser fallback failed",
                status="manual_action_required",
                next_action="open_qas_webui",
                details={"returncode": result.returncode, "stderr": result.stderr[:300]},
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise DeploymentError(
                "QAS_BROWSER_OUTPUT_INVALID",
                "QAS browser fallback returned invalid output",
                status="manual_action_required",
                next_action="open_qas_webui",
            ) from None
        return BrowserResult(
            status=str(payload.get("status", "manual_action_required")),
            next_action=str(payload.get("nextAction", "open_qas_webui")),
            details=dict(payload.get("details", {})),
        )
