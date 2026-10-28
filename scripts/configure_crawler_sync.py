"""Upgrade the dedicated T07 draft to full-text create/update/unchanged sync."""
from copy import deepcopy
from datetime import datetime
import inspect
import json
from pathlib import Path
import sys

from dotenv import dotenv_values
import requests
import yaml

from configure_dify import make_api
from verify_web_crawler_workflow import runtime_dsl, ROOT, STATE


def prepare(title: str, source_url: str, scrape_status: int, scrape_body: str) -> dict:
    import hashlib
    import json
    from urllib.parse import urlsplit
    parsed = urlsplit(source_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid approved public URL')
    body = json.loads(scrape_body)
    markdown = body.get('data', {}).get('markdown')
    if scrape_status != 200 or body.get('success') is not True or not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('Firecrawl did not return full successful Markdown')
    source_key = 't07-' + hashlib.sha256(source_url.encode()).hexdigest()
    text = '来源URL：' + source_url + '\n\n' + markdown
    digest = hashlib.sha256(text.encode()).hexdigest()
    payload = {'name': source_key + ':' + digest, 'text': text,
               'indexing_technique': 'high_quality', 'doc_form': 'text_model',
               'doc_language': 'Chinese', 'process_rule': {'mode': 'automatic'}}
    return {'source_key': source_key, 'content_sha256': digest,
            'request_body': json.dumps(payload, ensure_ascii=False)}


def plan(lookup_status: int, lookup_body: str, source_key: str, request_body: str,
         source_url: str, api_base: str, dataset_id: str) -> dict:
    import json
    import uuid
    value = json.loads(lookup_body)
    if lookup_status != 200 or not isinstance(value.get('data'), list) or value.get('has_more'):
        raise ValueError('Incomplete document lookup; no write attempted')
    docs = [doc for doc in value['data'] if doc['name'].startswith(source_key + ':')]
    if len(docs) > 1:
        raise ValueError('Duplicate source documents require review; no write attempted')
    payload = json.loads(request_body)
    action, document_id = 'created', ''
    if docs:
        old = docs[0]
        document_id = str(uuid.UUID(old['id']))
        if old.get('indexing_status') != 'completed' or not old.get('enabled', True) or old.get('archived'):
            raise ValueError('Existing source is not ready; inspect before another write')
        action = 'unchanged' if old['name'] == payload['name'] else 'updated'
    path = '/documents/' + document_id + '/update-by-text' if document_id else '/document/create-by-text'
    receipt = {'action': action, 'document_id': document_id, 'source_url': source_url,
               'content_sha256': payload['name'].rsplit(':', 1)[-1]}
    return {'write_required': 'no' if action == 'unchanged' else 'yes',
            'write_url': api_base.rstrip('/') + '/datasets/' + str(uuid.UUID(dataset_id)) + path,
            'receipt': json.dumps(receipt, ensure_ascii=False)}


def validate(write_status: int, write_body: str, planned_receipt: str) -> dict:
    import json
    import uuid
    receipt, body = json.loads(planned_receipt), json.loads(write_body)
    doc_id = str(uuid.UUID(body.get('document', {}).get('id', '')))
    if write_status != 200 or not body.get('batch'):
        raise ValueError('Write is missing HTTP200 or indexing batch')
    if receipt['document_id'] and receipt['document_id'] != doc_id:
        raise ValueError('Update changed original document ID')
    receipt.update(document_id=doc_id, batch=body['batch'], write_http_status=write_status)
    return {'receipt': json.dumps(receipt, ensure_ascii=False)}


def node_code(fn):
    return inspect.getsource(fn).replace('def ' + fn.__name__ + '(', 'def main(', 1)


def graph():
    doc = runtime_dsl()
    nodes = doc['workflow']['graph']['nodes']
    by_id = {n['id']: n for n in nodes}
    for n in nodes:
        if n['data']['type'] == 'parameter-extractor':
            n['data']['model'].update(provider='langgenius/openai_api_compatible/openai_api_compatible', name='Pro/Qwen/Qwen2.5-7B-Instruct')
            n['data']['model']['completion_params']['max_tokens'] = 128
    # Explicit refresh: Firecrawl's default cache can otherwise conceal real source changes.
    body = by_id['firecrawl']['data']['body']['data'][0]
    body['value'] = body['value'].replace('"onlyMainContent":true', '"onlyMainContent":true,"maxAge":0,"skipTlsVerification":false')
    by_id['serialize']['data'].update(code=node_code(prepare), outputs={k: {'type': 'string'} for k in ['source_key', 'content_sha256', 'request_body']}, desc='JSON解析完整Markdown；来源URL和全文SHA构成文档标识，不由模型转写正文。')
    internal = {'isInIteration': True, 'iteration_id': 'each_url'}
    def add(id, kind, title, data):
        n = deepcopy(by_id['serialize'])
        n.update(id=id, position={'x': 350 + 180 * len(nodes), 'y': 160})
        n['data'] = {'type': kind, 'title': title, **internal, **data}
        nodes.append(n); by_id[id] = n
    lookup = deepcopy(by_id['write_knowledge']['data'])
    lookup.update(method='get', url='{{#env.dify_api_base#}}/datasets/{{#env.dataset_id#}}/documents',
                  params='keyword:{{#serialize.source_key#}}\nlimit:100', body={'type': 'none', 'data': []}, desc='按URL稳定标识查询，不依赖Windows映射或人工登记。')
    lookup.pop('title')
    add('lookup', 'http-request', '查询原文档', lookup)
    variables = [{'variable': v, 'value_selector': selector} for v, selector in [
        ('lookup_status', ['lookup','status_code']), ('lookup_body',['lookup','body']),
        ('source_key',['serialize','source_key']), ('request_body',['serialize','request_body']),
        ('source_url',['each_url','item']), ('api_base',['env','dify_api_base']), ('dataset_id',['env','dataset_id'])]]
    add('plan', 'code', '判定新增、变更或未变', {'code_language':'python3','code':node_code(plan),'variables':variables,
         'outputs':{k:{'type':'string'} for k in ['write_required','write_url','receipt']}})
    add('changed', 'if-else', '内容需要写入？', {'cases':[{'id':'true','case_id':'true','logical_operator':'and',
        'conditions':[{'id':'needs-write','variable_selector':['plan','write_required'],'comparison_operator':'is','value':'yes','varType':'string'}]}]})
    by_id['write_knowledge']['data'].update(url='{{#plan.write_url#}}',desc='首次新增或更新原ID；不自动重试，失败先检查写入结果。')
    add('validate_write','code','校验写入回执',{'code_language':'python3','code':node_code(validate),
        'variables':[{'variable':v,'value_selector':sel} for v,sel in [('write_status',['write_knowledge','status_code']),('write_body',['write_knowledge','body']),('planned_receipt',['plan','receipt'])]],
        'outputs':{'receipt':{'type':'string'}}})
    add('receipt','variable-aggregator','汇总同步回执',{'variables':[['validate_write','receipt'],['plan','receipt']],'output_type':'string','advanced_settings':{'group_enabled':False,'groups':[]}})
    children=['iteration_start','firecrawl','extract_page','serialize','lookup','plan','changed','write_knowledge','validate_write','receipt']
    by_id['each_url']['data'].update(_children=children,output_selector=['receipt','output'],desc='串行按URL同步；相同内容跳过、变更更新原ID。')
    for id, x, y in [('lookup',1120,100),('plan',1450,100),('changed',1780,100),
                     ('write_knowledge',2110,100),('validate_write',2440,100),('receipt',2770,240)]:
        by_id[id]['position']={'x':x,'y':y}
    by_id['each_url'].update(width=3120,height=520)
    by_id['end']['position']={'x':3920,'y':240}
    by_id['end']['data']['desc']='同步回执需再核索引和检索；未变分支没有写入。'
    edge_pairs=[('start','extract_urls'),('extract_urls','each_url'),('each_url','end'),('iteration_start','firecrawl'),('firecrawl','extract_page'),('extract_page','serialize'),('serialize','lookup'),('lookup','plan'),('plan','changed'),('changed','write_knowledge','true'),('changed','receipt','false'),('write_knowledge','validate_write'),('validate_write','receipt')]
    edges=[]
    for pair in edge_pairs:
        a,b=pair[:2]; handle=pair[2] if len(pair)==3 else 'source'
        data={'sourceType':by_id[a]['data']['type'],'targetType':by_id[b]['data']['type']}
        if a in children: data.update(internal)
        edges.append({'id':a+'-'+b,'type':'custom','source':a,'sourceHandle':handle,'target':b,'targetHandle':'target','data':data})
    doc['workflow']['graph']['edges']=edges
    doc['app']['description']='T07真实网页同步：完整Markdown、URL稳定标识、未变跳过、变更更新原ID；串行显式运行。'
    return doc


def self_check():
    import hashlib
    url='https://example.com/t07'
    value=prepare('test',url,200,json.dumps({'success':True,'data':{'markdown':'完整正文 "quote"\n第二段'}}))
    request=value['request_body']; key=value['source_key']; old_id='00000000-0000-4000-8000-000000000001'; ds='00000000-0000-4000-8000-000000000002'
    def decision(docs): return plan(200,json.dumps({'data':docs,'has_more':False}),key,request,url,'http://api/v1',ds)
    assert decision([])['write_required']=='yes'
    old={'id':old_id,'name':json.loads(request)['name'],'indexing_status':'completed'}
    assert decision([old])['write_required']=='no'
    assert json.loads(decision([{**old,'name':key+':old'}])['receipt'])['action']=='updated'
    for docs in [[old,old],[{**old,'indexing_status':'indexing'}]]:
        try: decision(docs)
        except ValueError: pass
        else: raise AssertionError('Unsafe write was accepted')
    assert json.loads(request)['text'].endswith('完整正文 "quote"\n第二段')
    return {'self_check':'passed','nodes':len(graph()['workflow']['graph']['nodes'])}


if __name__ == '__main__':
    if '--self-check' in sys.argv:
        print(json.dumps(self_check())); sys.exit()
    self_check()
    cfg=dotenv_values(ROOT/'.env',interpolate=False)
    state=json.loads(STATE.read_text(encoding='utf-8'))
    s=requests.Session();s.trust_env=False
    api=make_api(cfg['DIFY_BASE_URL'],s)
    api.token=api('POST','/login',{'email':cfg['DIFY_ADMIN_EMAIL'],'password':cfg['DIFY_ADMIN_PASSWORD']})['data']['access_token']
    old=api('GET',f"/apps/{state['app_id']}/workflows/draft")
    private=ROOT/'data/private'/('crawler-before-sync-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    private.write_text(json.dumps(old,ensure_ascii=False),encoding='utf-8')
    doc=graph(); payload=doc['workflow']
    # Reuse this app and its dataset only; never bind the customer assistant dataset.
    for e in payload['environment_variables']:
        e['value']={'firecrawl_api_key':cfg['FIRECRAWL_API_KEY'],'dataset_api_key':cfg['DIFY_DATASET_API_KEY'],'dataset_id':state['dataset_id'],'dify_api_base':cfg['DIFY_BASE_URL']+'/v1'}[e['name']]
    result=api('POST',f"/apps/{state['app_id']}/workflows/draft",{'hash':old['hash'],'graph':payload['graph'],'features':payload['features'],'environment_variables':payload['environment_variables'],'conversation_variables':[]})
    clean=graph()
    (ROOT/'services/web_crawler_sync_workflow.yml').write_text(yaml.safe_dump(clean,allow_unicode=True,sort_keys=False),encoding='utf-8')
    print(json.dumps({'app_id':state['app_id'],'dataset_id':state['dataset_id'],'nodes':len(payload['graph']['nodes']),'draft_saved':bool(result),'published':False,'secrets_logged':False}))
