"""Reference data every deployment needs (licenses)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import License

# allowed_for_contributions: licenses a community upload may carry. Anything that
# forbids commercial use or derivatives is excluded so that every dataset can be
# redistributed and used by the hosted compute service. See README "Open questions".
LICENSES = [
    ("CC0-1.0", "Creative Commons Zero v1.0 Universal (public domain dedication)",
     "https://creativecommons.org/publicdomain/zero/1.0/", False, False, True),
    ("CC-BY-4.0", "Creative Commons Attribution 4.0 International",
     "https://creativecommons.org/licenses/by/4.0/", True, False, True),
    ("CC-BY-SA-4.0", "Creative Commons Attribution-ShareAlike 4.0 International",
     "https://creativecommons.org/licenses/by-sa/4.0/", True, True, False),
    ("ODbL-1.0", "Open Data Commons Open Database License v1.0",
     "https://opendatacommons.org/licenses/odbl/1-0/", True, True, False),
    ("PDDL-1.0", "Open Data Commons Public Domain Dedication and License v1.0",
     "https://opendatacommons.org/licenses/pddl/1-0/", False, False, True),
]


def ensure_licenses(session: Session) -> None:
    for lid, name, url, attr, sa, allowed in LICENSES:
        lic = session.get(License, lid)
        if lic is None:
            session.add(License(id=lid, name=name, url=url, requires_attribution=attr, share_alike=sa,
                                allowed_for_contributions=allowed))
    session.flush()
