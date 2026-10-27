#!/usr/bin/env python3
"""Copy configuration-only integration files into the independent application."""
import argparse
import json
import shutil
from pathlib import Path


def setup(application:Path):
    root=Path(__file__).resolve().parents[1]
    destination=application.resolve()/'duress'
    destination.mkdir(parents=True,exist_ok=True)
    for name in ('contract.json','events.json','seed.json'):
        shutil.copy2(root/'examples'/'ticketing'/name,destination/name)
    scenarios=json.loads((root/'examples'/'ticketing'/'scenarios.json').read_text())
    for variant in ('flawed','fixed','normal'):
        # TOML nested scenario dictionaries are encoded as inline tables by a
        # small data-only encoder. No application or engine logic is generated.
        text=f'''[duress]
project = "ticketing-{variant}"
[target]
id = "ticketing-{variant}"
mode = "managed"
cwd = ".."
init = ["python", "-m", "ticketing", "init"]
serve = ["python", "-m", "ticketing", "serve"]
isolated_test_database = true
[target.env]
DURESS_VARIANT = "{variant}"
DURESS_ALLOW_TEST_DATABASE = "1"
[delivery]
endpoint = "/webhooks"
signature = "hmac-sha256"
[observation]
adapter = "postgresql@1"
database_ref = "DURESS_TICKETING_DB"
[contract]
file = "contract.json"
[fixtures]
file = "seed.json"
events_file = "events.json"
'''
        for scenario in scenarios:
            text+='\n[[scenarios]]\n'+''.join(f'{k} = {toml(v)}\n' for k,v in scenario.items())
        (destination/f'{variant}.toml').write_text(text)
    return destination


def toml(value):
    if isinstance(value,dict):
        return '{'+', '.join(json.dumps(k)+' = '+toml(v) for k,v in value.items())+'}'
    if isinstance(value,list): return '['+', '.join(toml(v) for v in value)+']'
    return json.dumps(value)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application',type=Path)
    args=parser.parse_args()
    print(setup(args.application))
