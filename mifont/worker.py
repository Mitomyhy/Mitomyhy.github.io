"""Cancellable owned-process operations; only the parent commits project edits."""
from pathlib import Path
import json
import os

def run_operation(kind,payload,directory,messages):
    try:
        try:
            from . import engine,completion
        except ImportError:
            import engine,completion
        folder=Path(directory)
        def progress(event):
            messages.put({'kind':'progress',**event})
        def report(value,message):progress({'progress':value,'message':message})
        if kind=='analyze':
            images=[]
            for index,image in enumerate(payload['images']):
                report(5+int(85*index/len(payload['images'])),'Finding glyph regions')
                options=dict(payload.get('options',{}))
                use_ocr=options.pop('ocr',True)
                if not isinstance(use_ocr,bool):raise ValueError('OCR must be enabled or disabled.')
                ocr_result=None
                if use_ocr and not options.get('text','').strip():
                    try:
                        try:from . import ocr
                        except ImportError:import ocr
                        ocr_result=ocr.suggest_labels(image['path'])
                    except Exception:
                        ocr_result={'available':False,'engine':'Windows OCR','warnings':['OCR is unavailable; map the detected regions manually.']}
                    if ocr_result.get('available'):options['ocr_words']=ocr_result.get('words',[])
                result=engine.analyze_image(image['path'],options)
                if ocr_result:
                    result['ocr']=ocr_result
                    result.setdefault('warnings',[]).extend(ocr_result.get('warnings',[]))
                images.append({'image_id':image['id'],**result})
            result={'images':images}
        elif kind=='extract':
            glyphs=[];regions=payload['regions']
            for index,region in enumerate(regions):
                report(5+int(85*index/len(regions)),'Tracing glyph outlines')
                options={key:value for key,value in payload.get('options',{}).items() if key!='ocr'}
                for key in ('char','baseline_y','reference_height'):
                    if key in region:options[key]=region[key]
                record=engine.extract_glyph(payload['image']['path'],region['box'],options)
                glyphs.append({'record':record,'image_id':payload['image']['id'],'box':region['box'],
                               'candidate_id':region.get('id')})
            result={'glyphs':glyphs}
        elif kind=='complete':
            reference=None
            if payload.get('options',{}).get('mode')=='reference':
                try:from . import references
                except ImportError:import references
                reference=references.resolve_reference(payload['options']['reference_id'],payload['references'],
                                                       cache_dir=Path(payload['cache_dir']))
            result=completion.complete_glyphs(payload['records'],payload.get('options'),progress,reference=reference)
        elif kind=='research':
            try:
                from . import references
            except ImportError:import references
            result=references.research_sources(payload['records'],payload.get('query',''),payload.get('options'),progress,
                                               cache_dir=Path(payload['cache_dir']))
        elif kind=='build':
            report(10,'Building TrueType and OpenType outlines')
            result=engine.build_font(payload['records'],payload['metadata'],folder/'font.ttf',folder/'font.otf')
            result={'metrics':result}
        else:raise ValueError('Unknown MiFont operation.')
        report(98,'Saving local results')
        temporary=folder/'result.tmp'
        temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        os.replace(temporary,folder/'result.json')
        messages.put({'kind':'done'})
    except Exception as exc:
        messages.put({'kind':'error','message':str(exc)[:1200]})
