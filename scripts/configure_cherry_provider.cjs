// Run with Cherry's bundled Node 22: ELECTRON_RUN_AS_NODE=1 cherrystudio this-file.cjs MODE
// CDP stays on guest loopback. This client touches only the project provider.
const assert = require('node:assert/strict');

async function main() {
  const mode = process.argv[2];
  assert(['configure', 'verify', 'select-qwen', 'verify-qwen', 'quit'].includes(mode), 'Invalid mode');
  const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
  const pages = targets.filter(t => t.type === 'page' &&
    t.url.split(/[?#]/)[0].endsWith('/out/renderer/index.html'));
  assert.equal(pages.length, 1, 'Expected exactly one Cherry main window');
  const ws = new WebSocket(pages[0].webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('CDP connection failed')), { once: true });
  });
  let expression;
  if (mode === 'quit') {
    expression = "setTimeout(() => window.api.quit(), 150); 'quit_requested'";
  } else {
    expression = `(async () => {
      for (let i = 0; i < 600; i++) {
        if (window.store && window.store.getState()._persist && window.store.getState()._persist.rehydrated) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      const store = window.store;
      if (!store || !store.getState()._persist || !store.getState()._persist.rehydrated) {
        throw new Error('Store is not ready');
      }
      const id = 'dify-course-xinference-local';
      const modelIds = ['qwen2.5-instruct', 'deepseek-r1-distill-qwen'];
      let p = store.getState().llm.providers.find(p => p.id === id);
      if (${mode === 'configure'}) {
        const payload = { ...p, id, name: 'Xinference 本地课程模型', type: 'openai',
          apiKey: '', apiHost: 'http://192.168.18.176:9997', enabled: true, isSystem: false,
          models: modelIds.map(id => ({ id, provider: 'dify-course-xinference-local',
            name: id, group: 'Xinference 本地', supported_text_delta: true })) };
        store.dispatch({ type: p ? 'llm/updateProvider' : 'llm/addProvider', payload });
        p = store.getState().llm.providers.find(p => p.id === id);
      }
      if (!p || p.type !== 'openai' || p.apiKey !== '' || !p.enabled ||
          p.apiHost !== 'http://192.168.18.176:9997' ||
          JSON.stringify(p.models.map(m => m.id)) !== JSON.stringify(modelIds) ||
          p.models.some(m => m.provider !== id)) throw new Error('Project provider mismatch');
      const summary = { id: p.id, type: p.type, apiHost: p.apiHost, keyIsEmpty: p.apiKey === '',
        enabled: p.enabled, models: p.models.map(m => ({ id: m.id, provider: m.provider })) };
      if (${['select-qwen', 'verify-qwen'].includes(mode)}) {
        const model = p.models.find(m => m.id === 'qwen2.5-instruct');
        let assistant = store.getState().assistants.assistants.find(a => a.id === 'default');
        if (!assistant || !assistant.topics.length) throw new Error('Project default assistant is missing');
        if (${mode === 'select-qwen'}) {
          store.dispatch({ type: 'llm/setDefaultModel', payload: { model } });
          store.dispatch({ type: 'assistants/setModel', payload: { assistantId: 'default', model } });
          assistant = store.getState().assistants.assistants.find(a => a.id === 'default');
          await new Promise(resolve => setTimeout(resolve, 1500));
        }
        const selected = store.getState().llm.defaultModel;
        if (selected.id !== model.id || selected.provider !== p.id ||
            assistant.model.id !== model.id || assistant.model.provider !== p.id) {
          throw new Error('Project Qwen default mismatch');
        }
        const persisted = JSON.parse(localStorage.getItem('persist:cherry-studio'));
        const savedDefault = JSON.parse(persisted.llm).defaultModel;
        const savedAssistant = JSON.parse(persisted.assistants).assistants.find(a => a.id === 'default');
        if (savedDefault.id !== model.id || savedDefault.provider !== p.id ||
            savedAssistant.model.id !== model.id || savedAssistant.model.provider !== p.id) {
          throw new Error('Project Qwen default has not persisted');
        }
        summary.defaultModel = { id: selected.id, provider: selected.provider };
        summary.defaultAssistant = { id: assistant.id, modelId: assistant.model.id,
          provider: assistant.model.provider };
        summary.persistedDefaultsVerified = true;
      }
      return summary;
    })()`;
  }
  let result;
  try {
    result = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('CDP evaluation timed out')), 75000);
    ws.addEventListener('message', event => {
      const data = JSON.parse(event.data);
      if (data.id !== 1) return;
      clearTimeout(timer);
      if (data.error) reject(new Error('CDP protocol failed'));
      else if (data.result.exceptionDetails) {
        const detail = data.result.exceptionDetails.exception;
        reject(new Error(detail ? detail.description.split('\n')[0] : 'CDP evaluation failed'));
      }
      else resolve(data.result.result.value);
    });
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate',
      params: { expression, returnByValue: true, awaitPromise: true } }));
    });
  } finally {
    ws.close();
  }
  console.log(JSON.stringify({ mode, result }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
