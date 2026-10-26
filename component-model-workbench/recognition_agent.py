"""Bounded visual re-reading. Agreement checks literal pixels, not API popularity."""
import base64, io, re, time
from concurrent.futures import ThreadPoolExecutor
import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError
from pv_component_lookup.contracts import ProviderError
from recognition import PROMPT, candidate_kind, compact_key, text_candidates

VERSION='visual-crosscheck-v2'
LOCAL_PROMPT=PROMPT+'''\n你现在只看一个放大的局部，独立逐字读取；不要假设存在特定型号或功率档。
不要利用型号等差数列补齐字符。抄录所有可见完整型号，没有完整前缀或后缀则不作为完整型号；可见文字仍照抄。
局部裁断的字词不要补全，缺字记为[裁断]。'''

def note_list(value):
    if isinstance(value,str):return [value] if value else []
    return [str(v) for v in value if isinstance(v,(str,int,float))] if isinstance(value,list) else []

def normalized_models(answer,index):
    text=answer.get('text','');text=text if isinstance(text,str) else ''
    items=[]
    for item in answer.get('models',[]) if isinstance(answer.get('models'),list) else []:
        if not isinstance(item,dict) or item.get('device_type')!='component':continue
        raw=item.get('raw_model');quote=item.get('quote')
        if not isinstance(raw,str) or not isinstance(quote,str) or not raw.strip() or not quote.strip():continue
        literal=compact_key(raw) in compact_key(text) and compact_key(quote) in compact_key(text)
        lines=item.get('visible_lines');box=item.get('bbox')
        cell_literal=(isinstance(lines,list) and 1<=len(lines)<=4 and all(isinstance(s,str) and s.strip() and compact_key(s) in compact_key(text) for s in lines)
                      and compact_key(''.join(lines))==compact_key(raw)
                      and isinstance(box,list) and len(box)==4 and all(isinstance(v,(int,float)) for v in box)
                      and 0<=box[0]<box[2]<=1000 and 0<=box[1]<box[3]<=1000)
        if not literal and not cell_literal:continue
        # Explicit exclusions apply even if a VLM incorrectly marks them as components.
        if re.fullmatch(r'\d{4}-\d{6}-\d{2}-\d{2}-\d{6}',raw):continue
        if re.fullmatch(r'\d+(?:\.\d+)?\s*(?:[kKmM]?[wW][pP]?|[kK]?[vV]|[kK]?[wW][hH])',raw):continue
        if re.fullmatch(r'[0-9A-Z]{18}',raw) and not index.families(raw):continue
        items.append({**item,'method':'视觉单元格字符定位' if cell_literal and not literal else '视觉原文定位'})
    known={compact_key(i['raw_model']) for i in items}
    for item in text_candidates(text,index):
        if compact_key(item['raw_model']) not in known:items.append(item);known.add(compact_key(item['raw_model']))
    return text,items

def read_image(im,reader,prompt,trace,view_id,box=None):
    frame=im.crop(box) if box else im.copy()
    if box and max(frame.size)<1600:
        frame=frame.resize((frame.width*2,frame.height*2),Image.Resampling.LANCZOS)
    frame.thumbnail((3200,3200),Image.Resampling.LANCZOS)
    buff=io.BytesIO();frame.convert('RGB').save(buff,format='PNG')
    begin=time.monotonic()
    answer=reader._request([{'role':'system','content':prompt},{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buff.getvalue()).decode()}}]}])
    if not isinstance(answer,dict):raise ProviderError('OCR返回格式无效')
    trace.append({'view_id':view_id,'bbox_px':list(box or (0,0,*im.size)),'sent_size_px':list(frame.size),'state':'done','elapsed_s':round(time.monotonic()-begin,2)})
    return answer

def crop_boxes(im,answer):
    w,h=im.size;boxes=[]
    for region in answer.get('model_regions',[]) if isinstance(answer.get('model_regions'),list) else []:
        if not isinstance(region,dict):continue
        b=region.get('bbox')
        if not isinstance(b,list) or len(b)!=4 or any(not isinstance(v,(int,float)) for v in b):continue
        x0,y0,x1,y1=b
        if not 0<=x0<x1<=1000 or not 0<=y0<y1<=1000:continue
        # An uploaded label crop can itself be the complete model cell. Splitting
        # it into bands would separate its two wrapped lines and lose the suffix.
        if (x1-x0)*(y1-y0)>850000 and (w>=1000 or h>=300):continue
        if w<1000 and h<300:
            if (0,0,w,h) not in boxes:boxes.append((0,0,w,h))
            continue
        # Preserve neighboring context; clamp every crop inside the real image.
        px=max(24,int(w*.04));py=max(24,int(h*.03))
        b=(max(0,int(x0*w/1000)-px),max(0,int(y0*h/1000)-py),min(w,int(x1*w/1000)+px),min(h,int(y1*h/1000)+py))
        if b[2]-b[0]>=10 and b[3]-b[1]>=10 and b not in boxes:boxes.append(b)
    return boxes[:3]

def bands(im):
    w,h=im.size
    if h>=w:
        return [(0,0,w,max(1,int(h*.40))),(0,int(h*.30),w,max(1,int(h*.73))),(0,int(h*.63),w,h)]
    return [(0,0,w,max(1,int(h*.58))),(0,int(h*.42),w,h)]

def page_requires_visual(page,text):
    if len(re.sub(r'\s+','',text))<80:return True
    area=page.rect.width*page.rect.height
    images=[b for b in page.get_text('dict')['blocks'] if b.get('type')==1]
    return any((b['bbox'][2]-b['bbox'][0])*(b['bbox'][3]-b['bbox'][1])>.25*area for b in images)

def read_page(im,index,reader,number):
    trace=[];overview=read_image(im,reader,PROMPT,trace,'overview');rotation=overview.get('rotation_clockwise',0)
    if isinstance(rotation,int) and rotation in (90,180,270):
        im=im.rotate(-rotation,expand=True)
        overview=read_image(im,reader,PROMPT,trace,'orientation_corrected')
    overview_text,initial=normalized_models(overview,index)
    notes=note_list(overview.get('notes'));views=[('overview',overview_text,initial)]
    filing=bool(re.search(r'备案|项目信息公告',overview_text))
    suspected=bool(initial) or bool(re.search(r'Module\s*Type|\bTYPE\b|组件型号|型号\s*[:：]|JAM\d|LR\d|TSM-',overview_text,re.I))
    boxes=bands(im) if filing else crop_boxes(im,overview)
    if len(initial)>1:
        w,h=im.size
        boxes=[(0,max(0,b[1]-int(h*.025)),w,min(h,b[3]+int(h*.025))) for b in boxes]
    if not boxes and suspected:boxes=bands(im)
    if boxes and reader:
        # All requests are independent: no guessed/model-list answers are sent back.
        def local(item):
            i,box=item;vid='region_'+str(i+1)
            try:
                response=read_image(im,reader,LOCAL_PROMPT,trace,vid,box)
                text,models=normalized_models(response,index)
                return {'view_id':vid,'bbox_px':list(box),'text':text,'models':models,'notes':note_list(response.get('notes'))}
            except ProviderError:
                trace.append({'view_id':vid,'bbox_px':list(box),'state':'failed'})
                return {'view_id':vid,'bbox_px':list(box),'text':'','models':[],'notes':['局部读取失败，未将该区域视为已核验']}
        with ThreadPoolExecutor(max_workers=2) as pool:regions=list(pool.map(local,enumerate(boxes)))
        for r in regions:
            views.append((r['view_id'],r['text'],r['models']));notes.extend(r['notes'])
    else:regions=[]
    counts={};records={};observations={}
    def add(view,text,models):
        for m in models:
            k=compact_key(m['raw_model']);records.setdefault(k,{**m,'page':number})
            counts.setdefault(k,set()).add(view)
            observations.setdefault(k,[]).append({'view_id':view,'quote':m['quote'],'visible_lines':m.get('visible_lines'),'bbox_normalized':m.get('bbox')})
    for view,text,models in views:add(view,text,models)
    # New model tokens found only in a crop get a second independent crop read.
    # At most two retries; those still unresolved return a machine-readable gap.
    pending=[k for k in counts if len(counts[k])<2 and candidate_kind(records[k],index)[0] in ('complete','unverified')]
    retry_boxes=[]
    for k in pending:
        source=next((r for r in regions if r['view_id'] in counts[k]),None)
        if source:
            x0,y0,x1,y1=source['bbox_px'];w,h=im.size
            box=(max(0,x0-15),max(0,y0-25),min(w,x1+15),min(h,y1+25))
        else:continue  # Repeating the same whole image is not a local confirmation.
        if box not in retry_boxes:retry_boxes.append(box)
    for i,box in enumerate(retry_boxes[:2]):
        vid='reread_'+str(i+1)
        try:
            answer=read_image(im,reader,LOCAL_PROMPT,trace,vid,box);text,models=normalized_models(answer,index)
            add(vid,text,models);notes.extend(note_list(answer.get('notes')))
            regions.append({'view_id':vid,'bbox_px':list(box),'text':text,'models':models})
        except ProviderError:
            trace.append({'view_id':vid,'bbox_px':list(box),'state':'failed'});notes.append('自动重读失败，保留证据缺口')
    for k,m in records.items():
        m.update(verification={'status':'visual_agreement' if len(counts[k])>=2 else 'unresolved','view_count':len(counts[k]),'observations':observations[k]},auto_query_eligible=len(counts[k])>=2,requires_manual_review=False)
    # Region transcript is primary for dense filing pages; overview is retained
    # separately so disagreements are inspectable rather than silently replaced.
    regional_text='\n\n'.join(r['text'] for r in regions if r['view_id'].startswith('region_') and r['text'])
    text=regional_text if filing and regional_text else overview_text
    page={'page':number,'text':text,'overview_text':overview_text,'regions':[{k:v for k,v in r.items() if k!='models'} for r in regions],'read_method':'visual_overview_and_regions','rotation_clockwise':rotation if isinstance(rotation,int) else 0,'trace':trace,'transcription_status':'region_read_failed' if any(t['state']=='failed' for t in trace) else 'transcribed_not_character_certified'}
    return page,list(records.values()),notes

def identify_verified(content,filename,index,reader):
    pages=[]
    if content.lstrip().startswith(b'%PDF-'):
        try:
            with pymupdf.open(stream=content,filetype='pdf') as d:
                if d.is_encrypted:raise ProviderError('PDF已加密，无法识别')
                if len(d)>6:raise ProviderError('识别入口最多6页，请先选取相关页')
                for i,p in enumerate(d):
                    text=p.get_text(sort=False)
                    visual=page_requires_visual(p,text)
                    im=Image.open(io.BytesIO(p.get_pixmap(dpi=200).tobytes('png'))).convert('RGB') if visual else None
                    pages.append((i+1,text,im))
        except (RuntimeError,ValueError):raise ProviderError('PDF无法读取') from None
    else:
        try:
            with Image.open(io.BytesIO(content)) as im:
                if im.format not in ('JPEG','PNG','WEBP'):raise ProviderError('仅支持JPEG、PNG、WebP和PDF')
                if im.width*im.height>25_000_000:raise ProviderError('图片超过2500万像素限制')
                pages=[(1,'',ImageOps.exif_transpose(im).convert('RGB'))]
        except (UnidentifiedImageError,Image.DecompressionBombError):raise ProviderError('图片格式或大小不支持') from None
    texts=[];records=[];notes=[]
    for number,text,im in pages:
        if im is not None:
            if not reader:raise ProviderError('图片或扫描PDF需要配置大模型OCR')
            page,models,ns=read_page(im,index,reader,number);notes.extend(ns)
        else:
            models=text_candidates(text,index)
            for m in models:m.update(page=number,auto_query_eligible=True,verification={'status':'pdf_literal','view_count':1,'observations':[{'view_id':'pdf_text','quote':m['quote']}]})
            page={'page':number,'text':text,'read_method':'pdf_text_layer','trace':[],'transcription_status':'text_layer_extracted'}
        texts.append(page);records.extend(models)
    unique={}
    for m in records:
        k=compact_key(m['raw_model'])
        if k not in unique or (m.get('auto_query_eligible') and not unique[k].get('auto_query_eligible')):unique[k]=m
    candidates=[];series=[];uncertain=[];unresolved=[]
    for m in unique.values():
        kind,reason,lookup=candidate_kind(m,index)
        m.update(identity_status=kind,identity_note=reason,lookup_model=lookup)
        if kind=='series':m['auto_query_eligible']=False;series.append(m)
        elif kind=='uncertain':m['auto_query_eligible']=False;uncertain.append(m)
        elif m.get('auto_query_eligible'):candidates.append(m)
        else:unresolved.append(m)
    # A corrected local reading doesn't erase the original suspicious reading;
    # keep it as audit evidence while accepting only independently repeated tokens.
    read_failed=any(t.get('state')=='failed' for p in texts for t in p.get('trace',[]))
    state='partial_models_verified' if candidates and (unresolved or uncertain or read_failed) else 'models_verified' if candidates else 'insufficient_evidence' if unresolved or uncertain or read_failed else 'series_only' if series else 'no_model_disclosed' if any(len(p['text'].strip())>=40 for p in texts) else 'unreadable_or_no_model_detected'
    notes=list(dict.fromkeys(notes))
    if candidates:notes.append('完整型号已按原文及局部重读自动交叉校验；型号存在性由后续完整型号检索核验。')
    if unresolved or uncertain:notes.append('未通过自动交叉校验的字串单列为证据缺口，不参与参数查询；不要求用户猜型号。')
    if series:notes.append('系列／功率范围不是单一完整型号，未根据功率列表补造型号。')
    return {'filename':filename,'recognition_version':VERSION,'recognition_status':state,'pages':texts,'candidates':candidates,'series_candidates':series,'uncertain_candidates':uncertain,'unresolved_candidates':unresolved,'notes':notes or ['材料未披露可自动核验的完整组件型号。'],'automatic_verification':{'method':'same_model_independent_image_views_and_literal_format_checks','human_model_confirmation_required':False,'limitations':'同一视觉模型的不同视图可能有相关错误；不以模型自报置信度或API收录情况替代原图证据。'}}
