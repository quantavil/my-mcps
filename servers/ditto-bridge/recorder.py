"""Local still-image control panel for human-driven Ditto captures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse


PANEL = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ditto recorder</title>
<style>body{font:16px system-ui;max-width:900px;margin:20px auto;padding:0 16px;color:#191919}
main{display:flex;gap:24px;flex-wrap:wrap}section{min-width:290px;flex:1}
img{width:100%;max-width:400px;border:1px solid #bbb;touch-action:none}
button,select,input{font:inherit;margin:5px 4px 5px 0;padding:8px}
#message{white-space:pre-wrap;color:#942a42}small{display:block;color:#555}</style>
<h1>Ditto original recorder</h1><p>Click or swipe the still image to control the emulator.
Each action is logged by the local MCP. Capture after the screen settles.</p>
<main><section><img id="screen" alt="Current emulator screen"><br><button id="refresh">Refresh still</button>
</section><section><label>Checkpoint <select id="checkpoint"></select></label>
<p id="step"></p><label><input type="checkbox" id="incidental">Incidental system action</label>
<small>Use this for a popup or permission prompt outside the declared app actions.</small>
<p><input id="input" placeholder="Text to type"><button id="type">Type</button>
<button id="back">Back</button></p>
<button id="capture">Capture checkpoint</button><button id="finish">Finish capture</button>
<p id="message"></p></section></main>
<script>
const token=__TOKEN__, q='?token='+encodeURIComponent(token);
const screen=document.getElementById('screen'), select=document.getElementById('checkpoint');
const message=document.getElementById('message'), incidental=document.getElementById('incidental');
let status={};
async function call(path,data){const r=await fetch('/api/'+path+q,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const value=await r.json();if(!r.ok)throw Error(value.error||r.statusText);return value}
async function refresh(){const r=await fetch('/api/status'+q);status=await r.json();
if(!select.options.length){for(const c of status.checkpoints){const o=document.createElement('option');
o.value=c.id;o.textContent=c.number+' '+c.id;select.append(o)}}
if(status.completed.includes(select.value)){
const next=status.checkpoints.find(c=>!status.completed.includes(c.id));
if(next)select.value=next.id}
const c=status.checkpoints.find(c=>c.id===select.value)||status.checkpoints[0];
document.getElementById('step').textContent='Next declared action: '+(c?.actions[status.pending.length]||'none')+
'\nCompleted: '+status.completed.join(', ');
screen.src='/api/screen'+q+'&v='+Date.now()}
async function act(data){try{await call('action',{checkpoint_id:select.value,incidental:incidental.checked,...data});
message.textContent='Action recorded';await refresh()}catch(e){message.textContent=e.message}}
screen.addEventListener('pointerdown',e=>{screen.dataset.x=e.offsetX;screen.dataset.y=e.offsetY;screen.dataset.time=Date.now()});
screen.addEventListener('pointerup',e=>{const x=+screen.dataset.x,y=+screen.dataset.y;
const scaleX=screen.naturalWidth/screen.clientWidth,scaleY=screen.naturalHeight/screen.clientHeight;
const endX=Math.round(e.offsetX*scaleX),endY=Math.round(e.offsetY*scaleY);
const startX=Math.round(x*scaleX),startY=Math.round(y*scaleY);
if(Math.hypot(endX-startX,endY-startY)>25)act({action:'swipe',x:startX,y:startY,end_x:endX,end_y:endY,
duration_ms:Math.max(100,Math.min(1000,Date.now()-screen.dataset.time))});
else act({action:'tap',x:endX,y:endY})});
document.getElementById('type').onclick=()=>act({action:'type',text:document.getElementById('input').value});
document.getElementById('back').onclick=()=>act({action:'back'});
document.getElementById('refresh').onclick=refresh;select.onchange=refresh;
document.getElementById('capture').onclick=async()=>{try{await call('capture',{checkpoint_id:select.value});
message.textContent='Checkpoint saved';await refresh()}catch(e){message.textContent=e.message}};
document.getElementById('finish').onclick=async()=>{try{const v=await call('finalize',{});
message.textContent='Saved to '+v.export_dir}catch(e){message.textContent=e.message}};
refresh();</script></html>'''


class Recorder:
    def __init__(self, mobile, contract_path, lock=None):
        self.mobile = mobile
        self.lock = lock or threading.RLock()
        self.contract = json.loads(Path(contract_path).read_text(encoding='utf-8'))
        checkpoints = self.contract.get('checkpoints')
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError('phase contract has no checkpoints')
        self.checkpoints = {item['id']: item for item in checkpoints}
        if len(self.checkpoints) != len(checkpoints):
            raise ValueError('phase contract has duplicate checkpoint IDs')
        self.token = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    def _checkpoint(self, identifier):
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
                'checkpoints': [{'number': item['number'], 'id': item['id'],
                                 'actions': item['actions']} for item in self.checkpoints.values()],
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
        return self.mobile.perform(action, step=step, **arguments)

    def capture(self, identifier):
        checkpoint = self._checkpoint(identifier)
        visible = self.mobile.inspect_ui(limit=40)['nodes']
        labels = [node['text'] or node['description'] for node in visible]
        description = ('Human-marked checkpoint; visible UI: '
                       + '; '.join(label[:100] for label in labels[:16] if label))
        return self.mobile.capture(number=checkpoint['number'],
                                   checkpoint_id=identifier, fixture=checkpoint['fixture'],
                                   setup=checkpoint['setup'], actions=checkpoint['actions'],
                                   kinds=checkpoint['artifacts'], observed_state=description,
                                   observation_source='human_recorder')

    def finalize(self):
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
                            result = recorder.capture(request.get('checkpoint_id'))
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
