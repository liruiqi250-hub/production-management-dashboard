"""合成资料的PI/装箱提取与最小模板填写，不包含客户原件。"""
import io
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

from app.lantern_documents import decimal_text, extract_packing, extract_pi, generate, read_rows, NS


def synthetic_template(root):
    folder = Path(root) / 'private/lantern_templates'
    folder.mkdir(parents=True)
    data = io.BytesIO()
    sheets = ['发票','合同','箱单','报关单','申报要素']
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="'+NS['s']+'" xmlns:r="'+NS['r']+'"><sheets>'+''.join(f'<sheet name="{n}" sheetId="{i}" r:id="r{i}"/>' for i,n in enumerate(sheets,1))+'</sheets><calcPr/></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1,6))+'</Relationships>')
        for i in range(1,6):
            archive.writestr(f'xl/worksheets/sheet{i}.xml','<worksheet xmlns="'+NS['s']+'"><sheetData><row r="12"><c r="B12" s="1" t="inlineStr"><is><t>旧目的国</t></is></c></row></sheetData><pageSetup scale="100"/></worksheet>')
        archive.writestr('xl/media/original-logo.png', b'synthetic-logo-preserved')
    (folder/'dayang.xlsx').write_bytes(data.getvalue())


class DocumentTests(unittest.TestCase):
    def test_ambiguous_decimal_comma_is_not_silently_changed(self):
        self.assertEqual(decimal_text('1,234.56'),'1234.56')
        with self.assertRaises(ValueError):
            decimal_text('1,12')

    def test_total_aliases_and_no_double_count(self):
        for marker in ('总计','合计','TOTAL:'):
            rows = [['名称','件数','毛重(kg)','净重(kg)','体积(CBM)'],['虚构产品','2','80','32','1.12'],[marker,'2','80','32','1.12']]
            result = extract_packing(rows)
            self.assertEqual(result['fields']['packages'],'2')
            self.assertEqual(result['fields']['gross_weight'],'80')
            self.assertEqual(result['fields']['volume'],'1.12')
            self.assertEqual(result['fields']['weight_unit'],'kg')
            self.assertEqual(result['fields']['volume_unit'],'m3')

    def test_mismatch_missing_units_and_multiple_totals(self):
        rows = [['名称','件数','毛重','净重','体积'],['虚构产品','2','80','32','1.12'],['总计','2','81','32','1.12']]
        result = extract_packing(rows)
        self.assertTrue(any('不一致' in w for w in result['warnings']))
        self.assertEqual(result['fields']['weight_unit'],'')
        with self.assertRaises(ValueError):
            extract_packing(rows+[['总计','2','81','32','1.12']])
        rows[-1][1]=''
        with self.assertRaises(ValueError):
            extract_packing(rows)

    def test_detail_fallback_uses_decimal_not_binary_float(self):
        rows = [['件数','毛重','净重','体积'],['1','0.1','0.1','0.1'],['1','0.2','0.2','0.2']]
        result = extract_packing(rows)
        self.assertEqual(result['fields']['volume'],'0.3')
        self.assertTrue(any('未找到总计' in w for w in result['warnings']))

    def test_pi_buyer_terms_currency_amount_and_no_bank_in_buyer(self):
        rows = [['Buyer:','Synthetic Buyer\nSynthetic Address','INV NO.: SYN-123'],
                ['Contract NO,: SYN-C123'],['COMMODITY','HS CODE','QUANTITY','TOTAL AMOUNT'],
                ['Synthetic lantern','1234567890','2','$99.88'],['TOTAL AMOUNT: USD test only'],
                ['Trade term: EXW\nTerms of Payment: synthetic T/T'],['Bank: sensitive-looking synthetic data']]
        fields = extract_pi(rows)['fields']
        self.assertEqual(fields['buyer'],'Synthetic Buyer')
        self.assertEqual(fields['buyer_address'],'Synthetic Address')
        self.assertEqual(fields['trade_terms'],'EXW')
        self.assertEqual(fields['amount'],'99.88')
        self.assertEqual(fields['currency'],'USD')
        self.assertNotIn('Bank', fields['buyer_address'])

    def test_docx_merged_grid_alignment(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('word/document.xml','<w:document xmlns:w="'+NS['w']+'"><w:body><w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>merged</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>next</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        self.assertEqual(read_rows('test.docx',data.getvalue()),[['merged','merged','next']])

    def test_template_preserves_parts_clears_stale_fields_and_fills_totals(self):
        with tempfile.TemporaryDirectory() as root:
            synthetic_template(root)
            fields={'buyer':'Synthetic Buyer','buyer_address':'Synthetic Address','trade_terms':'EXW','currency':'USD','amount':'99.88',
                    'quantity':'2','product_description':'Synthetic Lantern','packages':'1','gross_weight':'80','net_weight':'32','volume':'1.12',
                    'weight_unit':'kg','volume_unit':'m3','payment_terms':'synthetic 40% by T/T'}
            result=generate(root,'dayang',fields)
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                self.assertEqual(archive.read('xl/media/original-logo.png'),b'synthetic-logo-preserved')
                sheet=ET.fromstring(archive.read('xl/worksheets/sheet3.xml'))
                cells={x.get('r'):x for x in sheet.findall('.//s:c',NS)}
                self.assertEqual(cells['D18'].findtext('s:v',namespaces=NS),'1')
                self.assertEqual(cells['E18'].findtext('s:v',namespaces=NS),'80')
                self.assertEqual(cells['G18'].findtext('s:v',namespaces=NS),'1.12')
                customs=ET.fromstring(archive.read('xl/worksheets/sheet4.xml'))
                old=customs.find('.//s:c[@r="B12"]',NS)
                self.assertEqual(list(old),[])
                invoice=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                self.assertIsNone(invoice.find('.//s:c[@r="D3"]/s:f',NS))
                self.assertIn('Synthetic Buyer',archive.read('xl/worksheets/sheet1.xml').decode())
            with self.assertRaises(ValueError):
                generate(root,'dayang',{**fields,'weight_unit':''})
            with self.assertRaises(ValueError):
                generate(root,'dayang',{**fields,'net_weight':'81'})
            with self.assertRaises(ValueError):
                generate(root,'dayang',{**fields,'packages':'1.5'})

    def test_rejects_unsafe_office_package(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('../evil.xml','data')
        with self.assertRaises(ValueError):
            read_rows('test.docx',data.getvalue())
