"""Slugs, public IDs, formulas and material categories."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata

from .rii import strip_html

ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr
Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re
Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu D""".split())
METAL_ELEMENTS = set("""Li Be Na Mg Al K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn
Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Th U""".split())
GASES = set("He Ne Ar Kr Xe Rn H2 D2 N2 O2 CO CO2 N2O NH3 SF6 CH4 C2H2 C2H4 C2H6 air".split())
SEMICONDUCTORS = set("""Si Ge Se Te SiC AlAs GaAs InAs AlSb GaSb InSb AlN GaN BP GaP InP ZnO ZnS ZnSe ZnTe CdS CdSe
CdTe PbS PbSe PbTe HgS Cu2O CuO GaS GaSe In2Se3 SnSe Bi2Se3 Bi2Te3 Ge2Sb2Te5 EuS CdGeAs2 ZnSiAs2 CdGeP2 ZnGeP2
AgGaS2 AgGaSe2 CuGaS2 CdGa2S4 HgGa2S4 LiGaS2 BaGa4S7 BaGa4Se7 BaGa2GeSe6 CuCl VO2 Fe2O3 Fe3O4 MoO2 B4C B""".split())
TWO_D = set("""MoS2 MoSe2 MoTe2 WS2 WSe2 WTe2 ReS2 HfSe2 ZrSe2 PtS2 PtSe2 PdSe2 SnS2 SnSe2 NbSe2 TaS2 TaSe2 MnPSe3
NiPS3 Ti3C2 MoOCl2 ZrTe5 franckeite 2D_HOIP""".split())
METALLIC_COMPOUNDS = set("TiN VN TiC VC TiH2 AuAl2 PtAl2 ZrN HfN TaN NbN".split())

PHASE_CHANGE = set("Ge2Sb2Te5 GeTe Sb2Se3 Sb2S3 Sb2Te3 In3SbTe2 Ge2Sb2Se4Te1 GeSbSeTe VO2 AgInSbTe".split())
# dielectric crystals and films used as integrated-photonics platforms; browsed with the semiconductors
PHOTONIC_PLATFORMS = set("Si3N4 LiNbO3 LiTaO3 AlN KTiOPO4 BaTiO3 GaP".split())
INTEGRATED_PHOTONICS = set("""Si Si3N4 SiO2 LiNbO3 LiTaO3 InP GaAs Ge AlN GaN TiO2 Ta2O5 Al2O3 HfO2 BaTiO3 SiC
Ge2Sb2Te5 Sb2Se3 VO2""".split())

# curated extra names users search for (material key -> aliases)
EXTRA_ALIASES = {
    "rii:main/Si3N4": ["SiN", "SiNx", "silicon nitride"],
    "rii:main/LiNbO3": ["LN", "TFLN", "thin-film lithium niobate", "lithium niobate"],
    "rii:main/LiTaO3": ["LT", "thin-film lithium tantalate"],
    "rii:main/Ge2Sb2Te5": ["GST", "GST-225", "GST225"],
    "rii:main/SiO2": ["silica", "silicon dioxide", "glass"],
    "rii:main/Al2O3": ["alumina", "sapphire"],
    "rii:main/Si": ["silicon", "c-Si", "SOI"],
    "rii:main/AlAs": ["AlGaAs"],
    "rii:main/InP": ["indium phosphide"],
    "rii:main/GaAs": ["gallium arsenide"],
}

# catalog groups (dividers) of the "other" shelf -> category, first match wins
GROUP_CATEGORIES = [
    (r"semiconductor alloy|perovskite|quantum dot", "semiconductor"),
    (r"gas", "gas"), (r"alloy|intermetallic", "metal"),
    (r"polymer|resist|plastic|adhesive|mixed organic compounds", "polymer"),
    (r"human body|silk|buffer", "biological"),
    (r"liquid crystal|immersion oil|fuel|heat transfer|index matching|mounting media|organic/inorganic", "liquid"),
    (r"glass|filter|mixed crystal|doped|non-stoichiometric", "glass_oxide"),
    (r"soot|clay|mineral|amorphous|exotic", "other"),
]


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def slugify(s: str, max_len: int = 80) -> str:
    s = ascii_fold(strip_html(s)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:max_len].strip("-") or "x"


def search_text(*terms: str | None) -> str:
    """Lower-case search terms delimited by '|', so '%|term|%' matches a whole name or alias."""
    return "|" + "|".join(dict.fromkeys(t.strip().lower() for t in terms if t and t.strip())) + "|"


def stable_id(prefix: str, key: str) -> str:
    """Deterministic public ID for imported records (same on every deployment)."""
    return f"{prefix}_{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def random_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


_FORMULA_RE = re.compile(r"^(?:[A-Z][a-z]?|\d+(?:\.\d+)?|[()\[\]·\-+xy,])+$")


def formula_from_name(name_html: str | None, book: str) -> tuple[str | None, str | None]:
    """(formula, common name) from catalog book names like ``"CH<sub>4</sub> (Methane)"``."""
    text = strip_html(name_html or "")
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", text)
    head, tail = (m.group(1), m.group(2)) if m else (text, None)
    for cand in (head, book):
        c = cand.replace(" ", "")
        if c and _FORMULA_RE.match(c) and any(ch.isupper() for ch in c) and _elements_ok(c):
            return c, tail
    return None, tail or text or None


def _elements_ok(f: str) -> bool:
    syms = re.findall(r"[A-Z][a-z]?", f)
    return bool(syms) and all(s in ELEMENTS for s in syms)


def categorize(shelf: str, book: str, formula: str | None, book_group: str | None, page_groups: list[str]) -> str:
    """Best-effort category from the catalog position and the formula."""
    key = book if shelf == "main" else (formula or book)
    groups = " | ".join(page_groups).lower()
    if shelf == "main":
        return _main_category(key)
    if key in PHASE_CHANGE:
        return "phase_change"
    if shelf == "organic":
        bg = (book_group or "").lower()
        if "polymer" in bg:
            return "polymer"
        if key in GASES:
            return "gas"
        if "amino" in bg or "other non-polymer" in bg:
            return "other"
        return "liquid"
    if shelf == "glass":
        return "glass_oxide"
    group = (book_group or "").lower()
    if shelf == "specs":
        return "liquid" if "liquid" in group else "glass_oxide"
    if key in TWO_D:
        return "2d"
    if key in ("Si-InP",):
        return "semiconductor"
    for pat, cat in GROUP_CATEGORIES:
        if re.search(pat, group):
            return cat
    return "other"


def _main_category(key: str) -> str:
    if key in PHASE_CHANGE:
        return "phase_change"
    if key in GASES:
        return "gas"
    if key == "H2O":
        return "liquid"
    if key == "C":  # diamond, graphite, graphene, nanotubes, amorphous carbon
        return "other"
    if key in METAL_ELEMENTS or key in METALLIC_COMPOUNDS:
        return "metal"
    if key in SEMICONDUCTORS or key in PHOTONIC_PLATFORMS or key.startswith(("CH3NH3Pb", "CsPb", "Cs2Ag")):
        return "semiconductor"
    if key in TWO_D:
        return "2d"
    return "glass_oxide"


def tags_for(shelf: str, book: str, formula: str | None, book_group: str | None) -> list[str]:
    """Browsing tags (anisotropy is added from the datasets)."""
    key = book if shelf == "main" else (formula or book)
    tags = []
    if key in INTEGRATED_PHOTONICS:
        tags.append("integrated-photonics")
    if key in PHASE_CHANGE:
        tags.append("phase-change")
    if "resist" in (book_group or "").lower():
        tags.append("photoresist")
    if shelf == "specs":
        tags.append("supplier-datasheet")
    return tags


AXIS_LABELS = ("o", "e", "x", "y", "z", "alpha", "beta", "gamma", "a", "b", "c")
_AXIS_SUFFIX = re.compile(r"[-_ ](o|e|x|y|z|a|b|c|alpha|beta|gamma|ordinary|extraordinary)$", re.I)


def optical_axis(conditions: dict, page: str) -> tuple[str | None, str]:
    """(axis label, page key without the axis suffix) for anisotropic entries, e.g. "Zelmon-o" -> ("o", "Zelmon")."""
    d = str(conditions.get("direction") or "").strip().lower()
    m = _AXIS_SUFFIX.search(page)
    base = page[: m.start()] if m else page
    if d in AXIS_LABELS:
        return d, base
    if m:
        lab = m.group(1).lower()
        lab = {"ordinary": "o", "extraordinary": "e"}.get(lab, lab)
        if lab in ("o", "e", "alpha", "beta", "gamma"):  # x/y/z/a/b/c suffixes are too ambiguous alone
            return lab, base
    return None, page


def anisotropy_kind(axes: set[str]) -> str | None:
    if not axes:
        return None
    return "uniaxial" if axes <= {"o", "e"} else "biaxial"


_STATES = [
    (r"\bamorphous\b|\ba-[A-Z]|\bamorph\b", "amorphous"),
    (r"poly-?crystal", "polycrystalline"),
    (r"\bcrystal(line)?\b|single[- ]crystal|\bc-[A-Z]", "crystalline"),
    (r"\bliquid\b", "liquid"), (r"\bgas\b|\bvapou?r\b", "gas"), (r"\bsolid\b|\bice\b", "solid"),
]


def material_state(*texts: str | None) -> str | None:
    """amorphous | polycrystalline | crystalline | liquid | gas | solid, from names, groups and comments."""
    text = " ".join(t for t in texts if t)
    for pat, state in _STATES:
        if re.search(pat, text, re.I):
            return state
    return None
