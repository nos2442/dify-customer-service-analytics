"""Read explicit product parameters from the MD; leave other questions to the LLM."""
import inspect
import json
import re


def read_catalog(text):
    products = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) >= 5 and re.fullmatch(r'MX-\d+[A-Z]', cells[0]):
            products[cells[0]] = dict(zip(('容量', '额定功率', '能效等级', '外形'), cells[1:5]))
    warranties = dict(re.findall(r'^\|\s*([^|]+?)\s*\|\s*(\d+)\s*年\s*\|', text, re.M))
    if not products or not all(k in warranties for k in ('整机', '内胆', '加热管')):
        raise ValueError('Product or warranty table is missing; no fast-parameter graph published')
    return products, warranties


def parameter_answer(user_input, history, products, warranties, unavailable_reply=''):
    # Synthetic trace labels carry no question text; arbitrary quoted instructions are retained.
    question = re.sub(r'^\[快测[0-9a-f]{10}-[a-z_]+\]\s*|【直答[0-9a-f]{10}】|^\[最终历史[0-9a-f]{8}\]\s*', '', user_input)
    pattern = r'MX\s*[-－]?\s*\d+\s*[A-Z]'
    def models(text):
        return list(dict.fromkeys('MX-' + re.sub(r'[\s－-]', '', m)[2:]
                                 for m in re.findall(pattern, text.upper())))
    current = models(question)
    if len(current) > 1 or (current and current[0] not in products):
        return ''
    model = current[0] if current else ''
    if not model:
        try:
            turns = json.loads(history) if history else []
        except (ValueError, TypeError):
            turns = []
        if isinstance(turns, list):
            for turn in reversed(turns):
                if not isinstance(turn, dict):
                    continue
                for role in ('user', 'assistant'):
                    found = models(turn.get(role, '')) if isinstance(turn.get(role), str) else []
                    if found:
                        if len(found) != 1 or found[0] not in products:
                            return ''
                        model = found[0]
                        break
                if model:
                    break
    fields = [name for name, cue in (('容量', r'容量|多少升|几升'), ('额定功率', r'功率|多少瓦|几瓦|瓦数'),
                                   ('能效等级', r'能效'), ('外形', r'外形|形状')) if re.search(cue, question)]
    warranty = bool(re.search(r'保修|质保', question))
    missing = bool(re.search(r'售价|价格|库存|优惠', question))
    if not fields and not warranty and not missing:
        return ''
    remainder = re.sub(pattern, '', question, flags=re.I)
    remainder = re.sub(r'[\s，。！？、；：,.;:!?/（）()“”\"\[\]【】]', '', remainder)
    remainder = re.sub(r'可以帮我查一下|帮我查一下|帮我查|告诉我|介绍一下|查一下|看一下|我想知道|请问|你好|您好|最后|这款|那款|这个|型号|整机|内胆|加热管|镁棒|密封圈|易损件|额定|功率|容量|能效等级|能效|外形|形状|保修期|保修|质保|今天|现在|当前|实时|真实|售价|价格|库存|优惠|瓦数|多少|多大|多久|分别|以及|一下|各|几|升|瓦|年|的|是|为|有|和|与|及|它|呢|啊|呀|吗|那|请', '', remainder)
    if remainder or (fields and not model) or (fields and re.search(r'加热管|镁棒|密封圈|易损件', question)):
        return ''
    if missing:
        return unavailable_reply if not fields and not warranty else ''
    parts = [name + '是' + products[model][name] for name in fields]
    if warranty:
        requested = [k for k in warranties if k in question]
        if re.search(r'镁棒|密封圈|易损件', question):
            requested += [k for k in warranties if '易损件' in k]
        for part in dict.fromkeys(requested or ['整机', '内胆']):
            parts.append(part + '保修' + warranties[part] + '年')
    return (model + '的' if model else '') + '，'.join(parts) + '。'


def facts_code(product_text):
    products, warranties = read_catalog(product_text)
    unavailable = ('当前资料不含实时售价和库存信息，请查看购买页面或联系销售方核实。'
                   if '当前资料不含实时售价' in product_text else '')
    return ('import json,re\n' + inspect.getsource(parameter_answer)
            + '\ndef main(user_input: str, history: str) -> dict:\n'
            + '    return {"answer": parameter_answer(user_input, history, '
            + repr(products) + ', ' + repr(warranties) + ', ' + repr(unavailable) + ')}\n')
