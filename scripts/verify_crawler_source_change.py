"""Real HTTP source A/repeat/B/repeat through the dedicated Dify draft."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

from dotenv import dotenv_values
import requests

from configure_dify import make_api
from verify_dify_workflow import safe_error
from verify_web_crawler_workflow import ROOT, STATE, check_fulltext_readback

FOLDER = ROOT / 'evidence/20261024/t07_source_change'
REPORT = FOLDER / 'acceptance.json'
URL = 'https://catering-generators-summit-perspective.trycloudflare.com/source'


def main(stage):
    FOLDER.mkdir(parents=True, exist_ok=True)
    cfg=dotenv_values(ROOT/'.env',interpolate=False)
    prior=json.loads(STATE.read_text(encoding='utf-8'))
    state=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {
        'scope':'real Firecrawl and Dify draft against a project-controlled public test page; no mocked HTTP',
        'source_url':URL,'app_id':prior['app_id'],'dataset_id':prior['dataset_id'],
        'runs':[],'status':'running','secrets_logged':False,'customer_dataset_changed':False}
    def save():
        state['updated_at']=datetime.now().astimezone().isoformat()
        text=json.dumps(state,ensure_ascii=False,indent=2)
        assert not any(v in text for k,v in cfg.items() if v and any(x in k for x in ['KEY','TOKEN','PASSWORD','SECRET']))
        REPORT.write_text(text+'\n',encoding='utf-8')
    s=requests.Session();s.trust_env=False
    api=make_api(cfg['DIFY_BASE_URL'],s)
    api.token=api('POST','/login',{'email':cfg['DIFY_ADMIN_EMAIL'],'password':cfg['DIFY_ADMIN_PASSWORD']})['data']['access_token']
    def dataset(method,path,payload=None):
        r=s.request(method,cfg['DIFY_BASE_URL']+'/v1/datasets/'+state['dataset_id']+path,
                    headers={'Authorization':'Bearer '+cfg['DIFY_DATASET_API_KEY']},json=payload,timeout=(8,75))
        if r.status_code!=200: raise RuntimeError('Dataset HTTP '+str(r.status_code))
        return r.json()
    try:
        expected_action={'first':'created','repeat':'unchanged','changed':'updated','repeat_changed':'unchanged'}[stage]
        assert not any(r['stage']==stage for r in state['runs']), 'Inspect the existing run before resubmission'
        if stage!='first': assert state['runs'][-1].get('status')=='passed'
        if stage=='changed':
            page={'version':'B','code':'T07-B-20261024','message':'第二版：演练受理截止时间已调整为16:30；新增核对项：请保留原文档ID。仅用于检验网页原文更新。'}
            (ROOT/'artifacts/t07/page.json').write_text(json.dumps(page,ensure_ascii=False),encoding='utf-8')
        html=s.get(URL,timeout=(8,30)); assert html.status_code==200
        expected_code='T07-B-20261024' if stage in ('changed','repeat_changed') else 'T07-A-20261024'
        html.encoding='utf-8'; assert expected_code in html.text
        (FOLDER/(stage+'_source.html')).write_text(html.text,encoding='utf-8')
        before=dataset('GET','/documents?limit=100')
        record={'stage':stage,'expected_action':expected_action,'started_at':datetime.now().astimezone().isoformat(),
                'source_html_sha256':sha256(html.content).hexdigest(),'source_http_status':200,'documents_before':before['total']}
        state['runs'].append(record);save()
        with s.post(cfg['DIFY_BASE_URL']+f"/console/api/apps/{state['app_id']}/workflows/draft/run",
                    headers={'Authorization':'Bearer '+api.token},json={'inputs':{'urls_text':URL},'files':[]},
                    stream=True,timeout=(10,180)) as r:
            assert r.status_code==200
            r.encoding='utf-8'
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith('data:'):continue
                ev=json.loads(line[5:]);data=ev.get('data') or {}
                if ev.get('workflow_run_id'):record['workflow_run_id']=ev['workflow_run_id']
                if ev.get('event')=='node_finished':
                    record.setdefault('nodes',[]).append({k:data.get(k) for k in ['node_id','node_type','status','elapsed_time']})
                    print(stage,data.get('node_id'),data.get('status'),flush=True)
                if ev.get('event')=='workflow_finished':
                    record.update(workflow_terminal=True,workflow_status=data.get('status'))
                    if data.get('status')!='succeeded': raise RuntimeError('Workflow failed: '+str(data.get('error')))
                    break
                if ev.get('event')=='error':raise RuntimeError('Workflow error: '+str(ev.get('message')))
        save();assert record.get('workflow_terminal')
        path=f"/apps/{state['app_id']}/workflow-runs/{record['workflow_run_id']}"
        detail=api('GET',path)
        receipts=detail['outputs']['creation_receipts']; assert len(receipts)==1
        receipt=json.loads(receipts[0]);assert receipt['action']==expected_action
        record['receipt']=receipt
        nodes=api('GET',path+'/node-executions')['data']
        serial=next(n for n in nodes if n['node_id']=='serialize')['outputs']
        payload=json.loads(serial['request_body'])
        scrape=next(n for n in nodes if n['node_id']=='firecrawl')['outputs']
        markdown=json.loads(scrape['body'])['data']['markdown']
        assert expected_code in markdown and payload['text']=='来源URL：'+URL+'\n\n'+markdown
        (FOLDER/(stage+'_scraped.md')).write_text(markdown,encoding='utf-8')
        record.update(full_markdown_sha256=sha256(markdown.encode()).hexdigest(),full_markdown_chars=len(markdown),firecrawl_http_status=scrape['status_code'],full_markdown_transmitted_exactly=True)
        if receipt.get('batch'):
            deadline=time.monotonic()+150
            while True:
                status=dataset('GET','/documents/'+receipt['batch']+'/indexing-status')['data']
                if status and all(d['indexing_status']=='completed' for d in status):break
                if time.monotonic()>deadline or any(d['indexing_status'] in ('error','paused') for d in status):raise RuntimeError('Indexing incomplete; do not resubmit')
                time.sleep(2)
        segments=dataset('GET','/documents/'+receipt['document_id']+'/segments?limit=100')
        assert not segments.get('has_more')
        record.update(check_fulltext_readback(payload['text'],segments['data']))
        text='\n'.join(p['content'] for p in segments['data'])
        assert record['readback_body_matches'] and expected_code in text
        if stage in ('changed','repeat_changed'):assert 'T07-A-20261024' not in text and '16:30' in text
        result=dataset('POST','/retrieve',{'query':'验收核对码 演练受理截止时间','retrieval_model':{'search_method':'semantic_search','reranking_enable':False,'top_k':10,'score_threshold_enabled':False}})
        matched=[v for v in result.get('records',[]) if v['segment']['document_id']==receipt['document_id']]
        assert any(expected_code in v['segment']['content'] for v in matched)
        after=dataset('GET','/documents?limit=100')
        old_ids={d['id'] for d in before['data']};new_ids={d['id'] for d in after['data']}
        assert old_ids<=new_ids
        assert after['total']==before['total']+(1 if stage=='first' else 0)
        if stage!='first':assert receipt['document_id']==state['runs'][0]['receipt']['document_id']
        assert ('write_knowledge' in {n['node_id'] for n in record['nodes']})==(expected_action!='unchanged')
        record.update(status='passed',documents_after=after['total'],indexing_completed=True,retrieval_expected_version_found=True,old_version_absent=stage not in ('changed','repeat_changed') or 'T07-A-20261024' not in text,other_document_ids_preserved=True)
        state.update(status='passed_real_source_change_and_idempotent_sync' if stage=='repeat_changed' else 'partial_pass',source_update_verified=stage in ('changed','repeat_changed'),automatic_workflow_idempotency_verified=stage in ('repeat','repeat_changed'))
        if stage=='repeat_changed':
            api('POST',f"/apps/{state['app_id']}/workflows/publish",{'marked_name':'真实网页更新与重复跳过验收通过','marked_comment':'A首次/重复/B原ID更新/重复四轮真实抓取、全文、索引与检索验证。串行显式运行，不含定时调度。'})
            state['published']=True
        save();print(json.dumps({'stage':stage,'status':record['status'],'action':receipt['action'],'document_id':receipt['document_id'],'document_count':after['total']},ensure_ascii=False))
    except Exception as e:
        state.update(status='incomplete',error=safe_error(e,cfg,api.token));save()
        raise RuntimeError(state['error']) from None


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['first','repeat','changed','repeat_changed'])
    args=parser.parse_args();main(args.stage)
