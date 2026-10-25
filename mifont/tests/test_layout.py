from copy import deepcopy
import unittest

from mifont import layout
from mifont.font_export import normalize_glyph


class SourceLayoutTests(unittest.TestCase):
    def test_italic_overhang_does_not_become_extra_advance(self):
        # A 20-pixel body has a 30-pixel top overhang. Its font must retain
        # that overhang while advancing only by the body and specimen gap.
        line={'baseline_y':100,'reference_height':70}
        regions=[]
        for offset,char in [(0,'a'),(24,'b')]:
            runs=[[row,offset,offset+20] for row in range(65,96)]
            runs.append([10,offset+25,offset+50])
            regions.append({'char':char,'box':[offset,10,50,86],'ink_runs':runs,
                            'reference_height':70,'cut_approximate':True})
        snapshot=deepcopy(regions)
        fitted=layout.annotate_line_layout(regions,line)
        record=normalize_glyph({'char':'a','origin':'observed','advance_width':580,
            'recording':[['moveTo',[[40,0]]],['lineTo',[[540,0]]],
                         ['lineTo',[[540,700]]],['lineTo',[[40,700]]],['closePath',[]]]})
        changed=layout.apply_source_layout(record,fitted[0])
        self.assertEqual(regions,snapshot)
        self.assertEqual(record['advance_width'],580)
        self.assertEqual(changed['advance_width'],240)
        self.assertEqual(changed['bbox'][2]-changed['bbox'][0],500)
        self.assertEqual(changed['bbox'][1::2],[0,700])
        self.assertGreater(changed['bbox'][2],changed['advance_width']+200)

    def test_source_seam_allows_negative_left_bearing_without_changing_shape(self):
        record=normalize_glyph({'char':'f','origin':'observed','advance_width':380,
            'recording':[['moveTo',[[40,-200]]],['lineTo',[[340,-200]]],
                         ['lineTo',[[340,700]]],['lineTo',[[40,700]]],['closePath',[]]]})
        region={'box':[10,0,30,90],'reference_height':70,
                'layout_origin_x':20,'layout_advance_px':20}
        result=layout.apply_source_layout(record,region)
        self.assertEqual(result['bbox'],[-100,-200,200,700])
        self.assertEqual(result['advance_width'],200)
        self.assertEqual(result['provenance']['layout']['origin_x'],20)

    def test_interior_seam_anchors_survive_body_regularization(self):
        regions=[{'char':'a','box':[10,30,30,50],
                  'ink_runs':[[row,10,40] for row in range(40,80)],
                  'layout_origin_x':15,'layout_advance_px':22}]
        fitted=layout.annotate_line_layout(regions,{'baseline_y':80,'reference_height':70})
        self.assertEqual(fitted[0]['layout_origin_x'],15)
        self.assertEqual(fitted[0]['layout_advance_px'],22)

    def test_unavailable_layout_preserves_manual_record(self):
        record={'recording':[],'advance_width':300}
        self.assertIs(layout.apply_source_layout(record,{'box':[0,0,5,5]}),record)


if __name__=='__main__':unittest.main()
