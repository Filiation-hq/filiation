"""Unit tests for ingestion check findings and their link to the bias register (#46, #92).

Task map:
    Input: CheckSpec declarations (stable check id, severity, bias-register factor ids) and the
        findings that checks emit against a subject (a source or partition).
    Output: validated, serializable findings; a registry of every declared check, so each
        register factor's controls can be listed and checked against docs/bias-register.md;
        ExportIntegrityError carrying every failing finding, not only the first.
    Edge cases: a malformed register id ("R1", "r18", "R018"); the same id listed twice; an
        unknown severity; a check id that is not dotted lowercase; two different specs
        registered under one id; the same spec registered twice (module re-import); a check
        that controls no register factor; several failures in one export reported together;
        a report-severity finding that must never stop a run.
    Invariants: check ids are unique and stable, since manifests and the register cite them;
        to_dict keys are fixed, so a later run manifest can record findings without a rename.

The module under test is imported inside each test so this file still imports on the base
branch, where the module does not exist yet (#137).
"""

import importlib
import json

import pytest
from filiation.core.storage.raw_store import LocalRawStore
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError, ingest_export
from openfda_fakes import PART_URL, PARTITION_URL, export_body, opener_serving, records


def findings():
    """Imports the findings module.

    Returns:
        module: filiation.pipeline.crawler.findings.
    """
    return importlib.import_module("filiation.pipeline.crawler.findings")


@pytest.mark.parametrize("bad", ["R1", "r18", "R018", "18", ""], ids=["one-digit", "lowercase", "three-digit", "no-prefix", "empty"])
def test_check_spec_rejects_a_malformed_register_id(bad):
    # Sample: register ids that are not R + two digits. The register numbers factors R01..R46;
    # a typo would silently detach a control from its factor.
    with pytest.raises(ValueError, match="register"):
        findings().CheckSpec("export_counts.total", "fail", (bad,))


def test_check_spec_rejects_a_register_id_listed_twice():
    # Sample: ("R18", "R18"). Duplicates would double-count a control in register summaries.
    with pytest.raises(ValueError, match="register"):
        findings().CheckSpec("export_counts.total", "fail", ("R18", "R18"))


def test_check_spec_rejects_an_unknown_severity():
    # Sample: severity "warn". Only "fail" (stop the run) and "report" (record and continue)
    # exist; anything else would leave the run's behaviour undefined.
    with pytest.raises(ValueError, match="severity"):
        findings().CheckSpec("export_counts.total", "warn", ("R18",))


@pytest.mark.parametrize("bad", ["total", "Export_counts.total", "export counts.total", "export_counts."], ids=["undotted", "uppercase", "space", "trailing-dot"])
def test_check_spec_rejects_a_check_id_that_is_not_dotted_lowercase(bad):
    # Sample: ids without a "<group>.<check>" shape. Ids are cited in manifests and the
    # register, so they must be stable, greppable identifiers.
    with pytest.raises(ValueError, match="check id"):
        findings().CheckSpec(bad, "fail", ("R18",))


def test_check_spec_allows_a_check_that_controls_no_register_factor():
    # Sample: a zip-validity check with register_ids (). Not every integrity check guards a
    # bias factor; forcing a tag would put false controls in the register.
    assert findings().CheckSpec("export_zip.valid", "fail", ()).register_ids == ()


def test_register_rejects_two_different_specs_under_one_check_id():
    # Sample: "export_counts.total" registered as fail/R18, then as report/R14, in a fresh
    # registry. One id must mean one check.
    module = findings()
    registry = {}
    module.register(module.CheckSpec("export_counts.total", "fail", ("R18",)), registry)
    with pytest.raises(ValueError, match="export_counts.total"):
        module.register(module.CheckSpec("export_counts.total", "report", ("R14",)), registry)


def test_register_accepts_an_equal_spec_registered_again_and_keeps_the_first():
    # Sample: fail/R18 "export_counts.total" registered, then a new but equal CheckSpec object
    # registered again, as a module re-import builds. The second call must not raise and must
    # hand back the first object, leaving one entry.
    module = findings()
    registry = {}
    first = module.register(module.CheckSpec("export_counts.total", "fail", ("R18",)), registry)
    again = module.register(module.CheckSpec("export_counts.total", "fail", ("R18",)), registry)
    assert again is first
    assert registry == {"export_counts.total": first}


def test_finding_to_dict_has_fixed_json_serializable_keys():
    # Sample: a fail finding for R18 on one partition. A future manifest field will store
    # these dicts, so the keys and their JSON types are part of the contract.
    module = findings()
    spec = module.CheckSpec("export_counts.total", "fail", ("R18",))
    data = spec.finding("510k/device-510k-0001-of-0001.json.zip", "total is 4, partitions hold 3").to_dict()
    assert json.loads(json.dumps(data)) == {
        "check": "export_counts.total",
        "severity": "fail",
        "register_ids": ["R18"],
        "subject": "510k/device-510k-0001-of-0001.json.zip",
        "message": "total is 4, partitions hold 3",
    }


def test_failures_keeps_fail_findings_and_drops_report_findings():
    # Sample: one fail and one report finding. A report finding (e.g. #92's completeness)
    # must never stop a run.
    module = findings()
    fail = module.CheckSpec("export_counts.total", "fail", ("R18",)).finding("510k", "m")
    report = module.CheckSpec("completeness.field", "report", ("R14",)).finding("510k", "m")
    assert module.failures([fail, report]) == [fail]


def test_export_count_checks_are_registered_with_their_register_factors():
    # Sample: the registry after importing openfda_bulk. Count mismatches control R18 (a lost
    # partition would read as upstream removals in the weekly diff); a missing meta.results
    # block is a silently empty field (R14).
    registry = findings().CHECKS
    counts = {cid: spec for cid, spec in registry.items() if cid.startswith("export_counts.")}
    assert {spec.severity for spec in counts.values()} == {"fail"}
    assert counts["export_counts.meta_results"].register_ids == ("R14",)
    assert all(spec.register_ids == ("R18",) for cid, spec in counts.items() if cid != "export_counts.meta_results")


def test_ingest_export_reports_every_failing_count_check_not_only_the_first(tmp_path):
    # Sample: two partitions, each with meta.results.limit 1 for 2 records (both wrong). One
    # run must surface both, so a broken export is diagnosed in one pass.
    def bad_limit(start, skip):
        return json.dumps({"meta": {"results": {"skip": skip, "limit": 1, "total": 4}}, "results": records(start, 2)})

    parts = [(PART_URL.format(n=1), bad_limit(1, 0), 2), (PART_URL.format(n=2), bad_limit(3, 2), 2)]
    with pytest.raises(ExportIntegrityError) as raised:
        ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_serving(parts))
    limit_findings = [f for f in raised.value.findings if f.check.check_id == "export_counts.partition_limit"]
    assert [f.subject for f in limit_findings] == [
        "510k/device-510k-0001-of-0002.json.zip",
        "510k/device-510k-0002-of-0002.json.zip",
    ]


def test_ingest_export_error_findings_carry_the_register_factor(tmp_path):
    # Sample: one 3-record partition whose meta total says 4. The raised error's finding names
    # R18, so the run log ties the failure to the factor it protects.
    parts = [(PARTITION_URL, export_body(records(1, 3), total=4), 3)]
    with pytest.raises(ExportIntegrityError) as raised:
        ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_serving(parts))
    assert [(f.check.check_id, f.check.register_ids) for f in raised.value.findings] == [
        ("export_counts.total", ("R18",))
    ]
