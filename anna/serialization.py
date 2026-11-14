"""Deterministic JSON identity and catalog export formats."""
import csv
from hashlib import sha256
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def csv_write(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in rows:
            w.writerow({k:canonical(v) if isinstance(v,(dict,list,tuple,set)) else v for k,v in r.items() if k in fields})


def jsonl_write(path,rows):
    with path.open('w',encoding='utf-8') as f:
        for row in rows:f.write(canonical(row)+'\n')

