"""Stable dataset/model keys plus user-facing names; no database relabelling."""
import json
from pathlib import Path

CLASS_IDS = {'Rust':1,'Cercospora':2,'Phoma':3,'Miner':4,'Healthy':5,'unknown':6}
CLASS_TYPES = {1:'disease',2:'disease',3:'disease',4:'pest',5:'healthy',6:'unknown'}
MODEL_CLASS_IDS = (1,2,3,4,5,6)
DISPLAY_NAMES = {
    1: ('Coffee leaf rust','कॉफी पत्ती का रतुआ'),
    2: ('Cercospora leaf spot','सर्कोस्पोरा पत्ती धब्बा रोग'),
    3: ('Phoma disease','फोमा रोग'),
    4: ('Coffee leaf miner','कॉफी पत्ती सुरंगक कीट'),
    5: ('Healthy leaf','स्वस्थ पत्ती'),
    6: ('Unknown / uncertain','अज्ञात / अनिश्चित'),
}
ALIASES = {'rust':'Rust','leaf rust':'Rust','coffee leaf rust':'Rust',
           'cercospora':'Cercospora','cerscospora':'Cercospora','cescospora':'Cercospora',
           'cercospora leaf spot':'Cercospora','phoma':'Phoma','phoma disease':'Phoma',
           'miner':'Miner','coffee leaf miner':'Miner','healthy':'Healthy','healthy leaf':'Healthy',
           'unknown':'unknown','unknown / uncertain':'unknown'}
MESSAGES = json.loads((Path(__file__).resolve().parents[1]/'knowledge/ui_messages.json').read_text(encoding='utf-8'))


def canonical_label(folder):
    return ALIASES.get(' '.join(folder.strip().casefold().split()))


def validate_classes(rows):
    actual=[(r['class_id'],r['label_en'],r['type']) for r in rows]
    expected=[(i,label,CLASS_TYPES[i]) for label,i in CLASS_IDS.items()]
    if len(actual)!=6 or sorted(actual)!=expected:
        raise ValueError('Class ID/label/type mapping does not match the fixed model contract')


def class_display(row,language='en'):
    result=dict(row)
    names=DISPLAY_NAMES[result['class_id']]
    result.update(display_name_en=names[0],display_name_hi=names[1],
                  display_name=names[1] if language=='hi' else names[0])
    return result


def fallback(code,language='en'):
    selected=language if language in ('en','hi') else 'en'
    return {'code':code,'language':selected,'text':MESSAGES[code][selected],
            'is_treatment_advice':False,'language_fallback':selected!=language}


def error_fallback(error,language='en'):
    code = {'unreadable_image':'unreadable_image','unsupported_image':'unreadable_image',
            'invalid_image_base64':'unreadable_image','model_not_configured':'model_unavailable',
            'invalid_model_output':'model_unavailable','unsupported_language':'unsupported_language'}.get(error,'request_failed')
    return fallback(code,language)
