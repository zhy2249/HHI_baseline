"""Synthetic OOXML/CSV tests only; no Excel, VBA, video, or codec execution."""
import csv
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import import_experiment_results as importer
import xlsm_table_fill as xml


SS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CHART = 'http://schemas.openxmlformats.org/drawingml/2006/chart'


def text_cell(address, value):
    return f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def result_row(key, rate=123.4):
    return [key, str(rate), '40.1', '41.2', '42.3', '10.5', '1.25', '2048', '1024',
            'pass', 'Sat Oct 31 09:00:00 2026']


class ImportExperimentResultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.template = self.root / 'template.xlsm'
        self.csv = self.root / 'input.csv'
        self.output = self.root / 'experiment1' / 'results.xlsm'
        keys = ['Other.Q37.jvet10.ra', 'Clip.Q22.jvet10.lb', 'Clip.Q27.jvet10.lb',
                'Unused.Q22.jvet10.lp', 'Unused.Q22.jvet10.lp']
        raw_rows = []
        for row, key in enumerate(keys, 1):
            measurements = ''.join(f'<c r="{col}{row}" s="3"><v>999</v></c>' for col in 'BCDEFGHI')
            measurements += text_cell(f'J{row}', 'old-pass') + text_cell(f'K{row}', 'old-time')
            raw_rows.append(f'<row r="{row}" ht="18">' + text_cell(f'A{row}', key) + measurements
                            + f'<c r="L{row}" s="7"><f>B{row}*2</f><v>1998</v></c>'
                            + f'<c r="M{row}"><v>77</v></c></row>')
        raw_rows.append('<row r="8">' + text_cell('A8', 'Notes') + text_cell('B8', 'keep note') + '</row>')
        raw_sheet = (f'<worksheet xmlns="{SS}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
                     ' xmlns:xr="urn:original-xr" mc:Ignorable="xr"><sheetData>'
                     + ''.join(raw_rows) + '</sheetData></worksheet>').encode()
        summary = (f'<worksheet xmlns="{SS}"><sheetData>'
                   '<row r="1"><c r="A1"><f>Test!B2</f><v>999</v></c>'
                   '<c r="B1"><f t="array" ref="B1:B2">Test!B2:B3</f><v>999</v></c>'
                   '<c r="C1" t="str"><f>Test!J2</f><v>old-pass</v></c>'
                   '<c r="D1"><v>765</v></c></row>'
                   '<row r="2"><c r="B2"><v>888</v></c></row></sheetData></worksheet>').encode()
        workbook = (f'<workbook xmlns="{SS}" xmlns:r="{REL}"'
                    ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
                    ' xmlns:x15="urn:x15" xmlns:xr="urn:xr" xmlns:xr6="urn:xr6"'
                    ' xmlns:xr10="urn:xr10" xmlns:xr2="urn:xr2" mc:Ignorable="x15 xr xr6 xr10 xr2">'
                    '<sheets><sheet name="Reference" sheetId="1" r:id="rId1"/>'
                    '<sheet name="Test" sheetId="2" r:id="rId2"/>'
                    '<sheet name="Summary" sheetId="3" r:id="rId3"/></sheets>'
                    '<calcPr calcId="191029" calcMode="manual"/>'
                    '<extLst><ext uri="original-extension"/></extLst></workbook>').encode()
        relationships = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + ''.join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in (1, 2, 3))
                         + '</Relationships>').encode()
        chart = (f'<c:chartSpace xmlns:c="{CHART}"><c:chart><c:plotArea><c:lineChart><c:ser>'
                 '<c:val><c:numRef><c:f>Test!$B$1:$B$2</c:f><c:numCache><c:formatCode>0.0</c:formatCode>'
                 '<c:ptCount val="2"/><c:pt idx="0"><c:v>999</c:v></c:pt></c:numCache></c:numRef></c:val>'
                 '<c:tx><c:strRef><c:f>Test!$A$1</c:f><c:strCache><c:ptCount val="1"/>'
                 '<c:pt idx="0"><c:v>old label</c:v></c:pt></c:strCache></c:strRef></c:tx>'
                 '<c:cat><c:multiLvlStrRef><c:f>Test!$A$1:$A$2</c:f><c:multiLvlStrCache>'
                 '<c:ptCount val="2"/></c:multiLvlStrCache></c:multiLvlStrRef></c:cat>'
                 '<c:cat><c:numLit><c:ptCount val="1"/><c:pt idx="0"><c:v>42</c:v></c:pt></c:numLit></c:cat>'
                 '</c:ser></c:lineChart></c:plotArea></c:chart></c:chartSpace>').encode()
        self.parts = {
            'xl/workbook.xml': workbook,
            'xl/_rels/workbook.xml.rels': relationships,
            'xl/worksheets/sheet1.xml': raw_sheet,
            'xl/worksheets/sheet2.xml': raw_sheet,
            'xl/worksheets/sheet3.xml': summary,
            'xl/charts/chart1.xml': chart,
            'xl/vbaProject.bin': b'FAKE VBA; never execute',
            'xl/styles.xml': b'<styles>unchanged style payload</styles>',
        }
        self.write_template()

    def write_template(self):
        with zipfile.ZipFile(self.template, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b'preserve archive comment'
            for name, data in self.parts.items():
                archive.writestr(name, data)

    def write_csv(self, rows, header_last=True):
        with self.csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            if not header_last:
                writer.writerow(importer.CSV_COLUMNS)
            writer.writerows(rows)
            if header_last:
                writer.writerow(importer.CSV_COLUMNS)

    def import_data(self):
        return importer.import_results(self.csv, self.template, self.output)

    def read_cells(self, part):
        with zipfile.ZipFile(self.output) as archive:
            return xml._build_cell_map(ET.fromstring(archive.read(part)))

    def test_header_last_empty_slots_and_normalized_api(self):
        self.write_csv([['Clip.Q27.ecm.lb'], result_row('Other.Q37.ecm.ra'),
                        ['Unused.Q22.ecm.lp'] + [''] * 10])
        rows = importer.read_csv(self.csv)
        self.assertEqual([(r['seq'], r['qp'], r['tag']) for r in rows],
                         [('Clip', 27, 'lb'), ('Other', 37, 'ra'), ('Unused', 22, 'lp')])
        self.assertEqual(rows[1]['sequence'], 'Other')
        self.assertEqual(rows[1]['enc_vmpeak_kb'], 2048)
        self.assertFalse(rows[0]['measured'])
        self.assertIsNone(rows[0]['kbps'])
        self.assertEqual(len(importer.read_raw_sheet(self.template)), 5)

    def test_reordered_keys_preserve_sources_reference_vba_styles_and_labels(self):
        self.write_csv([result_row('Clip.Q22.ecm.lb'), result_row('Other.Q37.ecm.ra', 234.5),
                        ['Clip.Q27.ecm.lb']])
        before = hashlib.sha256(self.template.read_bytes()).hexdigest()
        source_csv = self.csv.read_bytes()
        report = self.import_data()
        self.assertEqual(report['written_rows'], 2)
        self.assertEqual(report['measured'], 2)
        self.assertEqual(report['accepted_pass_rows'], 2)
        self.assertEqual(report['nonpass_rows'], 0)
        self.assertEqual(before, hashlib.sha256(self.template.read_bytes()).hexdigest())
        self.assertEqual(source_csv, self.csv.read_bytes())
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.comment, b'preserve archive comment')
            for name in ('xl/worksheets/sheet1.xml', 'xl/vbaProject.bin', 'xl/styles.xml', 'xl/_rels/workbook.xml.rels'):
                self.assertEqual(archive.read(name), self.parts[name])
            workbook = archive.read('xl/workbook.xml')
            self.assertEqual(workbook.split(b'<calcPr')[0], self.parts['xl/workbook.xml'].split(b'<calcPr')[0])
            for prefix in ('x15', 'xr', 'xr6', 'xr10', 'xr2'):
                self.assertIn(f'xmlns:{prefix}="urn:{prefix}"'.encode(), workbook)
        cells = self.read_cells('xl/worksheets/sheet2.xml')
        self.assertEqual(xml._get_cell_value(cells['B1'], []), 234.5)
        self.assertEqual(xml._get_cell_value(cells['B2'], []), 123.4)
        self.assertEqual(cells['B2'].get('s'), '3')
        self.assertEqual(xml._get_cell_value(cells['A2'], []), 'Clip.Q22.jvet10.lb')
        self.assertEqual(xml._get_cell_value(cells['M2'], []), 77)
        self.assertEqual(xml._get_cell_value(cells['B8'], []), 'keep note')
        for row in (3, 4, 5):
            for column in 'BCDEFGHIJK':
                self.assertIsNone(xml._get_cell_value(cells[f'{column}{row}'], []))
        out = importer.read_raw_sheet(self.output, 'Test')
        self.assertEqual([r['row'] for r in out if r['measured']], [1, 2])

    def test_caches_invalidated_and_formulas_literals_reference_preserved(self):
        self.write_csv([result_row('Clip.Q22.ecm.lb')])
        report = self.import_data()
        self.assertEqual(report['invalidated_formula_cells'], 9)  # 5 Test plus 4 Summary/array cells
        self.assertEqual(report['invalidated_chart_caches'], 3)
        with zipfile.ZipFile(self.output) as archive:
            for name in ('xl/worksheets/sheet2.xml', 'xl/worksheets/sheet3.xml'):
                before = ET.fromstring(self.parts[name])
                after = ET.fromstring(archive.read(name))
                self.assertEqual([(f.text, f.attrib) for f in before.iter(xml._q('ss:f'))],
                                 [(f.text, f.attrib) for f in after.iter(xml._q('ss:f'))])
                for cell in after.iter(xml._q('ss:c')):
                    if cell.find(xml._q('ss:f')) is not None:
                        self.assertIsNone(cell.find(xml._q('ss:v')))
            summary = xml._build_cell_map(ET.fromstring(archive.read('xl/worksheets/sheet3.xml')))
            self.assertIsNone(summary['B2'].find(xml._q('ss:v')))
            self.assertEqual(xml._get_cell_value(summary['D1'], []), 765)
            chart = archive.read('xl/charts/chart1.xml')
            for cache in (b'numCache', b'strCache', b'multiLvlStrCache'):
                self.assertNotIn(cache, chart)
            self.assertIn(b'<c:numLit>', chart)
            self.assertIn(b'<c:v>42</c:v>', chart)
            self.assertEqual(ET.fromstring(chart).findall('.//{' + CHART + '}f')[0].text, 'Test!$B$1:$B$2')
            calc = ET.fromstring(archive.read('xl/workbook.xml')).find(xml._q('ss:calcPr'))
            for name, value in {'calcMode': 'auto', 'fullCalcOnLoad': '1', 'forceFullCalc': '1'}.items():
                self.assertEqual(calc.get(name), value)

    def test_input_and_existing_output_cannot_be_overwritten(self):
        self.write_csv([result_row('Clip.Q22.ecm.lb')])
        for source in (self.template, self.csv):
            before = source.read_bytes()
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'input source'):
                importer.import_results(self.csv, self.template, source)
            self.assertEqual(source.read_bytes(), before)
        self.output.parent.mkdir()
        self.output.write_bytes(b'existing output')
        with self.assertRaises(FileExistsError):
            self.import_data()
        self.assertEqual(self.output.read_bytes(), b'existing output')

    def test_duplicate_csv_measurement_ignores_only_layout_label(self):
        self.write_csv([result_row('Clip.Q22.ecm.lb'), result_row('Clip.Q22.jvet10.lb')])
        with self.assertRaisesRegex(ValueError, 'duplicate CSV task'):
            self.import_data()
        self.assertFalse(self.output.exists())

    def test_measured_missing_duplicate_or_other_tag_is_rejected(self):
        for key in ('Unknown.Q22.ecm.lb', 'Unused.Q22.ecm.lp', 'Clip.Q22.ecm.ldb'):
            with self.subTest(key=key):
                self.write_csv([result_row(key)])
                with self.assertRaisesRegex(ValueError, 'exactly one Test row'):
                    self.import_data()
                self.assertFalse(self.output.exists())

    def test_unmeasured_duplicate_and_missing_slots_only_reported(self):
        self.write_csv([result_row('Clip.Q22.ecm.lb'), ['Unused.Q22.ecm.lp'], ['Absent.Q37.ecm.ai']])
        report = self.import_data()
        self.assertEqual(report['missing_unmeasured_keys'], ['Absent.Q37.ecm.ai'])
        self.assertEqual(report['duplicate_unmeasured_keys'],
                         [{'seq': 'Unused', 'qp': 22, 'tag': 'lp', 'rows': [4, 5]}])

    def test_missing_cells_are_inserted_in_column_order_without_touching_other_regions(self):
        self.parts['xl/worksheets/sheet2.xml'] = self.parts['xl/worksheets/sheet2.xml'].replace(
            b'<c r="B2" s="3"><v>999</v></c>', b'').replace(b'<c r="E2" s="3"><v>999</v></c>', b'')
        self.write_template()
        self.write_csv([result_row('Clip.Q22.ecm.lb')], header_last=False)
        self.import_data()
        cells = self.read_cells('xl/worksheets/sheet2.xml')
        self.assertEqual(xml._get_cell_value(cells['B2'], []), 123.4)
        self.assertEqual(xml._get_cell_value(cells['E2'], []), 42.3)
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
            row = next(row for row in root.iter(xml._q('ss:row')) if row.get('r') == '2')
            self.assertEqual([cell.get('r') for cell in row], [f'{column}2' for column in 'ABCDEFGHIJKLM'])

    def test_calcpr_inserted_before_workbook_extensions(self):
        self.parts['xl/workbook.xml'] = self.parts['xl/workbook.xml'].replace(
            b'<calcPr calcId="191029" calcMode="manual"/>', b'')
        self.write_template()
        self.write_csv([result_row('Clip.Q22.ecm.lb')])
        self.import_data()
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read('xl/workbook.xml'))
            self.assertEqual([child.tag.rsplit('}', 1)[-1] for child in root], ['sheets', 'calcPr', 'extLst'])

    def test_nonfinite_numeric_and_incomplete_pass_are_rejected(self):
        for kind in ('nan', 'missing'):
            row = result_row('Clip.Q22.ecm.lb')
            row[2] = 'nan' if kind == 'nan' else ''
            self.write_csv([row])
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.import_data()
            self.assertFalse(self.output.exists())

    def test_formula_in_target_measurement_is_not_overwritten(self):
        self.parts['xl/worksheets/sheet2.xml'] = self.parts['xl/worksheets/sheet2.xml'].replace(
            b'<c r="B2" s="3"><v>999</v></c>', b'<c r="B2" s="3"><f>Reference!B2</f><v>999</v></c>')
        self.write_template()
        self.write_csv([result_row('Clip.Q22.ecm.lb')])
        with self.assertRaisesRegex(ValueError, 'cannot overwrite a formula'):
            self.import_data()
        self.assertFalse(self.output.exists())

    def test_failed_measurements_preserved_in_csv_but_not_written_into_formulas(self):
        failed = result_row('Clip.Q22.ecm.lb')
        failed[9] = 'fail'
        self.write_csv([failed, result_row('Other.Q37.ecm.ra')])
        raw = importer.read_csv(self.csv)
        self.assertTrue(raw[0]['measured'])
        self.assertEqual(raw[0]['kbps'], 123.4)
        report = self.import_data()
        self.assertEqual(report['written_rows'], 2)
        self.assertEqual(report['accepted_pass_rows'], 1)
        self.assertEqual(report['nonpass_rows'], 1)
        self.assertEqual(report['nonpass_rows_with_raw_measurements'], 1)
        cells = self.read_cells('xl/worksheets/sheet2.xml')
        for column in 'BCDEFGHI':
            self.assertIsNone(xml._get_cell_value(cells[f'{column}2'], []))
        self.assertEqual(xml._get_cell_value(cells['J2'], []), 'fail')
        self.assertEqual(xml._get_cell_value(cells['K2'], []), failed[10])
        self.assertEqual(cells['L2'].find(xml._q('ss:f')).text, 'B2*2')
        self.assertIsNone(cells['L2'].find(xml._q('ss:v')))
        written = importer.read_raw_sheet(self.output, 'Test')
        self.assertFalse(written[1]['measured'])
        self.assertEqual(written[1]['error_info'], 'fail')

    def test_numeric_record_without_pass_status_is_not_accepted(self):
        unverified = result_row('Clip.Q22.ecm.lb')
        unverified[9] = ''
        self.write_csv([unverified])
        report = self.import_data()
        self.assertEqual(report['accepted_pass_rows'], 0)
        self.assertEqual(report['nonpass_rows'], 1)
        record = importer.read_raw_sheet(self.output, 'Test')[1]
        self.assertFalse(record['measured'])
        self.assertIsNone(record['error_info'])
        self.assertEqual(record['time'], unverified[10])


if __name__ == '__main__':
    unittest.main()
