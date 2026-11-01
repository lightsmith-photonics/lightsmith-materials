# Lightsmith Materials

Lightsmith Materials is an open database of optical constants, served over a REST API. It covers n, k and
permittivity, and dispersive models ready for FDTD and FDFD. Every dataset is tied to its source publication,
keeps its original file, and has a version history.

Within Lightsmith it is the materials service:
- the community platform browses, visualises and moderates it;
- Lightsmith Studio and the simulation library load any dataset directly as a library material;
- the community contributes data through moderated submissions.

The initial content is the complete refractiveindex.info database (CC0 1.0), imported at a pinned commit. That
project is cited as the data source; it is not affiliated with Lightsmith.

| | |
| --- | --- |
| API reference | [API.md](API.md), plus the live OpenAPI document at `/openapi.json` (interactive docs at `/docs`) |
| Port and prefix | 8100, `/v1` |
| Code license | MIT ([LICENSE](LICENSE)). Each dataset carries its own data license (CC0-1.0, CC-BY-4.0, …). |
| Branding | `BRAND_NAME`, `LIBRARY_NAME` (see [.env.example](.env.example)) |

Conventions, the same as the simulation library:
- vacuum wavelengths in nm;
- time dependence exp(-iωt), so absorbing media have k > 0;
- linear interpolation, with no extrapolation.

## Run it with Docker

```bash
cp .env.example .env                       # set POSTGRES_PASSWORD and PLATFORM_JWT_SECRET
docker compose up -d --build               # postgres -> migrate (one-off, exits 0) -> api on :8100
docker compose --profile import run --rm importer   # one-off job: pinned refractiveindex.info snapshot
curl localhost:8100/healthz                # {"status":"ok","schema":"ok",...}
```

| Service | What it does |
| --- | --- |
| `db` | `postgres:17`, with the `pgdata` named volume and a `pg_isready` healthcheck. Published on `127.0.0.1:${POSTGRES_PORT:-5433}` for host-side tools; change `POSTGRES_PORT` if that port is taken. |
| `migrate` | Runs `materials-db migrate` (Alembic) once, then exits. The API waits for it to finish successfully. |
| `api` | Serves the API on `${API_BIND:-127.0.0.1}:${API_PORT:-8100}`. It never imports data on start. Its healthcheck returns 503 until the schema is at head. |
| `importer` | Behind the `import` profile. Runs `materials-db import-rii --if-needed --crossref`. It is idempotent: a commit that is already imported is skipped, and a changed file adds a dataset version. The commit comes from `RII_COMMIT`, and the `data` volume caches the archive and DOI metadata. |

The image is multi-stage and uses only the official `python:3.12-slim` image:
- The first stage installs `requirements.lock` with `pip --require-hashes`. That file is a hash-pinned export
  of `uv.lock`; regenerate it with `uv export --frozen --no-dev --no-emit-project -o requirements.lock`, and CI
  checks that it matches.
- The runtime stage runs as non-root uid 10001 and has a healthcheck.

In compose the containers have read-only root filesystems.

Some features need the simulation library, which is not bundled by default: the `/model` fits for
non-Sellmeier data and `materials-db fit-models`. To bundle it, build with
`--build-arg LIBRARY_SPEC=<pip requirement>`. The exact Sellmeier models need no library.

Authentication uses only the community platform's JWTs, signed with `PLATFORM_JWT_SECRET`. This service has no
user store.

## Develop

Development uses the compose Postgres, with the service running on the host:

```bash
cp .env.example .env
scripts/dev.sh        # or scripts\dev.ps1 on Windows: starts db, migrates, imports once, serves with --reload
uv run pytest         # unit tests (SQLite, the only place SQLite is accepted)
TEST_DATABASE_URL=postgresql+psycopg://materials:change-me@127.0.0.1:5433/materials uv run pytest   # against Postgres; this empties the database
uv run --extra library pytest   # with the sibling ../lightsmith library: also runs the parity and fit tests
```

| Command (`materials-db` or `lightsmith-materials`) | |
| --- | --- |
| `migrate` | Applies migrations. This is a separate step: nothing else migrates implicitly, except `import-rii --migrate`. |
| `import-rii [--commit C] [--source zip-or-dir] [--crossref] [--if-needed]` | Imports the database. The archive is downloaded from `codeload.github.com/…/zip/<commit>`; only `database/**.yml` members are read, and nothing is extracted or executed. |
| `enrich-references [--refresh]` | Fetches DOI metadata from Crossref, falling back to doi.org/DataCite. |
| `fit-models [--category metal,semiconductor,phase_change]` | Precomputes and caches Drude–Lorentz fits. Needs the library. |
| `token --role moderator` | Mints a platform-style JWT for development. |
| `serve`, `stats` | Runs the API; prints counts and the imported snapshot. |

## What is in the database

### Categories
Every material has one category, and `/v1/categories` returns them with counts and featured materials. The
counts below are research materials; the 1,784 supplier datasheets are counted separately.

| Category | Content | Research materials |
| --- | --- | --- |
| `semiconductor` | Semiconductors and photonic platforms: Si, SiN, InP, GaAs, LiNbO₃/TFLN, LiTaO₃, AlN, … | 78 |
| `glass_oxide` | Glasses, oxides and dielectrics. Supplier glass catalogs are also here. | 121 (+1,784 supplier) |
| `polymer` | Polymers and photoresists (SU-8, PMMA resists, IP-Dip, …) | 64 |
| `2d` | 2D materials (TMDs, MXenes, …) | 23 |
| `metal` | Metals and conductors | 64 |
| `liquid` | Liquids | 72 |
| `phase_change` | Phase-change materials: GST (crystalline and amorphous), VO₂. Sb₂Se₃, Sb₂S₃ and GeTe are recognised when they arrive. | 2 |
| `gas`, `biological`, `other` | | 19, 11, 10 |

### Tags
Materials also carry tags: `integrated-photonics`, `phase-change`, `anisotropic`, `photoresist` and
`supplier-datasheet`. Filter with `?tag=`; `?supplier=false` hides the datasheets. Curated aliases such as SiN,
TFLN and GST resolve to their materials.

### Photonics metadata

| Field | Description | Datasets |
| --- | --- | --- |
| `optical_axis` | o and e (uniaxial) or α, β, γ (biaxial). Datasets for the axes of one measurement share an axis group, and `anisotropy.axes` links them. Each axis exports separately as a library material. | 433 (264 groups) |
| `state` | amorphous, crystalline, polycrystalline, liquid, gas or solid, when the source names it | 332 |
| `thermo_optic` | dn/dT constants, or the Schott thermal-dispersion model with dn/dT evaluated at 633, 1310 and 1550 nm | 1,335 |
| Dispersive models (`/model`, `/lightsmith?model=drude_lorentz`) | The library's `drude_lorentz` kind. Sellmeier datasets (formulas 1 and 2) are rewritten exactly as undamped Lorentz poles, with no fitting. Other datasets are fitted with the library's `fit_drude_lorentz` over the data range clipped to 200 nm–20 µm, and the fit is cached. | 940 exact |

### Import statistics (commit c5c2f18, imported 2026-11-01)

| | |
| --- | --- |
| Catalog pages | 3,576 on 7 shelves |
| Datasets | 3,478, one per distinct data file (0 failures) |
| Materials | 2,248: 464 research materials and 1,784 supplier datasheets |
| References | 671 distinct. 514 have a DOI, and 513 of those resolved to Crossref or DataCite metadata. Every dataset also cites the source database. |
| Data types | 916 tabulated nk, 217 tabulated n, 19 tabulated k only, 37 tabulated n + k. The other 2,289 are formulas 1–9, including 1,684 with tabulated k. |
| Source-data flags | k < 0: 12. n ≤ 0: 10. Formula non-finite in part of its range: 5. No primary reference: 8. k covering only part of the n range: 272. Data are kept exactly as published. |

The parser, formulas and sampling are a copy of the library's reference implementation, and agree with it to
round-off (≤ 5×10⁻¹⁵) over the whole database. Slugs (`au/johnson-christy-1972`) and public IDs (`mat_…`,
`ds_…`) are deterministic and never change once assigned.

## Data model

Postgres is the only supported database. The schema is in `src/materials_db/models.py`, with Alembic migrations
in `src/materials_db/migrations`.

| Table | Purpose |
| --- | --- |
| `materials` | Slug, name, formula, aliases, category, tags, group, manufacturer, description |
| `datasets` | Belongs to a material. Data type, range, temperature, `optical_axis`, `axis_group`, `state`, `thermo_optic`, conditions, measurement method, sample notes, quality flags, license, status, current version |
| `dataset_versions` | Immutable. The numbers, losslessly, in the source structure; a metadata snapshot; content hash; the source file; commit or submission; contributor; change note |
| `source_files` | The original file, byte for byte, keyed by sha256 |
| `dispersion_models` | Cached fits per dataset version (library `drude_lorentz` JSON, band, errors) |
| `bib_references` + `dataset_references` | DOI, arXiv ID, title, authors, journal, year, raw DOI metadata. The link carries a role: `source`, `related` or `database`. |
| `licenses` | SPDX ID, and whether contributions may use the license |
| `contributors`, `submissions`, `submission_events` | Platform users (by JWT `sub`), the moderation queue (pending, approved or rejected), and its history |
| `dataset_aliases`, `catalog_entries`, `import_runs` | Alternative slugs, the imported shelf → book → page tree, and import provenance |

## Repository

This directory is self-contained, ready to become the `lightsmith-materials` repository. It has its own
`pyproject.toml`, `uv.lock`, `requirements.lock`, LICENSE, tests and fixtures, and `.github/workflows/ci.yml`, which runs the tests
on SQLite and Postgres, builds the image, and runs a compose smoke test. Nothing refers to paths outside it.
The simulation library is optional, installed with `--with-editable` in the monorepo or `LIBRARY_SPEC` in
Docker.

## Open questions for the owner

1. **Contribution licenses.** Contributions currently accept CC0-1.0, CC-BY-4.0 and PDDL-1.0. Share-alike and
   non-commercial licenses are refused. Should only CC0 be accepted, matching the source database?
2. **Contributor agreement.** Should contributors confirm that they hold the rights to the data, recorded on the
   submission?
3. **Publishing upstream.** Should approved CC0 contributions be offered back to the refractiveindex.info
   database?
4. **Supplier datasheets.** There are 1,784 datasheet materials, almost all in `glass_oxide`. They are tagged
   and searchable separately; should the UI hide them by default?
5. **Library package.** Docker images can bundle the library for fits (`LIBRARY_SPEC`). Which package name and
   version will Lightsmith publish?
6. **Upstream updates.** When should `RII_COMMIT` move forward? Updates arrive as new versions, and removed
   entries are withdrawn rather than deleted.
7. **Measurement method.** The source has no structured measurement-method field. Should we backfill a
   controlled vocabulary?
