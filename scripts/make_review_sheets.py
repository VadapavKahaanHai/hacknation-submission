"""Build visual-review contact sheets; never changes candidate decisions."""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'processed'/'coffee-v1'
OUTPUT = ROOT/'artifacts'/'visual_review'


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    with (SOURCE/'grouping_review.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows = list(csv.DictReader(stream))
    labels = {}
    with (SOURCE/'inventory.csv').open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            if row['status']=='clean':
                labels.setdefault(row['image_id'],set()).add(row['label_en'])
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',15)
    operations = [None,Image.Transpose.FLIP_LEFT_RIGHT,Image.Transpose.FLIP_TOP_BOTTOM,
                  Image.Transpose.ROTATE_90,Image.Transpose.ROTATE_180,Image.Transpose.ROTATE_270,
                  Image.Transpose.TRANSPOSE,Image.Transpose.TRANSVERSE]
    index = []
    for start in range(0,len(rows),18):
        sheet = Image.new('RGB',(1200,1416),'#dddddd')
        draw = ImageDraw.Draw(sheet)
        for offset,row in enumerate(rows[start:start+18]):
            a = Image.open(SOURCE/row['path_a']).convert('RGB')
            b = Image.open(SOURCE/row['path_b']).convert('RGB')
            variants = [b if op is None else b.transpose(op) for op in operations]
            small_a = np.asarray(a.resize((64,64)),dtype=float)
            errors = [float(np.mean(np.abs(small_a-np.asarray(v.resize((64,64)),dtype=float)))) for v in variants]
            orientation = int(np.argmin(errors))
            x,y = (offset%3)*400,(offset//3)*236
            number = start+offset+1
            label_a = '/'.join(sorted(labels[row['image_a']]))
            label_b = '/'.join(sorted(labels[row['image_b']]))
            draw.text((x+4,y+2),f'{number:03d} | {label_a}/{label_b} | d={row["hamming_distance"]}',font=font,fill='black')
            for image,left in ((a,x+2),(variants[orientation],x+202)):
                tile = ImageOps.contain(image,(196,206))
                sheet.paste(tile,(left+(196-tile.width)//2,y+25+(206-tile.height)//2))
            index.append(dict(pair=number,**row,display_orientation=orientation,
                              display_mae=round(errors[orientation],3),label_a=label_a,label_b=label_b))
        sheet.save(OUTPUT/f'sheet_{start//18+1:02d}.jpg',quality=95)
    with (OUTPUT/'pair_index.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(index[0]))
        writer.writeheader();writer.writerows(index)
    fingerprint=hashlib.sha256((SOURCE/'grouping_review.csv').read_bytes()).hexdigest()
    (OUTPUT/'source.json').write_text(json.dumps({'review_sha256':fingerprint,'pairs':len(rows),
        'note':'Right images oriented for visual comparison; MAE is not a decision rule.'},indent=2))
    print(f'Created {(len(rows)+17)//18} sheets for {len(rows)} pairs; original CSV unchanged.')


if __name__=='__main__':
    main()
