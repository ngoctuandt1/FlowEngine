import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flow.operations import frames_to_video as f2v_mod


def _evaluate_upload_applied_js(js_source: str, label: str) -> dict:
    node = shutil.which("node")
    if node is None:
        if "target-slot-thumbnail" in js_source and "media-in-frame-row" not in js_source:
            return {"ok": False, "reason": "target-slot-no-thumbnail"}
        return {"ok": True, "reason": "legacy-row-wide-thumbnail"}

    script = f"""
const vm = require('vm');

class Element {{
  constructor(tagName, text = '', options = {{}}) {{
    this.tagName = tagName.toUpperCase();
    this._text = text;
    this.children = [];
    this.parentElement = null;
    this.style = Object.assign(
      {{ display: 'block', visibility: 'visible', opacity: '1' }},
      options.style || {{}}
    );
    this.rect = Object.assign(
      {{ left: 0, top: 0, width: 100, height: 20 }},
      options.rect || {{}}
    );
    this.attributes = options.attributes || {{}};
  }}

  append(...children) {{
    for (const child of children) {{
      child.parentElement = this;
      this.children.push(child);
    }}
    return this;
  }}

  get innerText() {{
    return [this._text, ...this.children.map((child) => child.innerText)]
      .filter(Boolean)
      .join(' ');
  }}

  get textContent() {{
    return this.innerText;
  }}

  getBoundingClientRect() {{
    return this.rect;
  }}

  querySelectorAll(selector) {{
    return descendants(this).filter((element) => matchesSelector(element, selector));
  }}
}}

const descendants = (root) => root.children.flatMap((child) => [child, ...descendants(child)]);
const element = (tagName, text, options, ...children) => new Element(tagName, text, options).append(...children);

let body;
const matchesSelector = (element, selector) => {{
  if (selector === 'body *') return element !== body;
  if (selector === 'img') return element.tagName === 'IMG';
  if (selector === 'img, video, canvas') return ['IMG', 'VIDEO', 'CANVAS'].includes(element.tagName);
  if (selector === '[role="dialog"]') return element.attributes.role === 'dialog';
  if (selector === '[role="dialog"], [aria-modal="true"]') {{
    return element.attributes.role === 'dialog' || element.attributes['aria-modal'] === 'true';
  }}
  return false;
}};

const startSlot = element(
  'div',
  '',
  {{ rect: {{ width: 160, height: 120 }} }},
  element('span', 'Start frame', {{ rect: {{ width: 80, height: 20 }} }}),
  element('img', '', {{ rect: {{ width: 120, height: 80 }} }})
);
const endSlot = element(
  'div',
  '',
  {{ rect: {{ width: 160, height: 120 }} }},
  element('span', 'End frame', {{ rect: {{ width: 70, height: 20 }} }})
);
const row = element(
  'section',
  '',
  {{ rect: {{ width: 420, height: 160 }} }},
  element('span', 'Swap first and last frames', {{ rect: {{ width: 180, height: 20 }} }}),
  startSlot,
  endSlot
);
body = element('body', '', {{ rect: {{ width: 800, height: 600 }} }}, row);

const context = {{
  document: {{
    body,
    querySelectorAll: (selector) => body.querySelectorAll(selector),
  }},
  getComputedStyle: (element) => element.style,
}};
const fn = vm.runInNewContext({json.dumps(js_source)}, context, {{ timeout: 1000 }});
console.log(JSON.stringify(fn({json.dumps(label)})));
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.asyncio
async def test_wait_for_end_upload_rejects_start_thumbnail_only_dom(monkeypatch):
    page = Mock()

    async def evaluate(js_source, label):
        return _evaluate_upload_applied_js(js_source, label)

    page.evaluate = AsyncMock(side_effect=evaluate)
    monkeypatch.setattr(f2v_mod, "_accept_upload_rights_notice", AsyncMock())
    monkeypatch.setattr(f2v_mod.asyncio, "sleep", AsyncMock())
    fake_clock = Mock(side_effect=[100.0, 100.0, 101.0])
    monkeypatch.setattr(f2v_mod, "time", SimpleNamespace(monotonic=fake_clock))

    with pytest.raises(RuntimeError, match="End frame upload did not attach"):
        await f2v_mod._wait_for_frame_upload_applied(page, "End", timeout_sec=0.5)

    page.evaluate.assert_awaited_once()
