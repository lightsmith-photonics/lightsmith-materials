# Lightsmith Materials API (`/v1`)

The machine-readable contract is the OpenAPI document at `GET /openapi.json` (interactive docs at `/docs`).
This file explains the conventions and lists every endpoint, including the ones added beyond the original brief.
The community platform (`apps/web`) and Studio (`apps/studio`) build against it, so keep existing fields and
parameters stable. Adding fields is fine; renaming or removing them needs a `/v2`.

Default base URL in development: `http://localhost:8100`.

## Conventions

- **Units.** Vacuum wavelength in nm unless a parameter says otherwise. Energies are in eV and wavenumbers in
  cm⁻¹. The absorption coefficient is in cm⁻¹ and the skin depth in nm.
- **Sign convention.** The time dependence is exp(-iωt), so absorbing media have k > 0 and Im(ε) > 0
  (the same as the simulation library).
- **IDs.** Every material and dataset has two identifiers, and both work wherever a `{material_id}` or
  `{dataset_id}` is expected:
  - a stable public ID: `mat_…` for materials and `ds_…` for datasets. Imported records get deterministic IDs
    (a hash of their source path), so they are the same on every deployment.
  - a slug: `au` for a material, `au/johnson-christy-1972` for a dataset.

  Dataset slugs contain a `/`. Use them as-is in paths (`/v1/datasets/au/johnson-christy-1972/data`); `%2F`
  also works. Old or alternative slugs (`au/johnson`, `au/jc`) still resolve as aliases. Materials also resolve
  by exact alias or formula (`gold`, `Au`).
- **Pagination.** List endpoints accept `limit`/`offset`, or the aliases `per_page`/`page` (1-based). They
  return `{items, total, limit, offset, page, per_page}`. The default page size is 50 and the maximum is 200.
- **Nulls.** Non-finite numbers are serialised as `null`, for example a value outside a dataset's range on a
  common grid, or the skin depth where k = 0.
- **Errors.** Errors return `{"error": "<code>", "message": "...", "detail": ...}` with an HTTP status:
  - `401 unauthorized` and `403 forbidden` for authentication and role failures;
  - `404 not_found`;
  - `409 conflict`;
  - `413 too_large`;
  - `422`: `invalid_request`, `invalid_range`, `invalid_quantity`, `invalid_category`, `no_overlap`,
    `no_n_data`, `no_model`, `fit_failed`, or `validation_failed` (in which case `detail.validation` holds the
    full report);
  - `501 fit_unavailable`;
  - `503 auth_not_configured`.
- **CORS.** `http://localhost:3000` (web) and `http://localhost:5173` (studio) are always allowed, along with
  their `127.0.0.1` equivalents. Add more origins with `CORS_ORIGINS`.

## Authentication (writes only)

Submission endpoints need `Authorization: Bearer <JWT>`. The token is signed by the web platform with HS256
using the shared `PLATFORM_JWT_SECRET`.

| Claim | Requirement |
| --- | --- |
| `sub` | Required: the platform user ID. |
| `name` | Optional. |
| `role` | `user` (the default), `moderator` or `admin`. |
| `exp` | Enforced when present, with 30 s leeway. |
| `aud` | When present, must include `materials-db`. Change this with `PLATFORM_JWT_AUDIENCE`. |
| `iss` | Checked only if `PLATFORM_JWT_ISSUER` is set. |

`moderator` and `admin` can review submissions. For local testing, mint a token with
`uv run materials-db token --role moderator`.

## Read endpoints

### `GET /v1/materials?q=&category=&tag=&supplier=`
Searches materials.
- `q` is a substring match on name, formula, alias and slug. Matches rank in this order: exact slug, exact
  formula, a whole name or alias (`silicon`, `TFLN`), then prefixes. Supplier datasheets rank after research
  materials.
- `category` takes one category or a comma-separated list, using the IDs from `/v1/categories`.
- `tag` is one of `integrated-photonics`, `phase-change`, `anisotropic`, `photoresist` or
  `supplier-datasheet`.
- `supplier=false` returns research data only; `supplier=true` returns only datasheets.

Each item has these fields:
- `id`, `slug`, `name`, `name_html`, `formula`;
- `category`, `tags`, `anisotropic`, `group`, `manufacturer`, `aliases`;
- `description`, shortened to about 280 characters in lists;
- `dataset_count`, `range_nm` (`[min, max]`), `default_dataset_id`;
- `datasets`: a list of `DatasetBrief` objects, each with `id`, `slug`, `name`, `type`, `range_nm`, `has_k`,
  `year`, `doi`, `reference`, `is_default`, `optical_axis` and `state`.

### `GET /v1/materials/{material_id}`
Returns the full material: the complete `description`, the `datasets` list (as `DatasetSummary` objects),
`source`, `catalog` (the places it is listed in the imported catalog), `links`, `created_at` and `updated_at`.

The default dataset is the one the simulation library uses for that material when there is one, for example
Johnson and Christy for Au. Otherwise it is the first dataset in catalog order.

### `GET /v1/categories` *(added)*
Returns the categories for browsing, in display order:

`[{id, name, description, count, research_count, supplier_count, featured: [MaterialRef]}]`

| ID | Name |
| --- | --- |
| `semiconductor` | Semiconductors and photonic platforms (Si, SiN, InP, GaAs, LiNbO3/TFLN, …) |
| `glass_oxide` | Glasses, oxides and dielectrics |
| `polymer` | Polymers and photoresists |
| `2d` | 2D materials |
| `metal` | Metals and conductors |
| `liquid` | Liquids |
| `phase_change` | Phase-change materials (GST, Sb2Se3, VO2) |
| `gas`, `biological`, `other` | |

`featured` lists the materials to show first in each category.

**Changed in 0.2:** `dielectric`, `glass`, `organic` and `mixture` were replaced by `glass_oxide` and
`other`. Migration `0002` maps existing rows.

### `GET /v1/datasets?q=&material=&category=&has_k=&covers_nm=&doi=&source=` *(added)*
Searches datasets.
- `covers_nm=500` or `covers_nm=400,900` keeps only datasets whose range contains that wavelength or interval.
- `doi` lists every dataset that cites a DOI.
- `source` is `rii` or `submission`.

### `GET /v1/datasets/{dataset_id}`
Returns the dataset metadata as a `DatasetSummary` plus details.

`DatasetSummary` has these fields:
- identity: `id`, `slug`, `name`, `material_id`, `material {id, slug, name, formula, category}`;
- data: `type` / `data_type` (`tabulated nk`, `tabulated n`, `tabulated k`, `formula N`, or blocks joined with
  ` + `), `range_nm`, `has_k`, `n_points`, `temperature_k`;
- citation: `year`, `doi`, `reference` (formatted citation), `authors`;
- status: `group`, `license`, `comments`, `status`, `current_version`, `is_default`;
- `source_url`: the page on refractiveindex.info, for imported data.

The detail adds these fields:
- conditions: `conditions` (as given by the source: temperature, substrate, film thickness, crystal
  direction, …), `sample_notes`, `measurement_method`;
- `optical_axis` and `state`;
- `anisotropy`: `{kind: "uniaxial" | "biaxial", axis, axes: {"o": {id, slug}, "e": {…}}, complete}` for
  axis-resolved data, linking the datasets for the other axes of the same measurement;
- `thermo_optic`: a list of entries. Each is either `{dn_dT (1/K), wavelength_nm?, wavelength_range_nm?,
  temperature_k?, direction?, origin}`, or
  `{model: "schott", coefficients: {D0, D1, D2, E0, E1, lambda_tk_um}, reference_temperature_k,
  dn_dT_at: {"633": …, "1310": …, "1550": …}, origin}`;
- `models`: the dispersive models already available, each `{kind, method: "exact" | "fit", band_nm, url}`,
  plus `model_fit_available` (whether this server can fit new ones);
- `quality_flags`: problems found in the source data, which is kept as published. Examples are k < 0, n ≤ 0,
  a formula that is non-finite in part of its range, k given only over part of the range, or no primary
  reference;
- `properties`: for example glass catalog data such as nd, Vd and thermal properties;
- `references`: each entry has `role` (`source`, `related` or `database`) plus `doi`, `title`, `authors`,
  `journal`, `year`, `citation` and `formatted`. Structured fields come from Crossref or DataCite when the DOI
  resolves;
- `references_html`, `license_info {id, name, url, requires_attribution, share_alike}`;
- `source`: database, shelf, book, page, path, commit, URL and raw-file URL;
- `versions`, `aliases`, `citation` (the text to cite), `links` (`self`, `data`, `lightsmith`, `model`,
  `source_file`, `versions`, `material`), `created_at`, `updated_at`.

### `GET /v1/datasets/{dataset_id}/data`
Returns the dataset's values on a grid.

| Parameter | Values | Default |
| --- | --- | --- |
| `quantity` | `nk`, `eps`, `alpha`, `skin_depth`, `all`, or a list such as `n,eps2` | `nk` |
| `axis` | `wavelength_nm`, `wavelength_um`, `ev`, `wavenumber` | `wavelength_nm` |
| `min`, `max` | Grid limits in axis units. They are clipped to the data range, and a warning is added. | data range |
| `points` | 1–20000: a uniform grid in the axis variable | Native points. Formulas: 400 log-spaced points plus the k points, the same sampling as the library. |
| `format` | `json`, `csv`, `yaml` (or use the `Accept` header) | `json` |
| `version` | Any earlier version of the dataset | current |

The JSON response looks like this:

```json
{"dataset": {"id": "ds_…", "slug": "au/johnson-christy-1972", "name": "…", "version": 1, "material": {…}},
 "quantity": "nk", "axis": "wavelength_nm", "axis_unit": "nm",
 "wavelength_nm": [187.9, …], "n": [1.28, …], "k": [1.188, …],
 "units": {"wavelength_nm": "nm", "n": "1", "k": "1"},
 "points": 49, "range_nm": [187.9, 1937.0], "warnings": [], "license": "CC0-1.0", "citation": "…"}
```

`wavelength_nm` is always present. When the axis is not nm, an extra array named after the axis (`ev`,
`wavenumber`, `wavelength_um`) is added. Quantity arrays are named `n`, `k`, `eps1`, `eps2`, `alpha` and
`skin_depth`.

Interpolation is linear in wavelength and never extrapolates (the same as the simulation library). At native points the
values are exactly the published ones.

The exports work as follows:
- **CSV**: `#` header lines carry the dataset, license, citation and conventions, followed by the columns.
- **YAML** with `quantity=nk` and a wavelength axis: a refractiveindex.info-format entry in µm, readable by
  the library's `materials.read_refractiveindex_yaml`.
- **YAML** in any other case: the JSON payload rendered as YAML.

### `GET /v1/compare?datasets=a,b,c`
Puts up to 12 datasets on one grid. It accepts the same `quantity`, `axis`, `min`, `max` and `format`
parameters as `/data`, plus:
- `points`: default 500;
- `spacing`: `linear` or `log`;
- `range`: `union` (the default; values outside a dataset's range are `null`) or `intersection` (returns 422
  `no_overlap` if the datasets have no common range).

The response looks like this:

```json
{"quantity": "nk", "axis": "wavelength_nm", "axis_unit": "nm", "range_mode": "union", "range_nm": [lo, hi],
 "units": {…}, "warnings": [],
 "items": [{"dataset": DatasetSummary, "data": {"wavelength_nm": […], "n": […], "k": […]},
            "range_nm": [lo, hi], "warnings": […]}]}
```

The CSV export has one column per dataset and quantity, named `<slug>:n`, `<slug>:k` and so on.

### `GET /v1/datasets/{dataset_id}/lightsmith`
Returns the dataset as a simulation-library material, ready to put into a `Simulation`.

`/omniscatter` is a deprecated alias of this endpoint, with identical behaviour.

By default (`model=tabulated`) the response is the library's `Tabulated` kind:

```json
{"kind": "tabulated", "wavelength_nm": […], "n_real": […], "n_imag": […], "source": "<citation; dataset slug, version, license, Lightsmith Materials URL; source commit>"}
```

Parameters:
- `min`, `max` (nm): restrict the range;
- `points`: log-spaced resampling;
- `version`.

With `model=drude_lorentz` the response is instead the library's `drude_lorentz` kind,
`{"kind": "drude_lorentz", "eps_inf", "wp_ev", "gamma_ev", "poles": [{delta_eps, omega0_ev, gamma_ev}]}`, the same
model as `/model`. The headers `X-Model-Method` and `X-Model-Band-Nm` describe it.

The response headers are `X-Dataset-Id`, `X-Dataset-Slug`, `X-Dataset-Version`, plus `X-Warnings` when there
are warnings. Datasets with k but no n return 422 `no_n_data`.

Anisotropic datasets export one axis per request. Use `anisotropy.axes` to get the others.

### `GET /v1/datasets/{dataset_id}/model` *(added)*
Returns a dispersive model for FDTD and FDFD:
`{dataset, kind: "drude_lorentz", method, material, band_nm, errors, notes}`.

- `method: "exact"`: the dataset is a Sellmeier formula (types 1 and 2), rewritten losslessly as undamped
  Lorentz poles. It is valid over the formula's range, and needs no library.
- `method: "fit"`: a passive Drude–Lorentz fit from the library's `materials_fit.fit_drude_lorentz`, over the
  data range clipped to 200 nm–20 µm. The fit is computed once and cached per dataset version, and `errors`
  holds the RMS and maximum relative errors.

If the server has no library, it returns 501 `fit_unavailable`.

### `GET /v1/datasets/{dataset_id}/fit/drude-lorentz?min=&max=&n_poles=&drude=` *(added)*
Fits over a custom band. This needs the library, and the result is not cached. It returns
`{dataset, material: <drude_lorentz>, fit: {rms_rel_error, …}, band_nm, warnings}`.

### `GET /v1/datasets/{dataset_id}/source?version=` *(added)*
Returns the original file verbatim: the YAML from the source database, or the uploaded CSV, YAML or JSON. The
`X-Content-SHA256` header carries its hash.

### `GET /v1/datasets/{dataset_id}/versions` *(added)*
Returns the version history as a list of objects with `version`, `created_at`, `change_note`,
`source_commit`, `content_hash`, `submission_id`, `contributor` and `source_file {sha256, filename, size,
path, origin_url}`. Past versions stay readable through `?version=` on `/data`, `/lightsmith` and `/source`.

### Other read endpoints *(added)*
- `GET /v1/references?doi=&q=` and `GET /v1/references/{reference_id or DOI}`: references, including the
  datasets that cite them. Use these to check for duplicates before submitting.
- `GET /v1/catalog` returns the shelves of the imported catalog. `GET /v1/catalog?shelf=main` returns that
  shelf's books and pages, with links to datasets and materials.
- `GET /v1/licenses`: licenses, and which ones contributions may use.
- `GET /v1/quantities`: quantities, units and axes.
- `GET /v1/stats`: counts, and the imported snapshot commit.
- `GET /healthz`: returns 200 `{status: "ok", schema: "ok"}` when the database is reachable and migrated, and
  503 otherwise.

## Submissions (authenticated)

### `POST /v1/submissions`
A submission can be sent in two ways.

**1. JSON** (`application/json`; this is what `apps/web` sends):

```json
{"material_id": "au" | null, "material_name": "…", "formula": "…", "category": "metal",
 "dataset_name": "Smith et al. 2025: evaporated film", "target_dataset_id": null,
 "reference": "full citation", "doi": "10.…", "arxiv": null, "url": null,
 "license": "CC-BY-4.0", "comments": "…", "temperature_k": 295, "conditions": {"substrate": "Si"},
 "measurement_method": "ellipsometry", "sample_notes": "…", "change_note": null,
 "optical_axis": "e", "axis_partner_id": "linbo3/zelmon-1997-o", "state": "crystalline",
 "thermo_optic": [{"dn_dT": 3.3e-5, "wavelength_nm": 1550}],
 "data": [[wavelength_nm, n, k], …]}
```

**2. Multipart** (`multipart/form-data`), with two parts:
- `file`: one of these formats:
  - CSV, TSV or whitespace-separated text. The first column is wavelength, energy or wavenumber, followed by
    `n`, `k` or `eps1`, `eps2` columns.
  - refractiveindex.info-style YAML. Its units are µm by definition, and all formula types are accepted.
- `metadata`: a JSON string with the same fields as the JSON form, except `data`. It can also include
  `wavelength_unit` (`nm`, `um`, `eV`, `cm-1`) and `columns` (`"nk"`, `"n"`, `"eps"`, or a list such as
  `"wavelength,n,k"`).

Units must be declared, either in `wavelength_unit` or in the CSV header (`wavelength_nm`, `wavelength (um)`,
`energy_eV`). Permittivity input is converted to n and k.

Exactly one of these must be given:
- `material_id`: an existing material, given by ID, slug or alias;
- `material_name` + `category`: proposes a new material;
- `target_dataset_id`: a new version of an existing dataset.

**Validation.** These problems are errors, and the endpoint answers 422 `validation_failed` with
`detail.validation = {errors, warnings, stats}`:
- units not declared;
- fewer than 2 points, or non-finite values;
- wavelengths that are not positive;
- duplicate or non-monotonic wavelengths;
- n ≤ 0, k < 0, or Im(ε) < 0;
- a source not cited: no DOI, no arXiv ID, and no citation of at least 10 characters;
- an invalid DOI or arXiv syntax;
- a license not accepted for contributions;
- an unknown material, target dataset or category;
- an invalid `optical_axis` (o, e, x, y, z, alpha, beta, gamma, a, b, c) or `state`;
- an implausible `thermo_optic` value (|dn/dT| ≥ 1e-2 /K);
- `axis_partner_id` without `optical_axis`.

`axis_partner_id` links a new axis dataset to an existing one, so they appear together under `anisotropy`.

These are warnings; the submission is stored and the moderator sees them:
- descending order, which is sorted automatically;
- spikes or outliers, found with a robust neighbour-median test;
- implausible magnitudes or ranges;
- duplicate DOIs (already cited for the same material, or by a pending submission);
- a DOI unknown to Crossref and doi.org;
- k given over only part of the range.

The response is `201` with a `Submission`:
- `id` (`sub_…`), `status` (`pending`, `approved` or `rejected`), `kind` (`new_dataset` or `new_version`);
- material: `material_id`, `material`, `proposed_material`, `target_dataset_id`, `material_name`,
  `category`;
- dataset: `dataset_name`, `reference`, `doi`, `license`, `comments`, `metadata`;
- review: `validation`, `reference_info` (filled from DOI metadata), `submitted_by {sub, name, role}`,
  `reviewer`, `review_note`, `result_dataset_id`, `result_dataset_slug`;
- data: `file {filename, size, sha256}`, `data_summary {type, range_nm, points, has_k}`, `data`
  (`[[wavelength_nm, n, k], …]`);
- history: `events`, `created_at`, `reviewed_at`.

### `GET /v1/submissions?status=&mine=&include_data=`
Moderators see every submission and other users see their own. The response is a paginated envelope. `data`
rows are included only with `include_data=true`.

### `GET /v1/submissions/{id}` and `GET /v1/submissions/{id}/file` *(added)*
These return one submission (with `data`) and the uploaded file verbatim. They are available to the owner or a
moderator.

### `POST /v1/submissions/{id}/review` (moderator)
The body is `{"decision": "approve" | "reject", "notes": "…"}`, and `note` is accepted as an alias.

On approval:
- a proposed material is created;
- the dataset is published (the slug is `<material>/<first-author>-<year>` when DOI metadata gives one) with
  the submitted license and reference, or a new version is appended to the target dataset;
- version 1 (or N+1) points to the verbatim upload and the submission.

Reviewing a submission that is not pending returns 409.

## Changes in 0.2 (Lightsmith rename and photonics scope)

- `/lightsmith` export added. `/omniscatter` is kept as a deprecated alias.
- New endpoints: `/model` and `?model=drude_lorentz`.
- New fields: `tags`, `anisotropic`, `optical_axis`, `state`, `anisotropy`, `thermo_optic`, `models`.
- New category IDs (see `/v1/categories`).
- New submission fields: `optical_axis`, `axis_partner_id`, `state`, `thermo_optic`.

## Differences from `web-api-expectations.md`

- List endpoints return `{items, total, page, per_page, limit, offset}`, including `GET /v1/submissions`.
- `GET /v1/compare` returns `{…, items: [{dataset, data}]}`, not a bare array.
- `DatasetSummary.type` and `range_nm` are as expected. Extra fields are always present (`slug`, `material`,
  `data_type`, …).
