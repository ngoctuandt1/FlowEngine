import re
from pathlib import Path

FRONTEND_JS = Path("frontend/js")

ATTR_INTERPOLATION = re.compile(
    r"(?P<attr>[\w:-]+)=\\?[\"'](?P<body>[^\"'\n]*?\$\{(?P<expr>[^}\n]+)\})"
)
SAFE_ASSIGNMENT = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<value>[^;]+)"
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
SANITIZERS = ("App.escapeHtml(", "escapeAttr(", "encodeURIComponent(", "CSS.escape(")
SAFE_FUNCTIONS = ("App.statusBadge(", "jobHash(", "chainTreeHash(")
SAFE_IDENTIFIERS = {"i", "index", "stepIndex", "size", "second", "k", "PAGE_ROOT_ID"}
SAFE_TERNARY_LITERAL = re.compile(
    r".+\?\s*('(?:[^']*)'|\"(?:[^\"]*)\"|true|false|\d+)\s*:\s*"
    r"('(?:[^']*)'|\"(?:[^\"]*)\"|true|false|\d+)"
)
SAFE_PRIMITIVE_EXPR = re.compile(
    r"(?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?|\d+)"
    r"(?:\s*(?:[+\-*/]|===?|!==?|<=?|>=?)\s*"
    r"(?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?|\d+|'[^']*'|\"[^\"]*\"))*"
)
SAFE_NUMERIC_OPTIONAL = re.compile(r"b\[k\] != null \? b\[k\] : ''")


def is_risky_attr(attr: str) -> bool:
    return attr in RISKY_ATTRS or attr.startswith("data-")


def is_safe_attr_expr(expr: str, safe_vars: set[str]) -> bool:
    expr = expr.strip()
    if any(marker in expr for marker in SANITIZERS):
        return True
    if any(expr.startswith(func) for func in SAFE_FUNCTIONS):
        return True
    if expr in safe_vars or expr in SAFE_IDENTIFIERS:
        return True
    if SAFE_TERNARY_LITERAL.fullmatch(expr):
        return True
    if SAFE_PRIMITIVE_EXPR.fullmatch(expr):
        return True
    if SAFE_NUMERIC_OPTIONAL.fullmatch(expr):
        return True
    return False


def test_no_unescaped_attribute_interpolation_in_frontend_js():
    offenders = []
    for path in sorted(FRONTEND_JS.rglob("*.js")):
        safe_vars: set[str] = set()
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assignment = SAFE_ASSIGNMENT.search(line)
            if assignment and any(marker in assignment.group("value") for marker in SANITIZERS):
                safe_vars.add(assignment.group("name"))

            for match in ATTR_INTERPOLATION.finditer(line):
                attr = match.group("attr")
                expr = match.group("expr")
                if is_risky_attr(attr) and not is_safe_attr_expr(expr, safe_vars):
                    offenders.append(f"{path}:{line_no} {attr}= ${{{expr.strip()}}}")

    assert not offenders, "Unsafe attribute interpolation found:\n" + "\n".join(offenders)


def test_app_escape_html_is_attribute_safe():
    source = Path("frontend/js/app.js").read_text(encoding="utf-8")
    escape_body = source[source.index("escapeHtml(text)") : source.index("  /**", source.index("escapeHtml(text)"))]
    for escaped in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;", "&#x2F;"):
        assert escaped in escape_body
