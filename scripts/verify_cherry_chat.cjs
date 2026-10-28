// Guest only, using ELECTRON_RUN_AS_NODE=1 cherrystudio this-file.cjs MODEL_UID --send.
// Requires an already-ready model and the project Cherry profile on loopback CDP.
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const origin = 'http://192.168.18.176:9997';
const prompt = '请只回复：本地模型连接成功';

async function main() {
  const model = process.argv[2];
  assert(['qwen2.5-instruct', 'deepseek-r1-distill-qwen'].includes(model), 'Invalid course model');
  assert.equal(process.argv[3], '--send', 'Explicit --send is required');
  const available = await (await fetch(origin + '/v1/models')).json();
  assert(available.data.some(m => m.id === model), 'Requested Xinference model is not ready');
  const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
  const pages = targets.filter(t => t.type === 'page' &&
    t.url.split(/[?#]/)[0].endsWith('/out/renderer/index.html'));
  assert.equal(pages.length, 1, 'Expected exactly one Cherry main window');
  const ws = new WebSocket(pages[0].webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('CDP connection failed')), { once: true });
  });
  let nextId = 0;
  let blockedRemoteRequests = 0;
  let guardError;
  const pending = new Map();
  const requests = new Map();
  function rpc(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 15000);
      pending.set(id, { resolve, reject, timer });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }
  ws.addEventListener('message', async event => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      const p = pending.get(data.id);
      clearTimeout(p.timer);
      pending.delete(data.id);
      if (data.error) p.reject(new Error('CDP protocol error'));
      else p.resolve(data.result);
    } else if (data.method === 'Fetch.requestPaused') {
      const p = data.params;
      const url = new URL(p.request.url);
      let allowed = ['file:', 'data:', 'blob:', 'devtools:'].includes(url.protocol) || url.origin === origin;
      if (allowed && url.origin === origin && p.request.method === 'POST') {
        try {
          const body = JSON.parse(p.request.postData || '{}');
          const users = body.messages.filter(m => m.role === 'user');
          const content = users.length === 1 && users[0].content;
          const isPrompt = content === prompt || (Array.isArray(content) && content.length === 1 &&
            content[0].type === 'text' && content[0].text === prompt);
          allowed = url.pathname === '/v1/chat/completions' && body.model === model && isPrompt;
          if (!allowed) guardError = 'Unexpected local inference payload blocked';
        } catch (_) { allowed = false; guardError = 'Unreadable inference payload blocked'; }
      }
      if (!allowed) blockedRemoteRequests++;
      rpc(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest',
        allowed ? { requestId: p.requestId } : { requestId: p.requestId, errorReason: 'BlockedByClient' })
        .catch(() => { guardError = 'Network guard failed'; });
    } else if (data.method === 'Network.requestWillBeSent') {
      const p = data.params;
      const url = new URL(p.request.url);
      if (url.origin === origin && url.pathname === '/v1/chat/completions' && p.request.method === 'POST') {
        requests.set(p.requestId, { path: url.pathname, method: 'POST', model });
      }
    } else if (data.method === 'Network.responseReceived' && requests.has(data.params.requestId)) {
      requests.get(data.params.requestId).status = data.params.response.status;
    }
  });
  async function evaluate(expression) {
    const result = await rpc('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert(!result.exceptionDetails, 'Cherry runtime evaluation failed');
    return result.result.value;
  }
  const assistantId = 'course-verify-' + randomUUID();
  const topicId = 'course-verify-' + randomUUID();
  try {
    await rpc('Network.enable');
    await rpc('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await evaluate(`(() => {
      const s = window.store.getState();
      const provider = s.llm.providers.find(p => p.id === 'dify-course-xinference-local');
      if (!provider || provider.apiHost !== ${JSON.stringify(origin)} || provider.apiKey !== '') {
        throw new Error('Project provider mismatch');
      }
      const model = provider.models.find(m => m.id === ${JSON.stringify(model)});
      if (!model || model.provider !== provider.id) throw new Error('Model missing from project provider');
      const now = new Date().toISOString();
      const topic = { id: ${JSON.stringify(topicId)}, assistantId: ${JSON.stringify(assistantId)},
        name: '本地连接核验', isNameManuallyEdited: true, messages: [], createdAt: now, updatedAt: now };
      const assistant = { id: ${JSON.stringify(assistantId)}, name: '本地连接核验：' + model.id,
        emoji: '🧪', type: 'assistant', prompt: '', model, topics: [topic], messages: [], regularPhrases: [],
        settings: { enableTemperature: true, temperature: 0, contextCount: 0,
          enableMaxTokens: true, maxTokens: 512, streamOutput: true, enableTopP: false,
          topP: 1, toolUseMode: 'prompt', customParameters: [] } };
      window.store.dispatch({ type: 'assistants/addAssistant', payload: assistant });
      window.navigate('/', { state: { assistant, topic } });
      return true;
    })()`);
    const inputReady = `window.store.getState().messages.currentTopicId === ${JSON.stringify(topicId)} &&
      document.querySelectorAll('.inputbar textarea').length === 1 &&
      document.querySelectorAll('.inputbar .icon-ic_send').length === 1`;
    let ready = false;
    for (let i = 0; i < 40; i++) {
      if (await evaluate(inputReady)) { ready = true; break; }
      await sleep(250);
    }
    assert(ready, 'Synthetic topic composer did not become ready');
    await evaluate(`(() => { const t = document.querySelector('.inputbar textarea');
      if (t.value !== '') throw new Error('Composer is not empty'); t.focus(); return true; })()`);
    await rpc('Input.insertText', { text: prompt });
    await sleep(200);
    await evaluate(`(() => {
      if (document.querySelector('.inputbar textarea').value !== ${JSON.stringify(prompt)}) throw new Error('Input mismatch');
      document.querySelector('.inputbar .icon-ic_send').click(); return true;
    })()`);
    let answer;
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      assert(!guardError, guardError);
      answer = await evaluate(`(() => {
        const s = window.store.getState();
        const m = Object.values(s.messages.entities).find(m =>
          m.topicId === ${JSON.stringify(topicId)} && m.assistantId === ${JSON.stringify(assistantId)} && m.role === 'assistant');
        if (!m) return null;
        const blocks = m.blocks.map(id => s.messageBlocks.entities[id]).filter(Boolean);
        return { status: m.status, modelId: m.modelId || (m.model && m.model.id),
          text: blocks.filter(b => b.type === 'main_text').map(b => b.content || '').join(''),
          thinkingCharacters: blocks.filter(b => b.type === 'thinking').reduce((n, b) => n + (b.content || '').length, 0),
          error: blocks.some(b => b.type === 'error') };
      })()`);
      if (answer && ['success', 'error', 'paused'].includes(answer.status)) break;
      await sleep(1500);
    }
    assert(answer && answer.status === 'success' && !answer.error, 'Cherry response did not finish successfully');
    assert.equal(answer.modelId, model, 'Response model mismatch');
    assert(answer.text.trim().length > 0, 'Inference finished without a final text reply');
    assert([...requests.values()].some(r => r.status === 200), 'No real Cherry HTTP 200 inference request observed');
    const screenshot = '/home/dify/.cache/dify-project-cherry/verify-' + model + '.png';
    const captured = await rpc('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(screenshot, Buffer.from(captured.data, 'base64'));
    const evidence = { status: 'passed', observed_at: new Date().toISOString(), model, assistantId, topicId, prompt,
      reply: answer.text, expectedPhraseObserved: answer.text.includes('本地模型连接成功'),
      thinkingCharacters: answer.thinkingCharacters, transport: 'Cherry composer → native adapter → Xinference',
      requests: [...requests.values()], blockedRemoteRequests, screenshot };
    fs.writeFileSync(screenshot.replace(/\.png$/, '.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
  } finally {
    await rpc('Fetch.disable').catch(() => {});
    ws.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
