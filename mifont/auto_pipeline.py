"""One-click local image-to-font automation with explicit source provenance.

OCR and alphabet-layout evidence suggest labels; they do not prove an unknown
font identity. Observed crops retain image provenance. Missing characters are
clearly marked proposals, using an original CPU grammar or a verified, locally
compared OFL reference. Source artwork is never sent to an online service.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
from statistics import median
import tempfile

from PIL import Image, ImageOps

try:
    from . import engine, completion, ocr, references
except ImportError:
    import engine, completion, ocr, references

ASCII = engine.ASCII_CHARSET
DEFAULT_OPTIONS = {'threshold':'auto', 'foreground':'auto', 'ocr':True, 'offline':False}


def normalize_options(options=None):
    if options is None:options={}
    if not isinstance(options,dict) or set(options)-set(DEFAULT_OPTIONS):
        raise engine.FontError('Unsupported automatic font option.')
    result={**DEFAULT_OPTIONS,**options}
    if result['foreground'] not in {'auto','dark','light'}:
        raise engine.FontError('Use automatic, dark or light foreground.')
    if result['threshold']!='auto':
        result['threshold']=engine._num(result['threshold'],'Ink threshold',1,254,True)
    for name in ('ocr','offline'):
        if not isinstance(result[name],bool):raise engine.FontError(name+' must be true or false.')
    return result


def _report(callback,value,key,message):
    if callback:callback({'progress':max(0,min(100,round(value))),'message_key':key,'message':message})


def _line_geometry(mask):
    """Find substantial text bands, excluding isolated frame/dust islands."""
    width,height=mask.size;data=mask.tobytes()
    rows=[sum(value!=0 for value in data[y*width:(y+1)*width]) for y in range(height)]
    maximum=max(rows,default=0)
    if not maximum:return []
    bands=[]
    for top,bottom in engine._runs([value>=max(1,maximum*.06) for value in rows]):
        if bands and top-bands[-1][1]<=max(3,height*.015):bands[-1][1]=bottom
        else:bands.append([top,bottom])
    lines=[]
    for top,bottom in bands:
        if bottom-top<max(5,height*.008):continue
        top=max(0,top-4);bottom=min(height,bottom+4);line_height=bottom-top
        crop=mask.crop((0,top,width,bottom))
        columns=[crop.crop((x,0,x+1,line_height)).histogram()[255] for x in range(width)]
        runs=engine._runs([value>=max(1,line_height*.025) for value in columns])
        groups=[]
        for left,right in runs:
            if groups and left-groups[-1][-1][1]<=max(15,line_height*.9):groups[-1].append([left,right])
            else:groups.append([[left,right]])
        if not groups:continue
        dominant=max(groups,key=lambda group:sum(columns[x] for left,right in group for x in range(left,right)))
        left=max(0,dominant[0][0]-round(line_height*.1));right=min(width,dominant[-1][1]+round(line_height*.1))
        local=mask.crop((left,top,right,bottom));ink=local.getbbox()
        if not ink:continue
        # Keep actual source extenders; the band is for detection only.
        line_rows=[sum(value!=0 for value in data[y*width+left:y*width+right]) for y in range(top,bottom)]
        heavy=[y for y,value in enumerate(line_rows) if value>=max(line_rows)*.22]
        baseline=top+(heavy[-1]+1 if heavy else ink[3])
        reference=max(1,baseline-(top+ink[1]))
        boxes=[]
        for a,b in dominant:
            bounds=mask.crop((a,top,b,bottom)).getbbox()
            if bounds:boxes.append([a+bounds[0],top+bounds[1],bounds[2]-bounds[0],bounds[3]-bounds[1]])
        lines.append({'id':len(lines),'box':[left,top,right-left,bottom-top],
                      'baseline_y':baseline,'reference_height':reference,'boxes':boxes})
    return lines


def _letters(text):return ''.join(c for c in text if c.isascii() and c.isalpha())


def _words_for_line(words,line):
    x,y,w,h=line['box']
    return [word for word in words if isinstance(word,dict) and isinstance(word.get('text'),str)
            and isinstance(word.get('box'),list) and len(word['box'])==4
            and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in word['box'])
            and y<=word['box'][1]+word['box'][3]/2<=y+h
            and x-w*.1<=word['box'][0]+word['box'][2]/2<=x+w*1.1]


def _sequence_score(actual,expected):
    actual=_letters(actual).upper();expected=expected.upper();best=0
    for start in range(len(actual)):
        for offset in range(len(expected)):
            length=0
            while start+length<len(actual) and offset+length<len(expected) and actual[start+length]==expected[offset+length]:length+=1
            best=max(best,length)
    return best


def _partition(mask,box,text):
    """Jointly place low-ink cuts, with broad character-width constraints."""
    x,y,width,height=box;count=len(text)
    if count<1 or width<count:return []
    crop=mask.crop((x,y,x+width,y+height))
    columns=[crop.crop((p,0,p+1,height)).histogram()[255] for p in range(width)]
    weights=[.48 if c in 'ilI!.,:;|\'' else 1.35 if c in 'MWmw@' else 1 for c in text]
    unit=width/sum(weights)
    # Bound both memory and runtime for long, high-resolution rows.
    step=max(1,width//1000)
    candidates=sorted(set([0,width]+list(range(step,width,step))))
    previous={0:(0,None)};history=[]
    for index,weight in enumerate(weights):
        target=unit*weight;current={};last=index==count-1
        remaining=sum(weights[index+1:])*unit
        expected=sum(weights[:index+1])*unit
        possible=[width] if last else [p for p in candidates if max(index+1,expected-unit*2.5)<=p<=min(width-(count-index-1),expected+unit*2.5)]
        for end in possible:
            best=None
            for start,(cost,_) in previous.items():
                span=end-start
                if not max(1,target*.3)<=span<=target*2.25:continue
                fit=.38*math.log(span/target)**2
                boundary=0 if last else min(columns[max(0,end-1):min(width,end+2)])/max(1,height)
                candidate=(cost+fit+boundary*2,start)
                if best is None or candidate[0]<best[0]:best=candidate
            if best is not None:current[end]=best
        if not current:return engine._guided_boxes(mask,box,text)
        history.append(current);previous=current
    cuts=[width];end=width
    for stage in reversed(history):end=stage[end][1];cuts.append(end)
    cuts.reverse()
    return [[x+cuts[i],y,cuts[i+1]-cuts[i],height] for i in range(count)]


def _mapped_line(mask,line,text,method,evidence):
    boxes=line['boxes'] if len(line['boxes'])==len(text) else _partition(mask,line['box'],text)
    approximate=len(line['boxes'])!=len(text)
    return [{'box':box,'char':char,'line_id':line['id'],'baseline_y':line['baseline_y'],
             'reference_height':line['reference_height'],'label_source':method,
             'label_evidence':evidence,'cut_approximate':approximate,'needs_review':True}
            for box,char in zip(boxes,text)]


def _infer_chart(mask,lines,words,header_text):
    regions=[];used=set();evidence=[]
    # A separated 26-letter sheet needs a real ordered OCR anchor as well as
    # consistent rows; a count of 26 on its own never establishes labels.
    substantial=[line for line in lines if line['box'][2]>=line['box'][3]*1.5]
    counts=[len(line['boxes']) for line in substantial]
    if 1<=len(substantial)<=4 and sum(counts)==26:
        heights=[line['box'][3] for line in substantial]
        if max(heights)<=min(heights)*1.45:
            start=0;score=0
            for line,count in zip(substantial,counts):
                expected='ABCDEFGHIJKLMNOPQRSTUVWXYZ'[start:start+count]
                score=max(score,max((_sequence_score(word['text'],expected) for word in _words_for_line(words,line)),default=0));start+=count
            if score>=3:
                start=0
                for line,count in zip(substantial,counts):
                    expected='ABCDEFGHIJKLMNOPQRSTUVWXYZ'[start:start+count];start+=count
                    regions+=_mapped_line(mask,line,expected,'alphabet_layout',{'ordered_ocr_run':score,'layout_count':26,'row_counts':counts})
                    used.add(line['id'])
                evidence.append('26-letter layout with ordered OCR anchor')
                return regions,used,evidence
    lower_header='LOWERCASE' in re.sub('[^A-Z]','',header_text.upper())
    number_header=bool(re.search(r'NUMBER|PUNCTUATION',header_text,re.I))
    if lower_header and len(substantial)>=3:
        for index in range(1,min(3,len(substantial))):
            second=substantial[index];first=substantial[index-1]
            candidates=_words_for_line(words,second)
            for word in candidates:
                token=_letters(word['text']).upper()
                if len(token)<4:continue
                offset=ord(token[0])-ord('A')
                if not 8<=offset<=20 or _sequence_score(token,'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[offset:])<4:continue
                if not .65<=first['box'][3]/second['box'][3]<=1.5:continue
                for line,text in [(first,'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[:offset]),(second,'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[offset:])]:
                    regions+=_mapped_line(mask,line,text,'alphabet_layout',{'header':'LOWERCASE','ordered_ocr_anchor':token[:12],'split':offset})
                    used.add(line['id'])
                evidence.append('Alphabet header and ordered second-row OCR anchor')
                for line in substantial[index+1:]:
                    tokens=_words_for_line(words,line)
                    if any(_sequence_score(_letters(t['text']),'abcdefghijklmnopqrstuvwxyz')>=4 and _letters(t['text'])[:1].islower() for t in tokens):
                        regions+=_mapped_line(mask,line,'abcdefghijklmnopqrstuvwxyz','alphabet_layout',{'header':'LOWERCASE','ordered_ocr_run':4,'cut':'connected-script proposal'})
                        used.add(line['id']);break
                break
            if regions:break
    if number_header and regions:
        for line in lines:
            if line['id'] in used:continue
            for word in _words_for_line(words,line):
                digits=''.join(c for c in word['text'] if c in '0123456789')
                text=next((candidate for candidate in ('1234567890','0123456789') if len(digits)>=7 and digits in candidate),None)
                if text:
                    regions+=_mapped_line(mask,line,text,'number_layout',{'header':'NUMBER','ordered_ocr_digits':digits})
                    used.add(line['id']);evidence.append('Number header and ordered digit OCR anchor');break
    return regions,used,evidence


def _word_regions(mask,lines,words,used):
    result=[]
    for line in lines:
        if line['id'] in used:continue
        for word in _words_for_line(words,line):
            text=word['text']
            if not re.fullmatch(r'[A-Za-z0-9]{1,32}',text):continue
            x,y,w,h=word['box'];x=max(0,round(x));y=max(0,round(y));w=min(mask.width-x,round(w));h=min(mask.height-y,round(h))
            if w<1 or h<1:continue
            if mask.crop((x,y,x+w,y+h)).histogram()[255]/(w*h)<.10:continue
            # OCR is not allowed to turn a distant heading into selected ink.
            box=[max(0,x-2),max(0,y-2),min(mask.width-max(0,x-2),w+4),min(mask.height-max(0,y-2),h+4)]
            boxes=_partition(mask,box,text)
            for crop,char in zip(boxes,text):
                result.append({'box':crop,'char':char,'line_id':line['id'],'baseline_y':line['baseline_y'],
                               'reference_height':line['reference_height'],'label_source':'local_ocr','label_evidence':{'recognized_word':text},
                               'cut_approximate':len(text)>1,'needs_review':True})
    return result


def _stable_prefix(mask,line,folder):
    """Recover only a stable readable prefix when a decorative word is noisy."""
    x,y,w,h=line['box']
    if w<h*2 or h<20:return []
    guesses=[]
    for index,fraction in enumerate((.65,.75,1)):
        crop=ImageOps.invert(mask.crop((x,y,x+w,y+max(1,round(h*fraction))))).convert('RGB')
        crop=ImageOps.expand(crop,25,'white');crop.thumbnail((1400,700));crop=crop.resize((crop.width*2,crop.height*2))
        path=folder/('ocr-prefix-'+str(index)+'.png');crop.save(path)
        recognized=ocr.suggest_labels(path)
        token=re.sub(r'\s+','',recognized.get('text',''))
        match=re.match('[A-Za-z]+',token)
        if match:guesses.append(match.group(0))
    if len(guesses)<2:return []
    prefix=''
    for index in range(min(8,max(map(len,guesses)))):
        votes=Counter(word[index] for word in guesses if len(word)>index)
        char,count=votes.most_common(1)[0]
        if count<2 or count<len(guesses)*.66:break
        prefix+=char
    if len(prefix)<2:return []
    # A lower swash must not define the x-height/baseline of the prefix.
    content=mask.crop((x,y,x+w,y+round(h*.75)));bounds=content.getbbox()
    if not bounds:return []
    left=x+bounds[0];top=y+bounds[1];bottom=y+bounds[3];glyph_height=bottom-top
    columns=[content.crop((p,0,p+1,content.height)).histogram()[255] for p in range(w)]
    start=left;result=[]
    for char in prefix:
        target=glyph_height*(.65 if char.isupper() else .5)
        expected=start+target;radius=max(3,round(glyph_height*.19))
        low=max(start+3,round(expected)-radius);high=min(x+w-1,round(expected)+radius)
        if low>=high:break
        end=min(range(low,high+1),key=lambda p:columns[p-x]+abs(p-expected)*.15)
        result.append({'box':[start,top,end-start,glyph_height],'char':char,'line_id':line['id'],
                       'baseline_y':bottom,'reference_height':glyph_height,'label_source':'stable_ocr_prefix',
                       'label_evidence':{'ocr_prefix':prefix,'agreeing_views':len(guesses)},'cut_approximate':True,'needs_review':True})
        start=end
    return result


def _extract(mask,region,image_id,folder):
    x,y,w,h=region['box'];crop=mask.crop((x,y,x+w,y+h))
    if not crop.getbbox():return None
    path=folder/'glyph-mask.png';ImageOps.invert(crop).save(path)
    record=engine.extract_glyph(path,[0,0,w,h],{'foreground':'dark','threshold':128,'char':region['char'],
                               'baseline_y':max(0,region['baseline_y']-y),'reference_height':region['reference_height'],
                               'speckle':max(0,min(6,round(h*h*.0002)))})
    record.update(source_box=list(region['box']),baseline_y=region['baseline_y'],image_id=image_id,
                  provenance={'method':'automatic-image-trace','image_id':image_id,'source_box':list(region['box']),
                              'label_source':region['label_source'],'label_evidence':deepcopy(region['label_evidence']),
                              'cut_approximate':region['cut_approximate'],'needs_review':True})
    return record


def generate(images,metadata,options,output_dir,progress=None,*,reference_cache_dir=None):
    """Automatically trace mapped source ink, complete ASCII95, build two fonts.

    images: [{id, path}], metadata: {family, style?}. Only trusted server code
    supplies reference_cache_dir; no source image or glyph leaves the machine.
    """
    options=normalize_options(options)
    if not isinstance(images,list) or not 1<=len(images)<=12:raise engine.FontError('Add between 1 and 12 reference images.')
    if not isinstance(metadata,dict):raise engine.FontError('Choose a font family name.')
    font_metadata={'family':metadata.get('family',metadata.get('family_name','MiFont Custom')),
                   'style':metadata.get('style',metadata.get('style_name','Regular'))}
    engine._ensure_cli_job()
    folder=Path(output_dir);folder.mkdir(parents=True,exist_ok=True)
    chosen={};analyses=[];warnings=[];wrappers={};evidence=[]
    with tempfile.TemporaryDirectory(prefix='mifont-auto-',dir=folder) as temporary:
        work=Path(temporary)
        for index,item in enumerate(images):
            if not isinstance(item,dict) or not isinstance(item.get('id',item.get('image_id')),str) or not item.get('path'):
                raise engine.FontError('A reference image is missing its local identity or file.')
            image_id=item.get('id',item.get('image_id'));image,info=engine._read_image(item['path'])
            try:
                _report(progress,3+index*45/len(images),'generate_analyze','Finding foreground letters and excluding frame noise')
                try:
                    mask,mask_info=engine._foreground(image,engine.normalize_options({key:options[key] for key in ('foreground','threshold')}))
                except engine.FontError:
                    warnings.append('One reference has no contrasting letters and was skipped.');continue
                mask=engine._analysis_mask(mask,engine.normalize_options())
                lines=_line_geometry(mask)
                if not lines:
                    warnings.append('One reference has no readable foreground letters and was skipped.');continue
                recognized={'available':False,'text':'','words':[]}
                if options['ocr']:
                    _report(progress,5+index*45/len(images),'generate_ocr','Reading letter labels locally')
                    recognized=ocr.suggest_labels(item['path'])
                regions,used,proof=_infer_chart(mask,lines,recognized.get('words',[]),recognized.get('text',''))
                regions+=_word_regions(mask,lines,recognized.get('words',[]),used)
                if not regions and options['ocr']:
                    clean=work/'selected-ink.png';ImageOps.invert(mask).save(clean)
                    alternate=ocr.suggest_labels(clean)
                    combined=recognized.get('words',[])+alternate.get('words',[])
                    regions,used,proof=_infer_chart(mask,lines,combined,recognized.get('text','')+' '+alternate.get('text',''))
                    regions+=_word_regions(mask,lines,alternate.get('words',[]),used)
                    if not regions:
                        regions=_stable_prefix(mask,max(lines,key=lambda line:line['box'][2]*line['box'][3]),work)
                for region in regions:
                    region['id']=hashlib.sha256((image_id+repr(region['box'])+region['char']).encode()).hexdigest()[:24]
                evidence+=proof
                analyses.append({'image_id':image_id,**info,'regions':deepcopy(regions),'lines':[{k:v for k,v in line.items() if k!='boxes'} for line in lines],
                                 'mask':mask_info,'region_count':len(regions),'automation_evidence':proof,
                                 'warnings':['Labels are inferred from local OCR or evidenced alphabet layouts; connected cuts are approximate.']})
                for number,region in enumerate(regions):
                    if region['char'] not in ASCII or region['char']==' ':continue
                    if region['char'] in chosen:continue
                    _report(progress,10+(index+(number+1)/max(1,len(regions)))*45/len(images),'generate_trace','Tracing observed character '+region['char'])
                    try:record=_extract(mask,region,image_id,work)
                    except (engine.FontError,ValueError) as exc:
                        warnings.append('A proposed character crop could not be traced and was left for completion.');continue
                    if record:
                        chosen[region['char']]=record;wrappers[region['char']]={'record':record,'image_id':image_id,'box':list(region['box'])}
            finally:image.close()
        if not chosen:
            raise engine.FontError('MiFont could not identify readable letters in these images. Try a sharper, larger image with clear text and less overlap, then Generate again.')
        completed=None;selected=None;research=None;obligations=[]
        if not options['offline'] and len(chosen)>=3:
            _report(progress,58,'generate_research','Comparing verified open-font references locally')
            try:
                research=references.research_sources(list(chosen.values()),'',{'candidate_limit':6},cache_dir=reference_cache_dir)
                candidates=[card for card in research['references'] if isinstance(card.get('match_score'),(float,int)) and card['match_score']>=.45 and len(card.get('matched_chars',''))>=3]
                if candidates:
                    selected=candidates[0]
                    reference=references.resolve_reference(selected['id'],research['references'],cache_dir=reference_cache_dir)
                    completed=completion.complete_glyphs(list(chosen.values()),{'mode':'reference','allow_approximate':True,'accept_reference_license':True,
                                                        'reference_id':selected['id'],'target_chars':ASCII},reference=reference)
                    obligation=completed.get('provenance',{}).get('license_obligations')
                    if obligation:obligations=[obligation]
                    font_metadata.update(license_obligations=obligations,requires_renamed_family=True)
                    try:from .font_export import reference_name_conflict
                    except ImportError:from font_export import reference_name_conflict
                    if reference_name_conflict(font_metadata['family'],obligations):
                        font_metadata['family']='MiFont Custom'
                        warnings.append('The requested name belongs to the selected source font; the derivative family was renamed MiFont Custom.')
            except (references.ReferenceError,completion.CompletionError,OSError,ValueError):
                warnings.append('Online reference comparison was unavailable; local CPU completion was used.')
                completed=None;selected=None;obligations=[]
                font_metadata={k:v for k,v in font_metadata.items() if k not in {'license_obligations','requires_renamed_family'}}
        _report(progress,78,'generate_complete','Completing the printable Latin character set')
        if completed is None:completed=completion.complete_glyphs(list(chosen.values()),{'mode':'proposal','allow_approximate':True,'target_chars':ASCII})
        elif completed['missing_chars']:
            completed=completion.complete_glyphs(completed['glyphs'],{'mode':'proposal','allow_approximate':True,'target_chars':ASCII})
        warnings+=completed.get('warnings',[])
        if any(r.get('cut_approximate') for a in analyses for r in a['regions']):
            warnings.append('Connected characters were split automatically. Their cut boundaries are approximate, not recovered original font outlines.')
        _report(progress,90,'generate_build','Building real TrueType and OpenType fonts')
        metrics=engine.build_font(completed['glyphs'],font_metadata,folder/'font.ttf',folder/'font.otf')
        glyphs=[wrappers.get(record['char'],{'record':record,'image_id':None,'box':None}) for record in completed['glyphs']]
        coverage={key:metrics[key] for key in ('observed_chars','generated_chars','missing_chars')};coverage['unassigned_count']=0
        warnings=list(dict.fromkeys(warnings))
        _report(progress,99,'generate_build','Font files are ready')
        return {'glyphs':glyphs,'images':analyses,'coverage':coverage,'metrics':metrics,'files':{'ttf':'font.ttf','otf':'font.otf'},
                'font_metadata':font_metadata,'license_obligations':obligations,'warnings':warnings,
                'references':research.get('references',[]) if research else [],
                'automation':{'one_click':True,'observed_count':len(chosen),'generated_count':len(coverage['generated_chars']),
                              'layout_evidence':evidence,'reference_selected':selected,'artwork_uploaded':False,
                              'neural':False,'missing_glyphs_approximate':True,'manual_character_mapping_required':False}}
