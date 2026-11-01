"""Community submissions: intake, moderation, publication."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import validation as v
from .auth import Principal
from .models import (Contributor, Dataset, DatasetAlias, DatasetReference, DatasetVersion, Material, SourceFile,
                     Submission, SubmissionEvent, utcnow)
from .naming import random_id, search_text, slugify
from .optics import Dispersion
from .references import DoiResolver, apply_metadata, get_or_create
from .rii import data_type

log = logging.getLogger(__name__)

META_FIELDS = ("material_id", "material_name", "formula", "category", "aliases", "dataset_name", "name",
               "target_dataset_id", "reference", "citation", "doi", "arxiv", "url", "license", "comments",
               "wavelength_unit", "unit", "columns", "format", "temperature_k", "conditions",
               "measurement_method", "sample_notes", "change_note", "optical_axis", "axis_partner_id", "state",
               "thermo_optic")


def upsert_contributor(session: Session, p: Principal) -> Contributor:
    c = session.scalar(select(Contributor).where(Contributor.external_id == p.sub))
    if c is None:
        c = Contributor(external_id=p.sub)
        session.add(c)
    c.name, c.role, c.last_seen_at = p.name or c.name, p.role, utcnow()
    session.flush()
    return c


def create_submission(session: Session, principal: Principal, meta: dict, *, content: bytes, filename: str,
                      media_type: str, rows: list | None = None, resolver: DoiResolver | None = None,
                      resolve_material=None, resolve_dataset=None) -> Submission:
    """Validate and store a submission. Raises :class:`validation.SubmissionError` on errors."""
    meta = {k: meta[k] for k in META_FIELDS if meta.get(k) not in (None, "")}
    report = v.Report()
    blocks = v.rows_to_blocks(rows, report) if rows is not None else v.parse_upload(content, filename, meta, report)
    material = target = None
    if meta.get("target_dataset_id"):
        target = resolve_dataset(meta["target_dataset_id"])
        if target is None:
            report.errors.append(f"target dataset {meta['target_dataset_id']!r} not found")
    if meta.get("axis_partner_id"):
        partner = resolve_dataset(meta["axis_partner_id"])
        if partner is None:
            report.errors.append(f"axis partner dataset {meta['axis_partner_id']!r} not found")
        elif not meta.get("optical_axis"):
            report.errors.append("axis_partner_id needs optical_axis")
        else:
            meta["axis_partner_id"] = partner.public_id
    if meta.get("material_id"):
        material = resolve_material(meta["material_id"])
        if material is None:
            report.errors.append(f"material {meta['material_id']!r} not found")
    if target is not None and material is not None and target.material_id != material.id:
        report.errors.append("target_dataset_id belongs to a different material")
    v.check_metadata(session, meta, report, material=material, target=target)
    if blocks:
        v.summarize_blocks(blocks, report)
    if report.errors:
        raise v.SubmissionError(report.as_dict())

    ref = get_or_create(session, citation=meta.get("reference") or meta.get("citation") or
                        f"doi:{meta.get('doi') or ''}{meta.get('arxiv') or ''}",
                        doi=meta.get("doi"), arxiv=meta.get("arxiv"), url=meta.get("url"), metadata_source="user")
    if resolver is not None and ref.doi and ref.doi_checked_at is None:
        try:
            msg = resolver.resolve(ref.doi)
            apply_metadata(ref, msg)
            if msg is None:
                report.warnings.append(f"DOI {ref.doi} was not found by Crossref or doi.org")
        except (httpx.HTTPError, ValueError) as e:
            log.warning("DOI lookup failed for %s: %s", ref.doi, e)
            report.warnings.append(f"DOI metadata could not be fetched now ({type(e).__name__}); retried later")

    contributor = upsert_contributor(session, principal)
    sha = hashlib.sha256(content).hexdigest()
    sf = session.scalar(select(SourceFile).where(SourceFile.sha256 == sha))
    if sf is None:
        sf = SourceFile(sha256=sha, filename=filename[:300], media_type=media_type, size=len(content),
                        content=content, origin_url=None)
        session.add(sf)
        session.flush()
    proposed = None
    if material is None and target is None:
        proposed = {"name": meta.get("material_name"), "formula": meta.get("formula"),
                    "category": (meta.get("category") or "other").lower(), "aliases": meta.get("aliases") or []}
    sub = Submission(public_id=random_id("sub"), contributor=contributor, status="pending",
                     kind="new_version" if target is not None else "new_dataset",
                     material_id=(target.material_id if target is not None else material.id if material else None),
                     proposed_material=proposed, target_dataset_id=target.id if target is not None else None,
                     meta=meta, blocks=blocks, validation=report.as_dict(), license_id=meta["license"],
                     reference=ref, source_file=sf)
    sub.events.append(SubmissionEvent(actor=contributor, action="submitted",
                                      notes=f"{len(report.warnings)} validation warning(s)"))
    session.add(sub)
    session.flush()
    return sub


def review_submission(session: Session, sub: Submission, principal: Principal, decision: str,
                      notes: str | None) -> Submission:
    if sub.status != "pending":
        raise ValueError(f"submission is already {sub.status}")
    reviewer = upsert_contributor(session, principal)
    sub.reviewer, sub.reviewed_at = reviewer, datetime.now(timezone.utc)
    sub.moderation_notes = notes
    if decision == "reject":
        sub.status = "rejected"
        sub.events.append(SubmissionEvent(actor=reviewer, action="rejected", notes=notes))
        return sub
    if decision != "approve":
        raise ValueError("decision must be 'approve' or 'reject'")
    ds = publish(session, sub, reviewer)
    sub.status, sub.result_dataset = "approved", ds
    sub.events.append(SubmissionEvent(actor=reviewer, action="approved", notes=notes))
    session.flush()
    return sub


def _unique(session: Session, model_col, base: str) -> str:
    slug, i = base, 2
    while session.scalar(select(model_col).where(model_col == slug)) is not None:
        slug, i = f"{base}-{i}", i + 1
    return slug


def publish(session: Session, sub: Submission, reviewer: Contributor) -> Dataset:
    meta = sub.meta
    if sub.target_dataset is not None:
        ds = sub.target_dataset
    else:
        mat = sub.material
        if mat is None:
            p = sub.proposed_material or {}
            name = p.get("name") or "Unnamed material"
            mat = Material(public_id=random_id("mat"), slug=_unique(session, Material.slug, slugify(p.get("formula") or name)),
                           name=name, formula=p.get("formula"), aliases=list(p.get("aliases") or []),
                           category=p.get("category") or "other", source="community")
            mat.search_text = search_text(mat.slug, name, mat.formula, *mat.aliases)
            session.add(mat)
            session.flush()
            sub.material = mat
        ref = sub.reference
        family = (ref.authors[0].get("family") if ref and ref.authors else None) or ""
        part = slugify(f"{family} {ref.year or ''}") if family and ref.year else slugify(
            meta.get("dataset_name") or meta.get("name") or "dataset")
        slug, i = f"{mat.slug}/{part}", 2
        while (session.scalar(select(Dataset.id).where(Dataset.slug == slug)) is not None
               or session.get(DatasetAlias, slug) is not None):
            slug, i = f"{mat.slug}/{part}-{i}", i + 1
        ds = Dataset(public_id=random_id("ds"), slug=slug, material=mat, source="submission",
                     license_id=sub.license_id, current_version=0,
                     position=10_000 + sub.id)
        session.add(ds)
    disp = Dispersion(sub.blocks)
    lo, hi = disp.range_nm
    ds.name = meta.get("dataset_name") or meta.get("name") or ds.name or _default_name(sub)
    ds.data_type, ds.has_k = data_type(sub.blocks), disp.has_k
    ds.wavelength_min_nm, ds.wavelength_max_nm = lo, hi
    ds.n_points = None if disp.is_formula else int(len(disp.native_nm()))
    ds.temperature_k = float(meta["temperature_k"]) if meta.get("temperature_k") is not None else None
    ds.conditions = meta.get("conditions") or {}
    ds.measurement_method, ds.sample_notes = meta.get("measurement_method"), meta.get("sample_notes")
    ds.comments = meta.get("comments")
    ds.quality_flags = list(disp.notes)
    ds.state = meta.get("state") or ds.state
    ds.thermo_optic = meta.get("thermo_optic") or ds.thermo_optic
    if meta.get("optical_axis"):
        ds.optical_axis = meta["optical_axis"]
        partner = (session.scalar(select(Dataset).where(Dataset.public_id == meta["axis_partner_id"]))
                   if meta.get("axis_partner_id") else None)
        if partner is not None and partner.axis_group is None:
            partner.axis_group = f"community:{partner.public_id}"
        ds.axis_group = partner.axis_group if partner is not None else (ds.axis_group or f"community:{ds.public_id}")
        if "anisotropic" not in (ds.material.tags or []):
            ds.material.tags = [*(ds.material.tags or []), "anisotropic"]
    ds.license_id, ds.status = sub.license_id, "published"
    ds.current_version += 1
    vmeta = {"name": ds.name, "comments": ds.comments, "conditions": ds.conditions,
             "measurement_method": ds.measurement_method, "sample_notes": ds.sample_notes,
             "submission": sub.public_id, "validation_warnings": sub.validation.get("warnings", [])}
    chash = hashlib.sha256(json.dumps([sub.blocks, vmeta], sort_keys=True).encode()).hexdigest()
    ds.versions.append(DatasetVersion(
        version=ds.current_version, blocks=sub.blocks, meta=vmeta, content_hash=chash,
        source_file_id=sub.source_file_id, contributor_id=sub.contributor_id, submission_id=sub.id,
        change_note=meta.get("change_note") or f"community submission {sub.public_id}, approved by "
                                                f"{reviewer.name or reviewer.external_id}"))
    if sub.reference is not None and all(link.reference_id != sub.reference.id for link in ds.reference_links):
        ds.reference_links.insert(0, DatasetReference(reference=sub.reference, position=0, role="source"))
        for i, link in enumerate(ds.reference_links):
            link.position = i
    session.flush()
    return ds


def _default_name(sub: Submission) -> str:
    ref = sub.reference
    if ref and ref.authors and ref.year:
        fam = [a.get("family") for a in ref.authors if a.get("family")]
        who = fam[0] + (" et al." if len(fam) > 2 else f" and {fam[1]}" if len(fam) == 2 else "")
        return f"{who} {ref.year}"
    return f"Community dataset {sub.public_id}"
