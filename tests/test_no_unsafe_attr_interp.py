import json
import re
import shutil
import subprocess
from pathlib import Path

FRONTEND_JS = Path("frontend/js")
# U1-owned offenders stay deferred until their PR can touch those files.
DEFERRED_ATTR_INTERP_FILES = {
    Path("frontend/js/api.js"),
    Path("frontend/js/pages/dashboard.js"),
    Path("frontend/js/pages/chain-tree.js"),
    Path("frontend/js/pages/job-detail.js"),
    Path("frontend/js/pages/media-tools.js"),
}

ATTR_INTERPOLATION = re.compile(
    r"(?P<attr>[\w:-]+)=\\?[\"'](?P<body>[^\"'\n]*?\$\{(?P<expr>[^}\n]+)\})"
)
RISKY_ATTRS = {
    "src",
    "href",
    "alt",
    "title",
    "value",
    "placeholder",
    "id",
    "for",
    "aria-label",
    "download",
}
SAFE_COMMENT_MARKER = "// safe:"
SAFE_CALL_PREFIXES = ("App.escapeHtml(", "App.safeHref(", "encodeURIComponent(")
SAFE_CALL_EXPR = re.compile(
    r"(?:App\.escapeHtml|App\.safeHref|encodeURIComponent)\s*\(.+\)"
)
SAFE_LITERAL_EXPR = re.compile(
    r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\$])*`|true|false|\d+(?:\.\d+)?)"
)
SAFE_PRIMITIVE_EXPR = re.compile(
    r"[A-Za-z_$][\w$]*(?:Index|Count)"
)


def is_risky_attr(attr: str) -> bool:
    return attr in RISKY_ATTRS or attr.startswith("data-")


def is_safe_attr_expr(expr: str) -> bool:
    expr = expr.strip()
    if expr.startswith(SAFE_CALL_PREFIXES):
        return True
    if SAFE_LITERAL_EXPR.fullmatch(expr):
        return True
    if SAFE_CALL_EXPR.fullmatch(expr):
        return True
    if SAFE_PRIMITIVE_EXPR.fullmatch(expr):
        return True
    return False


def test_no_unescaped_attribute_interpolation_in_frontend_js():
    offenders = []
    for path in sorted(FRONTEND_JS.rglob("*.js")):
        if path in DEFERRED_ATTR_INTERP_FILES:
            continue

        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SAFE_COMMENT_MARKER in line:
                continue

            for match in ATTR_INTERPOLATION.finditer(line):
                attr = match.group("attr")
                expr = match.group("expr")
                if is_risky_attr(attr) and not is_safe_attr_expr(expr):
                    offenders.append(
                        f"{path}:{line_no} {attr}= ${{{expr.strip()}}} "
                        f"Wrap ${{{expr.strip()}}} in App.escapeHtml() or App.safeHref(), "
                        "or add // safe: <reason> on the line"
                    )

    assert not offenders, "Unsafe attribute interpolation found:\n" + "\n".join(offenders)


def test_app_escape_html_is_attribute_safe():
    source = Path("frontend/js/app.js").read_text(encoding="utf-8")
    escape_body = source[source.index("escapeHtml(text)") : source.index("  /**", source.index("escapeHtml(text)"))]
    for escaped in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;", "&#x2F;"):
        assert escaped in escape_body


def test_app_safe_href_allowlists_flow_and_same_origin_relative():
    node = shutil.which("node")
    source = Path("frontend/js/app.js").read_text(encoding="utf-8")

    if node is None:
        assert "safeHref(url)" in source
        assert "new URL(raw, window.location.origin)" in source
        assert "labs.google" in source
        return

    script = f"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync({json.dumps(str(Path('frontend/js/app.js').resolve()))}, 'utf8') + '\\n;globalThis.__App__ = App;';
const warnings = [];
const context = {{
  console: {{ warn: (...args) => warnings.push(args), error: () => {{}}, log: () => {{}} }},
  localStorage: {{ removeItem: () => {{}} }},
  document: {{ addEventListener: () => {{}}, getElementById: () => null }},
  window: {{ location: {{ origin: 'https://app.example.com', hash: '' }} }},
  location: {{ hash: '' }},
  URL: URL,
  setTimeout: () => ({{}}),
  clearTimeout: () => {{}},
}};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {{ filename: 'frontend/js/app.js' }});

const App = context.__App__;
const inputs = ['', 'javascript:alert(1)', 'data:text/html,pwn', 'https://labs.google/fx/tools/flow', '/jobs', '/\\t/evil.com', '/\\\\evil.com', '//evil.com'];
console.log(JSON.stringify({{
  outputs: inputs.map((value) => App.safeHref(value)),
  warningCount: warnings.length,
}}));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    seen = json.loads(result.stdout)

    assert seen["outputs"] == ['', '#', '#', 'https://labs.google/fx/tools/flow', '/jobs', '#', '#', '#']
    assert seen["warningCount"] == 5
