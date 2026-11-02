"""What the website shows: sanitised source HTML, dispersion formulas as MathML, derived optical
quantities, data tables and the library snippets.

The source database writes references, material descriptions and names in a small HTML subset
(``<a>``, ``<b>``, ``<i>``, ``<sub>``, lists). :func:`sanitize` keeps that subset and drops the rest.
The derived quantities match ``static/site.js``, which recomputes them in the browser.
"""

from __future__ import annotations

import html
import json
import math
import re
from html.parser import HTMLParser

import numpy as np
from jinja2 import Undefined
from markupsafe import Markup, escape

from .. import rii
from ..optics import Dispersion
from ..rii import FORMULA_NAMES

# --- HTML from the source database -------------------------------------------------------

_ALLOWED = {"a", "b", "i", "em", "strong", "sub", "sup", "br", "p", "ul", "ol", "li", "span", "small"}
_VOID = {"br"}


class _Sanitizer(HTMLParser):
    def __init__(self, links: bool):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.links = links
        self.open: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED or (tag == "a" and not self.links):
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not re.match(r"^(https?:)?//|^mailto:", href.strip(), re.I):
                return
            href = re.sub(r"^//", "https://", href.strip())
            self.out.append(f'<a href="{escape(href)}" rel="noopener nofollow">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in _VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in self.open:
            while self.open:
                t = self.open.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(str(escape(data)))

    def result(self) -> str:
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.open))


def sanitize(text: str | None, links: bool = True) -> Markup:
    """Source HTML reduced to safe inline/list markup (links only to http(s) and mailto)."""
    if not text:
        return Markup("")
    p = _Sanitizer(links)
    p.feed(text)
    p.close()
    return Markup(p.result().strip())


def paragraphs(text: str | None) -> Markup:
    """A description: blank-line separated paragraphs, with the source's inline HTML kept."""
    if not text:
        return Markup("")
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for p in parts:
        body = sanitize(p.replace("\n", " "))
        out.append(body if re.match(r"^\s*<(ul|ol|p)\b", body) else Markup(f"<p>{body}</p>"))
    return Markup("\n".join(out))


def plain(text: str | None) -> str:
    """Text without markup, for titles and meta tags."""
    if not text:
        return ""
    s = re.sub(r"<br\s*/?>", "; ", text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip(" ;")


def summary(text: str | None, limit: int = 160) -> str:
    """Plain text cut at a word boundary, for meta descriptions."""
    s = plain(text)
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


# --- numbers -------------------------------------------------------------------------------

def num(x, digits: int = 6) -> str:
    """A value for display: ``digits`` significant figures, scientific when very large or small."""
    if x is None or isinstance(x, Undefined):  # missing values, e.g. GVD of tabulated data
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    if x == 0:
        return "0"
    if 1e-4 <= abs(x) < 1e6:
        s = f"{x:.{digits}g}"
        return s if "e" not in s else f"{x:.{max(digits - 1, 1)}e}"
    return f"{x:.{max(digits - 1, 1)}e}"


def exact(x: float) -> str:
    """The shortest text that round-trips the float (the source's own digits)."""
    return repr(float(x))


def um_range(lo_nm: float, hi_nm: float) -> str:
    """'0.188–1.94 µm' in the style of the source catalog."""
    return f"{lo_nm / 1000:.4g}–{hi_nm / 1000:.4g} µm"


# --- dispersion formulas as MathML ------------------------------------------------------

LAMBDA = "<mi>λ</mi>"


def _mn(x: float) -> str:
    """A coefficient as MathML: decimal, or mantissa × 10^exponent."""
    s = exact(abs(x))
    if "e" in s:
        mant, e = s.split("e")
        return f"<mrow><mn>{mant}</mn><mo>×</mo><msup><mn>10</mn><mn>{int(e)}</mn></msup></mrow>"
    return f"<mn>{s}</mn>"


def _smn(x: float) -> str:
    """A signed coefficient (for bases and exponents)."""
    return _mn(x) if x >= 0 else f"<mrow><mo>−</mo>{_mn(x)}</mrow>"


def _pow(base: str, p: float) -> str:
    if p == 1:
        return base
    return f"<msup>{base}{_smn(p)}</msup>"


class _Sum:
    """Signed terms joined with + and −."""

    def __init__(self):
        self.terms: list[str] = []

    def add(self, coef: float, body: str | None = None, *, frac_den: str | None = None):
        sign = "−" if coef < 0 else "+"
        mag = abs(coef)
        if frac_den is not None:
            num_ = (_mn(mag) + (body or "")) if not (mag == 1 and body) else body
            term = f"<mfrac><mrow>{num_}</mrow><mrow>{frac_den}</mrow></mfrac>"
        elif body is None:
            term = _mn(mag)
        else:
            term = body if mag == 1 else f"{_mn(mag)}<mo>⁢</mo>{body}"
        self.terms.append((sign, term))

    def mathml(self) -> str:
        if not self.terms:
            return "<mn>0</mn>"
        out = []
        for i, (sign, term) in enumerate(self.terms):
            if i == 0:
                out.append(("<mo>−</mo>" if sign == "−" else "") + term)
            else:
                out.append(f"<mo>{sign}</mo>{term}")
        return "".join(out)


def formula_mathml(num_: int, coefficients: list[float]) -> Markup:
    """The dispersion formula with its coefficients substituted (λ in µm), as display MathML."""
    c = list(map(float, coefficients)) + [0.0] * max(0, 17 - len(coefficients))
    l2 = f"<msup>{LAMBDA}<mn>2</mn></msup>"
    s = _Sum()
    pairs = range(1, len(coefficients) - 1, 2)
    if num_ in (1, 2):
        lhs = "<msup><mi>n</mi><mn>2</mn></msup><mo>−</mo><mn>1</mn>"
        if c[0]:
            s.add(c[0])
        for i in pairs:
            if c[i]:
                pole = f"<msup>{_mn(c[i + 1])}<mn>2</mn></msup>" if num_ == 1 else _mn(c[i + 1])
                sign = "−" if num_ == 1 or c[i + 1] >= 0 else "+"
                s.add(c[i], l2, frac_den=f"{l2}<mo>{sign}</mo>{pole}")
    elif num_ in (3, 5):
        lhs = "<msup><mi>n</mi><mn>2</mn></msup>" if num_ == 3 else "<mi>n</mi>"
        s.add(c[0])
        for i in pairs:
            if c[i]:
                s.add(c[i], _pow(LAMBDA, c[i + 1]))
    elif num_ == 4:
        lhs = "<msup><mi>n</mi><mn>2</mn></msup>"
        s.add(c[0])
        for i in (1, 5):
            if c[i]:
                s.add(c[i], _pow(LAMBDA, c[i + 1]),
                      frac_den=f"{l2}<mo>−</mo><msup>{_smn(c[i + 2])}{_smn(c[i + 3])}</msup>")
        for i in range(9, len(coefficients) - 1, 2):
            if c[i]:
                s.add(c[i], _pow(LAMBDA, c[i + 1]))
    elif num_ == 6:
        lhs = "<mi>n</mi><mo>−</mo><mn>1</mn>"
        if c[0]:
            s.add(c[0])
        for i in pairs:
            if c[i]:
                s.add(c[i], None, frac_den=f"{_mn(c[i + 1])}<mo>−</mo><msup>{LAMBDA}<mn>−2</mn></msup>")
    elif num_ == 7:
        lhs = "<mi>n</mi>"
        d = f"{l2}<mo>−</mo><mn>0.028</mn>"
        s.add(c[0])
        if c[1]:
            s.add(c[1], None, frac_den=d)
        if c[2]:
            s.add(c[2], f"<msup><mrow><mo>(</mo><mfrac><mn>1</mn><mrow>{d}</mrow></mfrac><mo>)</mo></mrow><mn>2</mn></msup>")
        for i, p in ((3, 2), (4, 4), (5, 6)):
            if c[i]:
                s.add(c[i], f"<msup>{LAMBDA}<mn>{p}</mn></msup>")
    elif num_ == 8:
        lhs = ("<mfrac><mrow><msup><mi>n</mi><mn>2</mn></msup><mo>−</mo><mn>1</mn></mrow>"
               "<mrow><msup><mi>n</mi><mn>2</mn></msup><mo>+</mo><mn>2</mn></mrow></mfrac>")
        s.add(c[0])
        if c[1]:
            s.add(c[1], l2, frac_den=f"{l2}<mo>−</mo>{_mn(c[2])}")
        if c[3]:
            s.add(c[3], l2)
    elif num_ == 9:
        lhs = "<msup><mi>n</mi><mn>2</mn></msup>"
        s.add(c[0])
        s.add(c[1], None, frac_den=f"{l2}<mo>−</mo>{_mn(c[2])}")
        shifted = f"<mo>(</mo>{LAMBDA}<mo>−</mo>{_smn(c[4])}<mo>)</mo>"
        s.add(c[3], f"<mrow>{shifted}</mrow>",
              frac_den=f"<msup><mrow>{shifted}</mrow><mn>2</mn></msup><mo>+</mo>{_mn(c[5])}")
    else:
        return Markup("")
    return Markup(f'<math display="block" class="formula"><mrow>{lhs}<mo>=</mo>{s.mathml()}</mrow></math>')


def formula_info(blocks: list[dict]) -> dict | None:
    for b in blocks:
        if b["type"].startswith("formula"):
            n = int(b["formula"])
            return {"number": n, "name": FORMULA_NAMES.get(n, f"formula {n}"),
                    "coefficients": [exact(x) for x in b["coefficients"]],
                    "range_um": [exact(x) for x in b["range_um"]],
                    "mathml": formula_mathml(n, b["coefficients"])}
    return None


# --- derived quantities (keep in step with static/site.js) ---------------------------------

C_UM_PER_FS = 0.299792458
LINES_UM = {"d": 0.5875618, "F": 0.4861327, "C": 0.6562725}
ABBE_MAX_K = 0.01


def derived_at(disp: Dispersion, wl_um: float) -> dict:
    """n, k and the quantities derived from them at one vacuum wavelength (µm)."""
    def nk(x):
        n, k = disp.nk(np.asarray([x * 1000.0]))
        return float(n[0]), float(k[0])

    n, k = nk(wl_um)
    out = {"wavelength_um": wl_um, "n": n, "k": k}
    if not math.isfinite(k):
        return out
    out["eps1"] = n * n - k * k if math.isfinite(n) else None
    out["eps2"] = 2 * n * k if math.isfinite(n) else None
    out["alpha"] = 4 * math.pi * k / (wl_um * 1e-4)  # cm^-1
    out["skin_depth_nm"] = wl_um * 1000 / (2 * math.pi * k) if k > 0 else None
    lo, hi = disp.range_nm

    def inside(h, m):  # wl ± m·h within the data range
        return lo <= (wl_um - m * h) * 1000 and (wl_um + m * h) * 1000 <= hi

    if math.isfinite(n):
        f = lambda x: nk(x)[0]  # noqa: E731
        d1 = None
        if disp.is_formula and inside(wl_um * 1e-3, 2):  # five-point stencils for formulas
            h = wl_um * 1e-3
            d1 = (-f(wl_um + 2 * h) + 8 * f(wl_um + h) - 8 * f(wl_um - h) + f(wl_um - 2 * h)) / (12 * h)
        elif inside(wl_um * 1e-4, 1):  # tables: the slope of the interpolating segment
            h = wl_um * 1e-4
            d1 = (f(wl_um + h) - f(wl_um - h)) / (2 * h)
        if d1 is not None:
            out["dn_dl"] = d1
            out["ng"] = n - wl_um * d1
        if disp.is_formula:  # group-velocity dispersion needs a smooth n(λ)
            d2 = None
            if inside(wl_um * 5e-3, 2):
                h = wl_um * 5e-3
                d2 = (-f(wl_um + 2 * h) + 16 * f(wl_um + h) - 30 * n + 16 * f(wl_um - h) - f(wl_um - 2 * h)) / (12 * h * h)
            elif inside(wl_um * 1e-3, 1):
                h = wl_um * 1e-3
                d2 = (f(wl_um + h) - 2 * n + f(wl_um - h)) / (h * h)
            if d2 is not None:
                out["gvd"] = 1e3 * wl_um**3 / (2 * math.pi * C_UM_PER_FS**2) * d2  # fs^2/mm
                out["D"] = -wl_um / C_UM_PER_FS * d2 * 1e3  # ps/(nm km)
    return out


def abbe_number(disp: Dispersion) -> float | None:
    """Vd from the d, F and C lines, for formula data of transparent media (as the source site shows it)."""
    lo, hi = disp.range_nm
    if not disp.is_formula or lo > LINES_UM["F"] * 1000 or hi < LINES_UM["C"] * 1000:
        return None
    n = {key: float(disp.nk(np.asarray([v * 1000.0]))[0][0]) for key, v in LINES_UM.items()}
    k_d = float(disp.nk(np.asarray([LINES_UM["d"] * 1000.0]))[1][0])
    if k_d > ABBE_MAX_K or n["F"] == n["C"]:  # only meaningful for transparent media
        return None
    return (n["d"] - 1) / (n["F"] - n["C"])


def default_wavelength_um(lo_nm: float, hi_nm: float) -> float:
    """The d line (587.6 nm), as the source site uses, when it is in range."""
    if lo_nm <= 587.6 <= hi_nm:
        return 0.5876
    mid = math.sqrt(lo_nm * hi_nm) / 1000.0
    return float(f"{mid:.3g}")


# --- data for the page --------------------------------------------------------------------

def source_order(raw: bytes | None, conditions: dict, properties: dict | None) -> tuple[dict, dict | None]:
    """CONDITIONS and PROPERTIES in the order of the original file (the database's JSON columns sort keys)."""
    if not raw:
        return conditions, properties
    try:
        doc = rii.parse_yaml(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return conditions, properties
    cond = doc.get("CONDITIONS") if isinstance(doc.get("CONDITIONS"), dict) else None
    props = doc.get("PROPERTIES") if isinstance(doc.get("PROPERTIES"), dict) else None

    def ordered(src: dict | None, stored: dict | None) -> dict | None:
        if not src or not stored:
            return stored
        return {**{k: stored[k] for k in src if k in stored}, **{k: v for k, v in stored.items() if k not in src}}

    return ordered(cond, conditions) or {}, ordered(props, properties)


def table_rows(blocks: list[dict], disp: Dispersion) -> tuple[list[tuple[str, str, str]], bool]:
    """(λ µm, n, k) rows: the published rows of a single table, or the formula sampled like /data.

    Returns the rows and whether they are the published values (``True``) or computed ones."""
    tabs = [b for b in blocks if b["type"].startswith("tabulated")]
    if len(blocks) == 1 and tabs:
        b = tabs[0]
        n = b.get("n") or [None] * len(b["wavelength_um"])
        k = b.get("k") or [None] * len(b["wavelength_um"])
        return [(exact(w), exact(x) if x is not None else "", exact(y) if y is not None else "")
                for w, x, y in zip(b["wavelength_um"], n, k)], True
    wl = disp.sample_nm()
    n, k = disp.nk(wl)
    return [(f"{w / 1000:.6g}", num(a, 7) if math.isfinite(a) else "", num(b, 7) if math.isfinite(b) else "")
            for w, a, b in zip(wl, n, k)], False


def plot_payload(disp: Dispersion) -> dict:
    """What site.js needs to evaluate n and k exactly as the API does."""
    out: dict = {"range_nm": list(disp.range_nm), "has_n": disp.has_n, "has_k": disp.has_k}
    if disp.formula is not None:
        num_, coeffs, lo, hi = disp.formula
        out["formula"] = {"type": num_, "c": [float(x) for x in coeffs], "range_um": [lo, hi]}
    if disp.n_tab is not None:
        out["n"] = [disp.n_tab[0].tolist(), disp.n_tab[1].tolist()]
    if disp.k_tab is not None:
        out["k"] = [disp.k_tab[0].tolist(), disp.k_tab[1].tolist()]
    return out


def json_script(data) -> Markup:
    """JSON safe to embed in <script type="application/json">."""
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


# --- library snippets --------------------------------------------------------------------

# The library's own check on shelf/book/page names (lightsmith.materials._SAFE_NAME).
_SAFE_NAME = re.compile(r"^\w(?:[\w .,+()\-]*[\w.,+()\-])?$")


def py_identifier(*candidates: str | None) -> str:
    for c in candidates:
        s = re.sub(r"[^0-9a-zA-Z]+", "_", c or "").strip("_").lower()
        if s and s[0].isalpha() and len(s) <= 16 and s not in ("ls", "json", "url", "sim", "res"):
            return s
    return "mat"


def snippets(*, ds_slug: str, ds_public_id: str, label: str, source: dict, library: dict | None,
             has_n: bool, range_nm: tuple[float, float], var: str, base_url: str, has_model: bool,
             docs_url: str) -> list[dict]:
    """Copy-paste Python for loading the dataset in the simulation library.

    Every call here exists in the library (``lightsmith.materials``): ``fetch_refractiveindex``,
    ``LibraryMaterial``, ``Tabulated`` and ``DrudeLorentz``; tests run them when it is installed."""
    out = []
    api = f"{base_url}/v1/datasets/{ds_public_id}/lightsmith"
    lo, hi = range_nm
    if not has_n:
        return out
    if library:
        out.append({"id": "bundled", "label": "Bundled", "code": (
            "import lightsmith as ls\n\n"
            f"# {label}\n"
            f'{var} = ls.LibraryMaterial(name="{library["material"]}", dataset="{library["dataset"]}")\n'
            f"print({var}.range_nm)  # ({lo:g}, {hi:g}) nm\n"
            f"eps = {var}.eps([{_example_nm(lo, hi):g}])  # complex permittivity"),
            "note": "Ships with the library: works offline and is the library's own default dataset choice."
                    if library.get("default") else "Ships with the library: works offline."})
    shelf, book, page = source.get("shelf"), source.get("book"), source.get("page")
    commit = source.get("commit")
    if shelf and book and page and all(_SAFE_NAME.match(v) and ".." not in v for v in (shelf, book, page)):
        ref = f', ref="{commit}"' if commit else ""
        out.append({"id": "fetch", "label": "refractiveindex.info", "code": (
            "import lightsmith as ls\n\n"
            f"# {label}\n"
            f'{var} = ls.fetch_refractiveindex("{shelf}", "{book}", "{page}"{ref})\n'
            f"print({var}.range_nm)  # ({lo:g}, {hi:g}) nm\n"
            f"eps = {var}.eps([{_example_nm(lo, hi):g}])  # complex permittivity"),
            "note": "Downloads the original record once (pinned to the imported snapshot) and caches it."})
    out.append({"id": "api", "label": "This API", "code": (
        "import json, urllib.request\nimport lightsmith as ls\n\n"
        f'url = "{api}"\n'
        f"{var} = ls.Tabulated.model_validate(json.load(urllib.request.urlopen(url)))\n"
        f"print({var}.source)  # citation and provenance travel with the data"),
        "note": f"Any dataset here, as the library's Tabulated material ({ds_slug})."})
    start, stop = _sim_band(lo, hi)
    out.append({"id": "simulate", "label": "Simulate", "code": (
        f"# ... load {var} as above, then:\n"
        "sim = ls.Simulation(\n"
        f"    particles=[ls.Particle(shape=ls.Sphere(radius_nm=50), material={var})],\n"
        "    medium_index=1.0,\n"
        f"    wavelengths=ls.WavelengthRange(start_nm={start:g}, stop_nm={stop:g}, num=81),\n"
        ")\n"
        "res = ls.solve(sim)  # exact Mie for a sphere\n"
        "print(res.q_ext)     # extinction efficiency at each wavelength"),
        "note": "Same spec runs with DDA, FDTD, BEM or T-matrix: ls.compare(sim, [\"mie\", \"dda\"])."})
    if has_model:
        out.append({"id": "model", "label": "FDTD model", "code": (
            "import json, urllib.request\nimport lightsmith as ls\n\n"
            f'url = "{api}?model=drude_lorentz"\n'
            f"{var}_dl = ls.DrudeLorentz.model_validate(json.load(urllib.request.urlopen(url)))"),
            "note": "A dispersive Drude–Lorentz model for time-domain solvers."})
    return out


def _example_nm(lo: float, hi: float) -> float:
    return 633.0 if lo <= 633 <= hi else float(f"{math.sqrt(lo * hi):.3g}")


def _sim_band(lo: float, hi: float) -> tuple[float, float]:
    """A visible/near-IR band inside the data range, or the data range itself."""
    a, b = max(lo, 400.0), min(hi, 1000.0)
    if b - a < 50:
        a, b = lo, hi
    return float(f"{math.ceil(a):.4g}"), float(f"{math.floor(b):.4g}")


def studio_spec(material: dict, range_nm: tuple[float, float]) -> dict:
    """A scattering spec (sphere in vacuum) that Studio opens with ``?spec_url=``."""
    start, stop = _sim_band(*range_nm)
    return {"kind": "scattering", "schema_version": "0.1",
            "particles": [{"shape": {"kind": "sphere", "radius_nm": 50}, "material": material,
                           "position_nm": [0, 0, 0]}],
            "medium_index": 1.0,
            "illumination": {"kind": "plane_wave", "direction": [0, 0, 1], "polarization": [1, 0, 0]},
            "wavelengths": {"start_nm": start, "stop_nm": stop, "num": 81}}
