"""Replace the five authorised snapshot datasets and verify every uploaded row."""
import argparse
import base64
from datetime import datetime
from hashlib import sha256
import csv
import json
from pathlib import Path
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
BASE='https://www.eduplus.net/dc_dataeaseapi/de-api/'
PANEL='9ca8233d-cdb7-11f1-b77c-57a8412ac418'
FOLDER=ROOT/'evidence/20261024/mofang_refresh'
CHARTS={
    'month':('聊天密度月分布.csv','31b8831c-a474-4aed-8e71-d56d9adda00d','92c5aad4-db90-49f8-be27-70e22ffa5968'),
    'hour':('聊天密度小时分布.csv','b11c5adf-4d70-40fc-8ef2-202d36152700','03768d95-b8ce-42ff-bca2-f6ba5876ca14'),
    'word':('聊天词语频次数据.csv','609825f9-ed53-40d8-b927-11dfb826b459','262cdbbd-d4da-4082-9b3a-467992114dca'),
    'topic':('月度主题趋势数据.csv','f6398544-7fce-426d-8e54-7fac01983617','c06e298a-b389-4341-9954-f5b53165c320'),
    'sentiment':('情感分布.csv','d219e8c3-82be-40bf-81d2-326ce6989f7c','17508a4b-7260-43d5-940f-9025a732b16b'),
}


def canonical(rows):
    return sorted(tuple(sorted((k.lstrip('\ufeff'),str(int(float(v))) if k.lstrip('\ufeff') in ('count','hour','topic') else str(v)) for k,v in r.items())) for r in rows)


def decoded(rows, fields):
    names={f['dataeaseName']:f['originName'].lstrip('\ufeff') for f in fields}
    return [{names[k]:v for k,v in row.items() if k in names} for row in rows]


def main(batch,stage):
    FOLDER.mkdir(parents=True,exist_ok=True)
    target=ROOT/'analysis/output'/batch
    manifest=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
    assert batch=='preset_refresh_20261024_jan_jul', 'Only this reviewed refresh batch is authorised by this helper'
    auth=json.loads((ROOT/'data/private/mofang-auth.json').read_text(encoding='utf-8'))
    s=requests.Session();s.trust_env=False;s.headers.update({'Authorization':auth['token'],'IdToken':auth['token']})
    def api(method,path,payload=None,**kwargs):
        r=s.request(method,BASE+path,json=payload,timeout=(10,45),**kwargs)
        d=r.json()
        if not d.get('success'):raise RuntimeError(path+': '+str(d.get('message')))
        return d['data']
    report_path=FOLDER/'acceptance.json'
    report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {
        'batch':batch,'panel_id':PANEL,'status':'in_progress','datasets':{},'charts':{},'secrets_logged':False,
        'original_batch_preserved':True,'original_csv_folder':'analysis/output/preset_deploy_20261020',
        'panel_before_backup':'data/private/mofang-live-before-refresh.json',
        'new_resource_attempt':{'dataset_id':'3cf5d510-bf8c-42a8-881e-a6aae9b2c036','creation_returned_http500_but_native_list_contains_resource':True,'read_and_cleanup_denied_by_platform':True,'used_by_dashboard':False,'cleanup_pending_platform_owner':True}}
    def save():
        report['updated_at']=datetime.now().astimezone().isoformat()
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def expected(file):
        with (target/file).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    try:
        if stage=='datasets':
            for kind,(file,table_id,view_id) in CHARTS.items():
                want=expected(file)
                prior=api('POST','dataset/table/getPreviewData/1/200',{'id':table_id,'row':200})
                old_fields=api('POST','dataset/field/list/'+table_id,{})
                already=canonical(decoded(prior['data'],prior['fields']))==canonical(want)
                item=report['datasets'].setdefault(kind,{'file':str((target/file).relative_to(ROOT)),'table_id':table_id,'view_id':view_id,'csv_sha256':sha256((target/file).read_bytes()).hexdigest()})
                if not already:
                    with (target/file).open('rb') as stream:
                        r=s.post(BASE+'dataset/table/excel/upload',files={'file':(file,stream,'text/csv')},data={'tableId':table_id,'editType':'0'},timeout=(10,45))
                    upload=r.json();assert upload.get('success'), 'CSV upload parse failed'
                    old=json.loads((ROOT/('data/private/mofang-dataset-before-'+table_id+'.json')).read_text(encoding='utf-8'))
                    payload={'id':table_id,'name':old['name'],'sceneId':old['sceneId'],'dataSourceId':None,'type':'excel','sheets':upload['data']['sheets'],'mode':1,'editType':0}
                    (ROOT/('data/private/mofang-refresh-upload-'+kind+'.json')).write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                    api('POST','dataset/table/update',payload)
                    item['replace_http_success']=True;save()
                else:
                    item['already_has_target_rows']=True
                deadline=time.monotonic()+60
                while True:
                    actual=api('POST','dataset/table/getPreviewData/1/200',{'id':table_id,'row':200})
                    if canonical(decoded(actual['data'],actual['fields']))==canonical(want):break
                    if time.monotonic()>deadline:raise RuntimeError('Uploaded table differs from target CSV: '+kind)
                    time.sleep(2)
                # Replacement must keep the original chart field IDs and dimension types.
                fields=api('POST','dataset/field/list/'+table_id,{})
                assert {f['id'] for f in fields}=={f['id'] for f in old_fields}
                assert {(f['id'],f['groupType']) for f in fields}=={(f['id'],f['groupType']) for f in old_fields}
                item.update(all_rows_match=True,rows=len(want),sum_count=sum(int(r['count']) for r in want),field_ids_and_groups_preserved=True)
                save();print('uploaded_verified',kind,len(want),flush=True)
        elif stage=='panel':
            assert all(report['datasets'].get(k,{}).get('all_rows_match') for k in CHARTS)
            panel=api('GET','panel/group/findOne/'+PANEL)
            tiles=json.loads(panel['panelData'])
            svg=ROOT/'artifacts/mofang'/batch/'情感玫瑰图_初版面积.svg'
            picture=next(t for t in tiles if t['component']=='Picture')
            picture['propValue']='data:image/svg+xml;base64,'+base64.b64encode(svg.read_bytes()).decode()
            notes=[t for t in tiles if t['component']=='v-text']
            notes[0]['propValue']='课程预置数据｜刷新批次 '+batch+'｜2024-01-01—2024-07-28｜有效42,860条（原始44,228条；排除1,368条）'
            notes[1]['propValue']='口径：筛选2024年1—7月，实际截至7月28日、末月未满自然月；小时为全期累计；主题沿用原模型编号；情感标签未校准，非投诉率或满意度。'
            for kind,(file,table_id,view_id) in CHARTS.items():
                view=api('POST','chart/view/get/'+view_id+'/'+PANEL,{})
                style=json.loads(view['customStyle'])
                style['text']['remark']='批次：'+batch+'；课程预置来源；数据期间2024-01-01至2024-07-28；末月未满自然月；沿用原模型逐条结果重新汇总。'
                view['customStyle']=json.dumps(style,ensure_ascii=False,separators=(',',':'))
                view.pop('data',None);view.pop('sql',None)
                api('POST','chart/view/save/'+PANEL,view)
            payload={k:panel[k] for k in ['id','name','pid','nodeType','panelStyle','mobileLayout','panelType']}
            payload.update(panelData=json.dumps(tiles,ensure_ascii=False,separators=(',',':')),optType='update')
            api('POST','panel/group/update',payload)
            reread=api('GET','panel/group/findOne/'+PANEL)
            saved=json.loads(reread['panelData'])
            assert next(t['propValue'] for t in saved if t['component']=='Picture')==picture['propValue']
            assert [t['propValue'] for t in saved if t['component']=='v-text']==[t['propValue'] for t in notes]
            (ROOT/'data/private/mofang-refresh-panel-after.json').write_text(json.dumps(reread,ensure_ascii=False),encoding='utf-8')
            report.update(panel_saved_and_reopened=True,static_svg_sha256=sha256(svg.read_bytes()).hexdigest(),visible_notes=[t['propValue'] for t in notes])
            save();print('panel_saved_reopened',flush=True)
        elif stage=='verify':
            assert report.get('panel_saved_and_reopened')
            for kind,(file,table_id,view_id) in CHARTS.items():
                view=api('POST','chart/view/getData/'+view_id+'/'+PANEL,{})
                d=view['data'];actual=decoded(d['tableRow'],d['fields'])
                assert canonical(actual)==canonical(expected(file)), 'Saved chart query differs: '+kind
                report['charts'][kind]={'view_id':view_id,'table_id':table_id,'rows':len(actual),'sum_count':sum(int(r['count']) for r in actual),'all_rows_match':True,'actual_rows':actual,'chart_type':view['type']}
                print('saved_chart_verified',kind,len(actual),flush=True)
            panel=api('GET','panel/group/findOne/'+PANEL)
            old=json.loads((ROOT/'data/private/mofang-live-before-refresh.json').read_text(encoding='utf-8'))['data']
            current=json.loads(panel['panelData']);prior=json.loads(old['panelData'])
            assert [(t['id'],t.get('style'),t.get('canvasStyle')) for t in current]==[(t['id'],t.get('style'),t.get('canvasStyle')) for t in prior]
            assert set(report['charts'])==set(CHARTS)
            report.update(status='passed_upload_saved_reopened_and_all_rows_verified',layout_unchanged=True,user_visual_confirmation=False,browser_screenshot_available=False,static_sentiment_is_interactive=False,total_effective_records=manifest['reports']['cleaning']['retained_rows'])
            save()
        print(json.dumps({'stage':stage,'status':report['status'],'batch':batch},ensure_ascii=False))
    except Exception as e:
        report.update(status='incomplete',error=str(e));save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['datasets','panel','verify'])
    parser.add_argument('--batch',default='preset_refresh_20261024_jan_jul')
    args=parser.parse_args();main(args.batch,args.stage)
