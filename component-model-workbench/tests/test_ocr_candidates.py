import io,json
from pathlib import Path
from PIL import Image
import pytest
from recognition import identify
from pv_component_lookup.naming import ComponentModelIndex

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def index():return ComponentModelIndex(json.loads((ROOT/'data/naming_rules.json').read_text('utf-8')))

def recognize(index,text,models):
    class Reader:
        def _request(self,messages):return {'text':text,'models':models,'notes':[]}
    buff=io.BytesIO();Image.new('RGB',(20,20),'white').save(buff,format='PNG')
    return identify(buff.getvalue(),'fixture.png',index,Reader())

def model(raw):return {'raw_model':raw,'quote':raw,'manufacturer':'JA Solar','device_type':'component','confidence':.99}

def test_multi_column_text_recovers_models_omitted_by_model_list(index):
    first='LR5-54HTB-440M';text='Module Type '+first+' LR5-54HTB-445M LR5-54HTB-450M'
    result=recognize(index,text,[model(first)])
    assert {r['raw_model'] for r in result['candidates']}=={first,'LR5-54HTB-445M','LR5-54HTB-450M'}

@pytest.mark.parametrize('raw',['JAM66D42-S80/MB','JAM66D42-6O5/MB','JAM66D42-I00/MB'])
def test_high_confidence_letter_in_power_slot_remains_uncertain(index,raw):
    result=recognize(index,raw,[model(raw)])
    assert result['candidates']==[]
    assert result['uncertain_candidates'][0]['raw_model']==raw
    assert result['uncertain_candidates'][0]['lookup_model']==raw

@pytest.mark.parametrize('raw',['TWMNH-66HDXXX','JKM440-480N-48HL4M-DB-Z2C3-OC','JAM54D40 LB'])
def test_placeholder_range_and_series_are_not_selectable_full_models(index,raw):
    result=recognize(index,raw,[model(raw)])
    assert result['candidates']==[] and result['series_candidates'][0]['raw_model']==raw

def test_wrapped_model_keeps_raw_text_and_only_merges_layout_spaces(index):
    raw='TSM-455 NEG9R.28';result=recognize(index,raw,[model(raw)])
    candidate=result['candidates'][0]
    assert candidate['raw_model']==raw
    assert candidate['lookup_model']=='TSM-455NEG9R.28'
    assert candidate['identity_status']=='unverified'  # API snapshot uses a different spelling.

def test_new_unregistered_model_format_can_be_visually_crosschecked(index):
    raw='NEWBRAND-625-AB';result=recognize(index,raw,[model(raw)])
    assert result['candidates'][0]['identity_status']=='unverified'
    assert result['candidates'][0]['lookup_model']==raw

def test_power_without_literal_model_does_not_expand_family(index):
    result=recognize(index,'JAM66D42 MB power 580 585 590 W',[model('JAM66D42 MB')])
    assert result['candidates']==[] and result['series_candidates']

def test_new_trailing_variant_is_not_treated_as_a_corrupted_digit(index):
    raw='CS6.2-66HB-635H';result=recognize(index,raw,[model(raw)])
    assert result['candidates'][0]['raw_model']==raw
    assert result['candidates'][0]['identity_status']=='unverified'
    assert result['uncertain_candidates']==[]

def test_layout_separator_spaces_recover_all_literal_columns(index):
    from recognition import text_candidates
    text='TYPE JAM66D42 -580/MB JAM66D42\n-585 /MB JAM66D42 -590/MB'
    assert {m['raw_model'] for m in text_candidates(text,index)}=={'JAM66D42-580/MB','JAM66D42-585/MB','JAM66D42-590/MB'}

def test_automatic_verification_never_uses_confidence_as_proof(index):
    class Reader:
        def __init__(self):self.calls=0
        def _request(self,messages):
            self.calls+=1
            if self.calls==1:return {'text':'JAM66D42-605/MB','models':[model('JAM66D42-605/MB')]}
            return {'text':'看不清完整型号','models':[]}
    buff=io.BytesIO();Image.new('RGB',(300,400),'white').save(buff,format='PNG')
    r=identify(buff.getvalue(),'test.png',index,Reader())
    assert not r['candidates'] and r['unresolved_candidates']
    assert r['recognition_status']=='insufficient_evidence'

def test_two_different_visible_power_bins_are_not_merged_or_guessed(index):
    class Reader:
        def __init__(self):self.calls=0
        def _request(self,messages):
            self.calls+=1
            if self.calls==1:return {'text':'JAM66D42-605/MB','models':[model('JAM66D42-605/MB')]}
            return {'text':'JAM66D42-600/MB','models':[model('JAM66D42-600/MB')]}
    buff=io.BytesIO();Image.new('RGB',(300,400),'white').save(buff,format='PNG')
    r=identify(buff.getvalue(),'test.png',index,Reader())
    assert [m['raw_model'] for m in r['candidates']]==['JAM66D42-600/MB']
    assert [m['raw_model'] for m in r['unresolved_candidates']]==['JAM66D42-605/MB']
    assert all(m['verification']['view_count']>=2 for m in r['candidates'])

def test_notes_string_is_not_split_into_characters():
    from recognition_agent import note_list
    assert note_list('未披露组件型号')==['未披露组件型号']

def test_title_and_scan_body_pdf_forces_visual_reading():
    import pymupdf
    from recognition_agent import page_requires_visual
    d=pymupdf.open();p=d.new_page(width=600,height=800)
    p.insert_text((20,20),'Attachment Filing Certificate '*5)
    im=io.BytesIO();Image.new('RGB',(600,600),'white').save(im,format='PNG')
    p.insert_image(pymupdf.Rect(20,80,580,750),stream=im.getvalue())
    assert page_requires_visual(p,p.get_text(sort=True))

def test_power_and_credit_number_cannot_be_promoted_by_vlm(index):
    from recognition_agent import normalized_models
    raw=['590Wp','2505-530425-04-01-626456','91530428MAEGH3NC2L']
    _,r=normalized_models({'text':' '.join(raw),'models':[model(s) for s in raw]},index)
    assert r==[]

def test_wrapped_same_cell_lines_require_literal_lines_and_position(index):
    from recognition_agent import normalized_models
    m={**model('TSM-455NEG9R.28'),'quote':'TSM-455\nNEG9R.28','visible_lines':['TSM-455','NEG9R.28'],'bbox':[10,20,100,80]}
    _,r=normalized_models({'text':'TSM-455 TSM-460\nNEG9R.28 NEG9R.28','models':[m]},index)
    assert r[0]['raw_model']=='TSM-455NEG9R.28'
    # No suffix in the image transcript: do not invent it from the family rule.
    _,r=normalized_models({'text':'TSM-455 TSM-460','models':[m]},index)
    assert r==[]

def test_missing_trina_suffix_is_series_not_full_model(index):
    result=recognize(index,'TSM-455',[model('TSM-455')])
    assert not result['candidates'] and result['series_candidates']

def test_small_wrapped_model_crop_is_reread_whole_instead_of_split(index):
    from recognition_agent import crop_boxes
    im=Image.new('RGB',(113,68),'white')
    assert crop_boxes(im,{'model_regions':[{'bbox':[0,0,1000,1000]}]})==[(0,0,113,68)]
