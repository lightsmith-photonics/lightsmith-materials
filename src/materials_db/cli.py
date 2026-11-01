"""``materials-db`` (alias ``lightsmith-materials``): migrations, imports, model fits, dev tokens, server."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from .config import get_settings


def _progress(label: str):
    def cb(i: int, n: int) -> None:
        print(f"  {label}: {i}/{n}", file=sys.stderr, flush=True)
    return cb


def cmd_migrate(args) -> None:
    from .db import upgrade
    upgrade(args.database_url)
    print("database at head revision", file=sys.stderr)


def _require_schema(db) -> None:
    from .db import schema_status

    status = schema_status(db.engine)
    if status != "ok":
        sys.exit(f"database schema is {status}; run `materials-db migrate` first")


def cmd_import_rii(args) -> None:
    from .db import Database, upgrade
    from .importer import download_archive, import_rii, open_source
    from .references import DoiResolver

    s = get_settings()
    commit = args.commit or s.rii_commit
    if args.migrate:
        upgrade(args.database_url)
    db = Database(args.database_url)
    _require_schema(db)
    if args.if_needed:
        from sqlalchemy import select

        from .models import ImportRun
        with db.session() as session:
            done = session.scalar(select(ImportRun.id).where(ImportRun.source == "rii", ImportRun.commit == commit,
                                                             ImportRun.status == "ok"))
        if done:
            print(f"database snapshot {commit} already imported; skipping", file=sys.stderr)
            return
    if args.source:
        path = args.source
    else:
        path = download_archive(commit, s.data_dir / "rii")
        print(f"database archive: {path}", file=sys.stderr)
    source = open_source(path)
    resolver = None
    if args.crossref:
        resolver = DoiResolver(s.data_dir / "doi-cache", mailto=s.crossref_mailto)
    t0 = time.time()
    with db.session() as session:
        stats = import_rii(session, source, commit, limit=args.limit, resolver=resolver,
                           progress=_progress("datasets"))
    out = stats.as_dict()
    out["seconds"] = round(time.time() - t0, 1)
    out["failures"] = out["failures"][:50]
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_enrich(args) -> None:
    from .db import Database
    from .importer import enrich_references
    from .references import DoiResolver

    s = get_settings()
    resolver = DoiResolver(s.data_dir / "doi-cache", mailto=s.crossref_mailto)
    with Database(args.database_url).session() as session:
        out = enrich_references(session, resolver, refresh=args.refresh, progress=_progress("references"))
    print(json.dumps(out, indent=1))


def cmd_token(args) -> None:
    from .auth import make_token

    secret = get_settings().jwt_secret
    if not secret:
        sys.exit("PLATFORM_JWT_SECRET is not set")
    print(make_token(secret, sub=args.sub, name=args.name, role=args.role, ttl=args.ttl))


def cmd_serve(args) -> None:
    import uvicorn
    uvicorn.run("materials_db.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload,
                proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


def cmd_fit_models(args) -> None:
    """Precompute and cache Drude-Lorentz fits (exact Sellmeier rewrites need no fitting)."""
    from sqlalchemy import select

    from .db import Database
    from .dispersion import ModelUnavailable, fit_available, get_model, stored_model
    from .models import Dataset, Material
    from .optics import Dispersion
    from .photonics import sellmeier_as_drude_lorentz

    if not fit_available():
        sys.exit("fitting needs the simulation library (the lightsmith package, materials_fit)")
    db = Database(args.database_url)
    cats = [c.strip() for c in args.category.split(",")] if args.category else None
    with db.session() as session:
        stmt = select(Dataset.id).join(Material).where(Dataset.status == "published", Dataset.has_k)
        if cats:
            stmt = stmt.where(Material.category.in_(cats))
        if not args.include_suppliers:
            stmt = stmt.where(Material.manufacturer.is_(None))
        ids = list(session.scalars(stmt.order_by(Dataset.id)))[: args.limit or None]
    done = skipped = failed = 0
    for i, ds_id in enumerate(ids):
        with db.session() as session:
            ds = session.get(Dataset, ds_id)
            ver = ds.versions[-1]
            if sellmeier_as_drude_lorentz(ver.blocks) is not None or stored_model(session, ds, ver.version):
                skipped += 1
                continue
            try:
                get_model(session, ds, ver, Dispersion(ver.blocks))
                done += 1
            except (ModelUnavailable, ValueError) as e:
                failed += 1
                print(f"  {ds.slug}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  models: {i}/{len(ids)}", file=sys.stderr, flush=True)
    print(json.dumps({"fitted": done, "skipped": skipped, "failed": failed}))


def cmd_stats(args) -> None:
    from .api.queries import database_stats
    from .db import Database

    with Database(args.database_url).session() as session:
        print(json.dumps(database_stats(session), indent=1))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="materials-db", description=__doc__)
    p.add_argument("--database-url", help="overrides DATABASE_URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)

    q = sub.add_parser("import-rii", help="import the refractiveindex.info database (idempotent)")
    q.add_argument("--commit", help="database repository commit (default: RII_COMMIT or the pinned one)")
    q.add_argument("--source", help="local zip archive or checkout instead of downloading")
    q.add_argument("--limit", type=int, help="import only the first N data files (testing)")
    q.add_argument("--crossref", action="store_true", help="also fetch DOI metadata (Crossref / doi.org)")
    q.add_argument("--migrate", action="store_true", help="apply migrations first (normally a separate step)")
    q.add_argument("--if-needed", action="store_true", help="skip when this commit was already imported")
    q.set_defaults(func=cmd_import_rii)

    q = sub.add_parser("enrich-references", help="fetch DOI metadata for references not checked yet")
    q.add_argument("--refresh", action="store_true", help="re-check every DOI")
    q.set_defaults(func=cmd_enrich)

    q = sub.add_parser("token", help="mint a platform JWT for local development")
    q.add_argument("--sub", default="dev-user")
    q.add_argument("--name", default="Dev User")
    q.add_argument("--role", default="user", help="user | moderator | admin")
    q.add_argument("--ttl", type=int, default=86400, help="seconds")
    q.set_defaults(func=cmd_token)

    q = sub.add_parser("fit-models", help="precompute Drude-Lorentz fits (needs the simulation library)")
    q.add_argument("--category", help="comma-separated categories, e.g. metal,semiconductor,phase_change")
    q.add_argument("--include-suppliers", action="store_true", help="also fit supplier datasheets")
    q.add_argument("--limit", type=int)
    q.set_defaults(func=cmd_fit_models)

    q = sub.add_parser("serve", help="run the API with uvicorn")
    q.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    q.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8100")))
    q.add_argument("--reload", action="store_true")
    q.set_defaults(func=cmd_serve)

    sub.add_parser("stats", help="print database statistics").set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
