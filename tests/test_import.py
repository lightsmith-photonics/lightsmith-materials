"""Import of the database: mapping, idempotency, version history, provenance."""

from __future__ import annotations

import shutil

from sqlalchemy import func, select

from materials_db.importer import DirectorySource, import_rii
from materials_db.models import CatalogEntry, Dataset, DatasetVersion, Material, Reference, SourceFile
from materials_db.naming import stable_id
from materials_db.rii import entry_blocks, parse_yaml

from .conftest import COMMIT, FIXTURE


def count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def test_mapping(db):
    with db.session() as s:
        slugs = set(s.scalars(select(Material.slug)))
        assert slugs == {"au", "co", "baf2", "co-carbon-monoxide", "sio2", "aggas2", "ge2sb2te5", "linbo3",
                         "methane", "schott-n-bk7"}
        assert count(s, Dataset) == 12  # one per data file; re-listings do not duplicate
        assert count(s, CatalogEntry) == 15  # every catalog page, including duplicates and selected shelves
        sio2 = s.scalar(select(Material).where(Material.slug == "sio2"))
        assert "Fused silica (fused quartz)" in sio2.aliases  # glass/fused_silica re-lists SiO2 data
        assert sio2.formula == "SiO2" and sio2.category == "glass_oxide"
        au = s.scalar(select(Material).where(Material.slug == "au"))
        assert au.category == "metal" and "Gold" in au.aliases and au.description.startswith("Gold")
        assert s.scalar(select(Material.category).where(Material.slug == "methane")) == "gas"
        bk7 = s.scalar(select(Material).where(Material.slug == "schott-n-bk7"))
        assert (bk7.category, bk7.manufacturer) == ("glass_oxide", "SCHOTT")
        assert "supplier-datasheet" in bk7.tags
        pop = s.scalar(select(CatalogEntry).where(CatalogEntry.shelf == "popular_glass"))
        assert pop.dataset_id == bk7.datasets[0].id


def test_slugs_and_ids(db):
    with db.session() as s:
        jc = s.scalar(select(Dataset).where(Dataset.slug == "au/johnson-christy-1972"))
        assert jc.public_id == stable_id("ds", "rii:main/Au/nk/Johnson.yml")
        assert {"au/johnson", "au/jc", "au/johnson-christy"} <= {a.slug for a in jc.aliases}
        assert s.scalar(select(Dataset.slug).where(Dataset.source_key == "rii:main/Co/nk/Johnson.yml")) == \
            "co/johnson-christy-1974"
        assert s.scalar(select(Dataset.slug).where(Dataset.source_key.like("%N-BK7.yml"))) == "schott-n-bk7/schott"


def test_references_and_provenance(db):
    with db.session() as s:
        for ds in s.scalars(select(Dataset)):
            roles = [link.role for link in ds.reference_links]
            assert "database" in roles  # the compiling database is always cited
            assert ds.license_id == "CC0-1.0"
            v = ds.versions[-1]
            assert v.source_commit == COMMIT
            raw = (FIXTURE / v.source_file.path).read_bytes()
            assert v.source_file.content == raw  # original file kept verbatim
            assert v.blocks == entry_blocks(parse_yaml(raw.decode("utf-8")))  # lossless values
        boyd = s.scalar(select(Dataset).where(Dataset.slug.like("aggas2/%")))
        assert [link.reference.doi for link in boyd.reference_links if link.role == "source"] == \
            ["10.1109/jqe.1971.1076588", "10.1364/ao.15.0305_1"]
        assert s.scalar(select(func.count()).where(Reference.doi == "10.1038/s41597-023-02898-2")) == 1
        bk7 = s.scalar(select(Dataset).where(Dataset.slug == "schott-n-bk7/schott"))
        assert bk7.versions[-1].meta["properties"]["nd"] == "1.5168"
        baf2 = s.scalar(select(Dataset).where(Dataset.slug.like("baf2/%")))
        assert baf2.data_type == "tabulated k" and baf2.temperature_k == 5.0


def test_import_is_idempotent(db, fixture_source):
    with db.session() as s:
        before = (count(s, Material), count(s, Dataset), count(s, DatasetVersion), count(s, Reference),
                  count(s, SourceFile), count(s, CatalogEntry), sorted(s.scalars(select(Dataset.slug))))
    with db.session() as s:
        stats = import_rii(s, fixture_source, COMMIT)
    assert stats.datasets_unchanged == 12 and stats.datasets_created == stats.datasets_updated == 0
    with db.session() as s:
        after = (count(s, Material), count(s, Dataset), count(s, DatasetVersion), count(s, Reference),
                 count(s, SourceFile), count(s, CatalogEntry), sorted(s.scalars(select(Dataset.slug))))
    assert before == after


def test_new_snapshot_adds_versions_and_withdraws(db, tmp_path):
    root = tmp_path / "rii-next"
    shutil.copytree(FIXTURE, root)
    jc = root / "database/data/main/Au/nk/Johnson.yml"
    jc.write_text(jc.read_text(encoding="utf-8").replace("0.1879 1.28 1.188", "0.1879 1.27 1.188"),
                  encoding="utf-8")
    cat = root / "database/catalog-nk.yml"
    text = cat.read_text(encoding="utf-8")
    start = text.index("    - DIVIDER: \"Co - Cobalt\"")
    end = text.index("    - DIVIDER: \"F - Fluorides\"")
    cat.write_text(text[:start] + text[end:], encoding="utf-8")
    with db.session() as s:
        stats = import_rii(s, DirectorySource(root), "next-commit")
    assert (stats.datasets_updated, stats.datasets_withdrawn, stats.datasets_unchanged) == (1, 1, 10)
    with db.session() as s:
        ds = s.scalar(select(Dataset).where(Dataset.slug == "au/johnson-christy-1972"))
        assert ds.current_version == 2 and [v.version for v in ds.versions] == [1, 2]
        assert ds.versions[0].blocks[0]["n"][0] == 1.28 and ds.versions[1].blocks[0]["n"][0] == 1.27
        assert ds.versions[1].source_commit == "next-commit"
        co = s.scalar(select(Dataset).where(Dataset.slug == "co/johnson-christy-1974"))
        assert co.status == "withdrawn"
