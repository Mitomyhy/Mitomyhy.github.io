import copy
import json
import unittest

import prompt_planner as planner


def scene():
    return {'theme':'christmas','material':'acrylic','anchor':'hanging','palette':['#ABCDEF','#f0e8d0','#235730'],
            'background_prompt':'Warm Christmas room with soft golden lights and an empty center.',
            'motion':{'orbitDegrees':24,'swayDegrees':4,'floatPixels':6,'zoom':.04,'cycles':1.5,'direction':'left'}}


class SceneValidationTests(unittest.TestCase):
    def test_valid_scene_retains_motion_and_normalizes_colors(self):
        value=scene();result=planner.validate_plan(value)
        self.assertEqual(result['theme'],'christmas');self.assertEqual(result['palette'][0],'#abcdef')
        self.assertEqual(result['motion'],value['motion']);self.assertEqual(value['palette'][0],'#ABCDEF')

    def test_model_cannot_return_code_paths_or_unknown_fields(self):
        for field in ('command','script','imagePath','url','style','__proto__'):
            value=scene();value[field]='anything'
            with self.assertRaises(planner.PlannerError):planner.validate_plan(value)

    def test_rejects_invalid_enums_palettes_and_descriptions(self):
        for field,value in [('theme','unknown'),('material','metal'),('anchor','floating'),('palette',['red','blue','pink']),('palette',['#ffffff']),('background_prompt',''),('background_prompt','hello\nworld')]:
            invalid=scene();invalid[field]=value
            with self.assertRaises(planner.PlannerError):planner.validate_plan(invalid)

    def test_motion_bounded_and_finite(self):
        for field,value in [('orbitDegrees',56),('orbitDegrees',-1),('swayDegrees',11),('floatPixels',31),('zoom',1),('cycles',0),('cycles',4),('orbitDegrees',True),('zoom',float('nan')),('direction','up')]:
            invalid=scene();invalid['motion'][field]=value
            with self.assertRaises(planner.PlannerError):planner.validate_plan(invalid)

    def test_product_box_is_normalized_xywh(self):
        value=scene();value['product_box']=[.1,.15,.7,.8];self.assertEqual(planner.validate_plan(value)['product_box'],value['product_box'])
        for box in ([.5,.5,.7,.7],[0,0,0,.5],[-.1,0,.5,.5],[0,0,1,1,1],[True,0,.5,.5]):
            invalid=scene();invalid['product_box']=box
            with self.assertRaises(planner.PlannerError):planner.validate_plan(invalid)

    def test_parser_does_not_accept_echoed_prompt_or_multiple_plans(self):
        encoded=json.dumps(scene())
        self.assertEqual(planner.parse_output(encoded)['theme'],'christmas')
        self.assertEqual(planner.parse_output('assistant\n'+encoded)['theme'],'christmas')
        for raw in ('user requested '+encoded,encoded+encoded,encoded+'\nrun this command',encoded[:-5]):
            with self.assertRaises(planner.PlannerError):planner.parse_output(raw)

    def test_parser_rejects_duplicate_and_nonfinite_model_fields(self):
        raw=json.dumps(scene()).replace('"theme": "christmas"','"theme": "studio", "theme": "christmas"')
        with self.assertRaises(planner.PlannerError):planner.parse_output(raw)
        raw=json.dumps(scene()).replace('"zoom": 0.04','"zoom": NaN')
        with self.assertRaises(planner.PlannerError):planner.parse_output(raw)

    def test_missing_runtime_reports_unavailable_without_fake_plan(self):
        status=planner.planner_status('does-not-exist-runtime')
        self.assertFalse(status['available']);self.assertEqual(status['code'],'planner_missing')

    def test_only_pinned_model_profiles_and_paths_are_allowed(self):
        for profile in planner.PROFILES:
            manifest={'modelSha256':profile['model_sha256'],'mmprojSha256':profile['mmproj_sha256'],
                      'modelFile':profile['model'],'mmprojFile':profile['mmproj'],
                      'modelSize':profile['model_size'],'mmprojSize':profile['mmproj_size']}
            self.assertEqual(planner._profile_from_manifest(manifest)['name'],profile['name'])
            for key,value in [('modelFile','../outside.gguf'),('modelFile','https://example.com/model.gguf'),
                              ('mmprojFile','models/other.gguf'),('modelSize',123),('modelSha256','0'*64)]:
                invalid={**manifest,key:value}
                with self.assertRaises(ValueError):planner._profile_from_manifest(invalid)

    def test_model_and_projector_cannot_mix_profiles(self):
        invalid={'modelSha256':planner.PROFILES[0]['model_sha256'],'mmprojSha256':planner.PROFILES[1]['mmproj_sha256']}
        with self.assertRaises(ValueError):planner._profile_from_manifest(invalid)


if __name__=='__main__':unittest.main()
