"""Local still-image control panel for human-driven Ditto captures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse

from bridge import BridgeError


PANEL = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ditto recorder</title>
<style>body{font:16px system-ui;max-width:900px;margin:20px auto;padding:0 16px;color:#191919}
main{display:flex;gap:24px;flex-wrap:wrap}section{min-width:290px;flex:1}
img{width:100%;max-width:400px;border:1px solid #bbb;touch-action:none}
button,select,input{font:inherit;margin:5px 4px 5px 0;padding:8px}
#message{white-space:pre-wrap;color:#942a42}small{display:block;color:#555}</style>
<h1>Ditto recorder</h1><p>Click or swipe the still image to control the emulator.
Each action is logged by the local MCP. Capture after the screen settles.</p>
<main><section><img id="screen" alt="Current emulator screen"><br><button id="refresh">Refresh still</button>
</section><section><label>Checkpoint <select id="checkpoint"></select></label>
<p><strong>Start here:</strong> <span id="setup"></span></p>
<p><strong>Capture when:</strong> <span id="capture-when"></span></p>
<details><summary>Inputs for this run</summary><dl id="fixture"></dl></details>
<p id="step"></p><label><input type="checkbox" id="incidental">Preparation / popup action</label>
<small>Use for focusing a field, scrolling to a control, or dismissing a popup without completing the named step.</small>
<p><input id="input" placeholder="Text to type"><button id="type">Type</button>
<button id="back">Back</button></p>
<details><summary>Screen marker for replay</summary><input id="expect" placeholder="Unique screen text (optional)">
<small>The AI can supply this in advance. Use a screen heading, not a shared label such as Next.</small></details>
<button id="capture">Capture checkpoint</button><button id="finish">Finish capture</button>
<button id="handoff">Return control to AI</button>
<p id="message"></p></section></main>
<script>
const token=__TOKEN__, q='?token='+encodeURIComponent(token);
const screen=document.getElementById('screen'), select=document.getElementById('checkpoint');
const message=document.getElementById('message'), incidental=document.getElementById('incidental');
let status={},busy=false,ended=false,activeCheckpoint=null;
function controls(disabled){document.querySelectorAll('button,input,select').forEach(e=>e.disabled=disabled);
screen.style.pointerEvents=disabled?'none':'auto'}
async function operation(work){if(busy||ended)return;busy=true;controls(true);
try{await work()}catch(e){message.textContent=e.message}finally{busy=false;controls(ended)}}
async function call(path,data){const r=await fetch('/api/'+path+q,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const value=await r.json();if(!r.ok)throw Error(value.error||r.statusText);return value}
async function refresh(){const r=await fetch('/api/status'+q);status=await r.json();
if(!r.ok)throw Error(status.error||r.statusText);
if(!select.options.length){for(const c of status.checkpoints){const o=document.createElement('option');
o.value=c.id;o.textContent=c.number+' '+c.id;select.append(o)}}
if(status.completed.includes(select.value)){
const next=status.checkpoints.find(c=>!status.completed.includes(c.id));
if(next)select.value=next.id}
const c=status.checkpoints.find(c=>c.id===select.value)||status.checkpoints[0];
document.getElementById('setup').textContent=c.setup;
document.getElementById('capture-when').textContent=c.capture_when||'Complete the listed actions, then wait for the screen to settle.';
const fixture=document.getElementById('fixture');fixture.replaceChildren();
for(const [key,value] of Object.entries(c.fixture_values)){
const label=document.createElement('dt'),detail=document.createElement('dd');
label.textContent=key.replaceAll('_',' ');detail.textContent=typeof value==='string'?value:JSON.stringify(value);
fixture.append(label,detail)}
if(activeCheckpoint!==c.id){document.getElementById('expect').value=c.expect||'';activeCheckpoint=c.id}
document.getElementById('step').textContent='Next declared action: '+(c?.actions[status.pending.length]||'none')+
'\nCompleted: '+status.completed.join(', ');
await new Promise((resolve,reject)=>{screen.onload=resolve;screen.onerror=()=>reject(Error('Could not refresh screen'));
screen.src='/api/screen'+q+'&v='+Date.now()})}
async function act(data){await operation(async()=>{await call('action',{
checkpoint_id:select.value,incidental:incidental.checked,...data});
incidental.checked=false;message.textContent='Action recorded';await refresh()})}
screen.addEventListener('pointerdown',e=>{if(busy||ended)return;
screen.setPointerCapture(e.pointerId);screen.dataset.x=e.offsetX;screen.dataset.y=e.offsetY;screen.dataset.time=Date.now()});
screen.addEventListener('pointerup',e=>{if(busy||ended||!screen.dataset.time)return;
const x=+screen.dataset.x,y=+screen.dataset.y,started=+screen.dataset.time;delete screen.dataset.time;
const scaleX=screen.naturalWidth/screen.clientWidth,scaleY=screen.naturalHeight/screen.clientHeight;
const endX=Math.round(e.offsetX*scaleX),endY=Math.round(e.offsetY*scaleY);
const startX=Math.round(x*scaleX),startY=Math.round(y*scaleY);
if(Math.hypot(endX-startX,endY-startY)>25)act({action:'swipe',x:startX,y:startY,end_x:endX,end_y:endY,
duration_ms:Math.max(100,Math.min(1000,Date.now()-started))});
else act({action:'tap',x:endX,y:endY})});
document.getElementById('type').onclick=()=>act({action:'type',text:document.getElementById('input').value});
document.getElementById('back').onclick=()=>act({action:'back'});
document.getElementById('refresh').onclick=()=>operation(refresh);select.onchange=()=>operation(refresh);
document.getElementById('capture').onclick=()=>operation(async()=>{await call('capture',{
checkpoint_id:select.value,expect:document.getElementById('expect').value.trim()});
document.getElementById('expect').value='';message.textContent='Checkpoint saved';await refresh()});
document.getElementById('finish').onclick=()=>operation(async()=>{const v=await call('finalize',{});
ended=true;message.textContent='Saved to '+v.export_dir});
document.getElementById('handoff').onclick=()=>operation(async()=>{await call('handoff',{});
ended=true;message.textContent='Session preserved. Tell the AI to resume.'});
operation(refresh);</script></html>'''


class Recorder:
    def __init__(self, mobile, contract_path, lock=None, checkpoint_ids=None):
        self.mobile = mobile
        self.lock = lock or threading.RLock()
        self.contract = json.loads(Path(contract_path).read_text(encoding='utf-8'))
        checkpoints = self.contract.get('checkpoints')
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError('phase contract has no checkpoints')
        self.checkpoints = {item['id']: item for item in checkpoints}
        if len(self.checkpoints) != len(checkpoints):
            raise ValueError('phase contract has duplicate checkpoint IDs')
        if checkpoint_ids is not None:
            if (not isinstance(checkpoint_ids, list) or not checkpoint_ids
                    or any(not isinstance(key, str) or key not in self.checkpoints for key in checkpoint_ids)
                    or len(set(checkpoint_ids)) != len(checkpoint_ids)):
                raise ValueError('checkpoint_ids contains unknown, duplicate, or no checkpoints')
            self.checkpoints = {key: item for key, item in self.checkpoints.items() if key in checkpoint_ids}
        self.handoff_requested = False
        self.token = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    def _checkpoint(self, identifier):
        if self.handoff_requested:
            raise ValueError('recorder handed back; ask the AI to resume')
        if identifier not in self.checkpoints:
            raise ValueError(f'unknown checkpoint: {identifier}')
        if self.mobile.stage is None:
            raise ValueError('begin mobile capture before opening the recorder')
        completed = {record['checkpoint_id'] for record in self.mobile.records}
        if identifier in completed:
            raise ValueError(f'checkpoint already complete: {identifier}')
        next_checkpoint = next((key for key in self.checkpoints if key not in completed), None)
        if identifier != next_checkpoint:
            raise ValueError(f'next checkpoint is {next_checkpoint}')
        return self.checkpoints[identifier]

    def status(self):
        return {'phase_id': self.contract['phase_id'],
                'handoff_requested': self.handoff_requested,
                'capture_active': self.mobile.stage is not None,
                'checkpoints': [{'number': item['number'], 'id': item['id'],
                                 'actions': item['actions'], 'setup': item['setup'],
                                 'capture_when': item.get('capture_when'), 'expect': item.get('expect'),
                                 'fixture_values': self.contract.get('fixtures', {}).get(item['fixture'], {})}
                                for item in self.checkpoints.values()],
                'completed': list(dict.fromkeys(record['checkpoint_id']
                                                for record in self.mobile.records)),
                'pending': [event['step'] for event in self.mobile.actions if event.get('step')]}

    def action(self, identifier, request):
        checkpoint = self._checkpoint(identifier)
        action = request.get('action')
        if action not in ('tap', 'swipe', 'type', 'back'):
            raise ValueError('recorder supports tap, swipe, type, and back')
        performed = [event['step'] for event in self.mobile.actions if event.get('step')]
        expected = checkpoint['actions']
        if performed != expected[:len(performed)]:
            raise ValueError('recorded actions differ from selected checkpoint')
        incidental = request.get('incidental', False)
        if not isinstance(incidental, bool):
            raise ValueError('incidental must be boolean')
        if not incidental and len(performed) >= len(expected):
            raise ValueError('all declared actions are recorded; select incidental or capture')
        step = None if incidental else expected[len(performed)]
        arguments = {key: request[key] for key in ('x', 'y', 'end_x', 'end_y',
                                                    'text', 'duration_ms') if key in request}
        selector = None
        if action == 'tap' and 'x' in arguments and 'y' in arguments:
            try:
                visible = self.mobile.inspect_ui(limit=100)
                if not visible['truncated']:
                    nodes = visible['nodes']
                    hits = [node for node in nodes if node['bounds'][0] <= arguments['x'] < node['bounds'][2]
                            and node['bounds'][1] <= arguments['y'] < node['bounds'][3]]
                    hits.sort(key=lambda node: (node['bounds'][2] - node['bounds'][0]) *
                              (node['bounds'][3] - node['bounds'][1]))
                    for node in hits:
                        for field in ('resource_id', 'description', 'text'):
                            value = node[field]
                            if value and sum(any(value.casefold() == item[key].casefold() for key in
                                    ('resource_id', 'description', 'text')) for item in nodes) == 1:
                                selector = value
                                break
                        if selector:
                            break
            except BridgeError:
                pass  # Coordinate recording remains usable when the app exposes no hierarchy.
        event = self.mobile.perform(action, step=step, **arguments)
        if selector:
            event['replay_selector'] = selector
        return event

    def capture(self, identifier, expect=''):
        checkpoint = self._checkpoint(identifier)
        expect = expect or checkpoint.get('expect') or ''
        if expect:
            self.mobile._target(expect)
        visible = self.mobile.inspect_ui(limit=40)['nodes']
        labels = [node['text'] or node['description'] for node in visible]
        description = ('Human-marked checkpoint; visible UI: '
                       + '; '.join(label[:100] for label in labels[:16] if label))
        result = self.mobile.capture(number=checkpoint['number'],
                                   checkpoint_id=identifier, fixture=checkpoint['fixture'],
                                   setup=checkpoint['setup'], actions=checkpoint['actions'],
                                   kinds=checkpoint['artifacts'], observed_state=description,
                                   observation_source='human_recorder')
        if expect:
            self.mobile.replay_plan[-1]['expect'] = expect
        return result

    def handoff(self):
        self.handoff_requested = True
        return self.status()

    def finalize(self):
        if self.handoff_requested:
            raise ValueError('recorder handed back; ask the AI to resume')
        completed = set(self.status()['completed'])
        missing = set(self.checkpoints) - completed
        if missing:
            raise ValueError('capture remaining checkpoints: ' + ', '.join(sorted(missing)))
        result = self.mobile.finalize()
        self.mobile.release_device()
        return result

    def start(self):
        if self.mobile.stage is None:
            raise ValueError('begin mobile capture before opening the recorder')
        if self.server is not None:
            return self.url
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _authorized(self):
                return parse_qs(urlparse(self.path).query).get('token', [''])[0] == recorder.token

            def _send(self, status, data, mime='application/json'):
                payload = (json.dumps(data).encode() if mime == 'application/json' else data)
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                path = urlparse(self.path).path
                try:
                    with recorder.lock:
                        if path == '/':
                            html = PANEL.replace('__TOKEN__', json.dumps(recorder.token))
                            return self._send(200, html.encode(), 'text/html; charset=utf-8')
                        if path == '/api/status':
                            return self._send(200, recorder.status())
                        if path == '/api/screen':
                            return self._send(200, recorder.mobile._screen(), 'image/png')
                    self._send(404, {'error': 'unknown route'})
                except Exception as error:
                    self._send(400, {'error': str(error)})

            def do_POST(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= 65536:
                        raise ValueError('request body too large')
                    request = json.loads(self.rfile.read(length))
                    path = urlparse(self.path).path
                    with recorder.lock:
                        if path == '/api/action':
                            result = recorder.action(request.get('checkpoint_id'), request)
                        elif path == '/api/capture':
                            result = recorder.capture(request.get('checkpoint_id'), request.get('expect', ''))
                        elif path == '/api/handoff':
                            result = recorder.handoff()
                        elif path == '/api/finalize':
                            result = recorder.finalize()
                        else:
                            return self._send(404, {'error': 'unknown route'})
                    self._send(200, result)
                except Exception as error:
                    self._send(400, {'error': str(error)})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/?token={self.token}'

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = self.thread = None
