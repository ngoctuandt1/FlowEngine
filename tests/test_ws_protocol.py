import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WS_JS = REPO_ROOT / "frontend" / "js" / "ws.js"
HOME_JS = REPO_ROOT / "frontend" / "js" / "pages" / "home.js"
DASHBOARD_JS = REPO_ROOT / "frontend" / "js" / "pages" / "dashboard.js"


def _run_ws_runtime_probe() -> dict | None:
    node = shutil.which("node")
    if node is None:
        return None

    script = f"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync({json.dumps(str(WS_JS))}, 'utf8') + '\\n;globalThis.__WS__ = WS;';
const context = {{
  console,
  location: {{ protocol: 'http:', host: 'example.test' }},
  document: {{ getElementById: () => null }},
  WebSocket: function FakeWebSocket() {{}},
  setTimeout: () => ({{}}),
  clearTimeout: () => {{}},
}};
context.globalThis = context;

vm.createContext(context);
vm.runInContext(source, context, {{ filename: 'frontend/js/ws.js' }});

const WS = context.__WS__;
const seen = {{
  jobUpdate: [],
  message: [],
  pingArgTypes: [],
}};

WS.on('job_update', (payload) => seen.jobUpdate.push(payload));
WS.on('message', (frame) => seen.message.push(frame));
WS.on('ping', (payload) => seen.pingArgTypes.push(typeof payload));

WS._handleMessage({{ event: 'job_update', data: {{ id: 'job-123', status: 'running' }} }});
WS._handleMessage({{ event: 'ping', ts: '2026-05-01T00:00:00Z' }});

console.log(JSON.stringify(seen));
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_ws_js_dispatches_event_data_frames_to_registered_handlers():
    seen = _run_ws_runtime_probe()

    if seen is None:
        source = WS_JS.read_text(encoding="utf-8")
        assert "const { event, data } = frame;" in source
        assert "this._emit(event, data);" in source
        assert "this._emit('message', frame);" in source
        return

    assert seen["jobUpdate"] == [{"id": "job-123", "status": "running"}]
    assert seen["message"] == [
        {"event": "job_update", "data": {"id": "job-123", "status": "running"}},
        {"event": "ping", "ts": "2026-05-01T00:00:00Z"},
    ]
    assert seen["pingArgTypes"] == ["undefined"]


def test_home_page_listens_for_job_update_only():
    source = HOME_JS.read_text(encoding="utf-8")

    assert "WS.on('job_update'" in source
    assert "WS.on('job_created'" not in source
    assert "WS.on('job_updated'" not in source
    assert "WS.on('job_completed'" not in source
    assert "WS.on('job_failed'" not in source


def test_dashboard_page_listens_for_job_update():
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    assert "WS.on('job_update'" in source
    assert "WS.on('job_created'" not in source
    assert "WS.on('job_updated'" not in source
    assert "WS.on('job_completed'" not in source
    assert "WS.on('job_failed'" not in source
    assert "WS.on('job_deleted'" not in source
