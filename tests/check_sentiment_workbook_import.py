"""Validate user labels and reject changed questions, duplicates and invalid labels."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
import review_sentiment_manual as review

path = ROOT / 'outputs/01a156e0-87c6-7021-a360-85bf2e57c2b3/情感标签人工复核表.xlsx'
samples = json.loads(review.SAMPLES.read_text(encoding='utf-8'))['samples']
before = review.digest(path)
supplied = review.read_workbook(path, samples)
assert len(supplied) == 24
assert all(item['label'] in (*review.LABELS, '待定') for item in supplied.values())
with ZipFile(path) as archive:
    entries = {name: archive.read(name) for name in archive.namelist()}
ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

for address, replacement in [('A10', 'S01'), ('B9', 'changed question'), ('C9', 'invalid'), ('C9', '')]:
    sheet = ET.fromstring(entries['xl/worksheets/sheet1.xml'])
    cell = sheet.find(f'.//s:c[@r="{address}"]', ns)
    cell.clear()
    cell.set('r', address)
    cell.set('t', 'inlineStr')
    ET.SubElement(ET.SubElement(cell, f'{{{ns["s"]}}}is'), f'{{{ns["s"]}}}t').text = replacement
    altered = dict(entries, **{'xl/worksheets/sheet1.xml': ET.tostring(sheet)})

    class InMemoryArchive:
        def __init__(self, _): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, name): return altered[name]
        def namelist(self): return list(altered)

    with patch.object(review, 'ZipFile', InMemoryArchive):
        try:
            review.read_workbook(path, samples)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'Accepted invalid review field {address}')
assert review.digest(path) == before
print('passed: 24 user labels readable; duplicates, changed questions, invalid and blank labels rejected; original workbook unchanged')
