"""Synthetic ZIP workbook tests; no Excel process or user workbook modification."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xlsm_table_fill import fill_jvet_hhi_test_sheet, _NS, _build_cell_map, _get_cell_value


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.template = self.root / "template.xlsm"
        def text(ref, value):
            return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
        sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                 '<row r="1">' + ''.join(text(f'{c}1', v) for c, v in zip('ABCDEF', ['Sequence', 'kbps', 'Ypsnr', 'Upsnr', 'Vpsnr', 'ErrorInfo'])) + '</row>'
                 '<row r="2">' + text('A2', 'Clip.Q22.ecm.lb') + '<c r="B2"><v>9999</v></c></row>'
                 '<row r="3">' + text('A3', 'Other.Q37.ecm.ra') + '<c r="B3"><v>8888</v></c></row>'
                 '</sheetData></worksheet>')
        with zipfile.ZipFile(self.template, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Reference" sheetId="1" r:id="rId1"/><sheet name="Test" sheetId="2" r:id="rId2"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', sheet)
            z.writestr('xl/worksheets/sheet2.xml', sheet)
            z.writestr('xl/vbaProject.bin', b'fake macro; never executed')

    def test_copy_preserves_reference_macro_template_and_clears_stale_test(self):
        before = hashlib.sha256(self.template.read_bytes()).hexdigest()
        output = self.root / 'out.xlsm'
        ok, message = fill_jvet_hhi_test_sheet(template_xlsm=self.template, output_xlsm=output,
            results=[dict(sequence='Clip', qp=22, kbps=123.4, ypsnr=40, upsnr=41, vpsnr=42, error_info='pass')], condition_tag='lb')
        self.assertTrue(ok, message)
        self.assertEqual(before, hashlib.sha256(self.template.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.template) as src, zipfile.ZipFile(output) as dst:
            for name in ('xl/worksheets/sheet1.xml', 'xl/vbaProject.bin'):
                self.assertEqual(src.read(name), dst.read(name))
            cells = _build_cell_map(ET.fromstring(dst.read('xl/worksheets/sheet2.xml')))
            self.assertEqual(_get_cell_value(cells['B2'], []), 123.4)
            self.assertIsNone(_get_cell_value(cells['B3'], []))

    def test_failed_row_not_written_as_measurement(self):
        output = self.root / 'out.xlsm'
        ok, _ = fill_jvet_hhi_test_sheet(template_xlsm=self.template, output_xlsm=output,
            results=[dict(sequence='Clip', qp=22, kbps=123.4, error_info='fail')], condition_tag='lb')
        self.assertTrue(ok)
        with zipfile.ZipFile(output) as dst:
            cells = _build_cell_map(ET.fromstring(dst.read('xl/worksheets/sheet2.xml')))
            self.assertIsNone(_get_cell_value(cells['B2'], []))

    def test_cannot_overwrite_template(self):
        ok, _ = fill_jvet_hhi_test_sheet(template_xlsm=self.template, output_xlsm=self.template,
                                       results=[], condition_tag='lb')
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
