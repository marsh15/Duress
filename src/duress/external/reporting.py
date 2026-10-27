"""Portable CI and HTML reports from stored evidence; never execute on render."""
import html
import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring


def junit(record: dict) -> str:
    scenarios = record.get("scenarios", [])
    suite = Element("testsuite", name="duress-business-effects", tests=str(len(scenarios)),
                    failures=str(sum(s["result"]["status"] == "FAIL" for s in scenarios)),
                    skipped=str(sum(s["result"]["status"] == "INCONCLUSIVE" for s in scenarios)))
    for scenario in scenarios:
        case = SubElement(suite,"testcase",classname=record["project"],name=scenario["scenario_id"])
        status = scenario["result"]["status"]
        if status != "PASS":
            element = SubElement(case,"failure" if status == "FAIL" else "skipped",message=status)
            element.text = json.dumps({"result":scenario["result"],"dna":record.get("dna",[]),
                "violations":[r for t in scenario["trials"] for r in t["invariants"] if r["status"] != "satisfied"]},indent=2)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(suite,encoding="unicode")


def html_report(record: dict) -> str:
    title = html.escape(str(record.get("project", "External investigation")))
    content = html.escape(json.dumps(record,indent=2))
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Duress — {title}</title><style>body{{font:16px system-ui;max-width:1100px;margin:3rem auto;padding:1rem;color:#202020}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f0;padding:1rem}}h1{{font-size:2rem}}</style><h1>{title}</h1><p>Recorded business-state investigation. Results apply to the declared contract and bounded scenarios.</p><pre>{content}</pre></html>'


def export_run(record: dict, *, output: Path | None = None, junit_path: Path | None = None,
               artifacts: Path | None = None, config: Path | None = None):
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(record,indent=2)+"\n")
    if junit_path:
        junit_path.parent.mkdir(parents=True,exist_ok=True)
        junit_path.write_text(junit(record))
    if artifacts:
        from .manifest import load_project
        from .storage import ProjectStore
        store=ProjectStore(load_project(config).manifest.duress.project)
        artifacts.mkdir(parents=True,exist_ok=True)
        for dna_id in record.get("dna",[]):
            (artifacts/f"{dna_id}.json").write_text(json.dumps(store.load("dna",dna_id)["payload"],indent=2)+"\n")
