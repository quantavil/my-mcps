"""Free human exploration through the MCP controller; candidates need AI review."""
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse


PANEL = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ditto walkthrough</title>
<style>
body{font:16px/1.45 system-ui;margin:16px;color:#191919;background:#fafafa}
h1{margin:0 0 12px;font-size:1.5rem}h2{margin:0 0 8px;font-size:1.1rem}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(270px,320px);gap:24px;align-items:start}
section,aside{min-width:0}aside{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}
button,input{font:inherit}button{padding:8px 12px;margin:4px 4px 4px 0;cursor:pointer}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid #1d5e9b;outline-offset:2px}
label{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#preview-size{width:min(220px,55vw)}#size-value{font-variant-numeric:tabular-nums}
#screen{display:block;width:min(100%,var(--preview-width,600px));height:auto;touch-action:none;user-select:none;background:#e6e6e6}
#screen.busy{opacity:.7}#guide{max-height:30vh;overflow:auto;padding-left:24px;margin:8px 0}
#guide li{margin:0 0 8px}#typing{display:flex;gap:6px;margin-top:8px}#text{min-width:0;flex:1;padding:8px}
#message{white-space:pre-wrap}#saved-path{display:block;overflow-wrap:anywhere;font:13px/1.4 ui-monospace,monospace}
@media(max-width:760px){main{grid-template-columns:minmax(0,1fr)}aside{position:static;max-height:none;overflow:visible}}
</style>
<h1>Explore this phase</h1><main><section>
<label for="preview-size">Preview size <input id="preview-size" type="range" min="320" max="960" step="20" value="600">
<output id="size-value" for="preview-size">600 px</output></label>
<img id="screen" alt="Waiting for emulator screen" draggable="false">
</section><aside><h2>Look for these states</h2><ol id="guide"></ol>
<p>Click or drag on the preview to explore. Pauses save screens automatically;
use <strong>Save screen</strong> to bookmark one. Backtracking is fine.</p>
<button id="back">Back</button><button id="save">Save screen</button><button id="done">Done</button>
<form id="typing"><input id="text" aria-label="Text to enter" placeholder="Text to enter" maxlength="500">
<button>Type</button></form><p id="message" role="status"></p><p id="counts"></p>
<p><a id="saved-files" target="_blank" rel="noopener noreferrer">Saved files</a>
<output id="saved-path"></output></p>
</aside></main><script>
const q='?token='+encodeURIComponent(__TOKEN__),$=id=>document.getElementById(id);
let busy=false,finished=false,down=null,blobUrl=null;
$('preview-size').oninput=e=>{const width=e.target.value;
$('screen').style.setProperty('--preview-width',width+'px');$('size-value').textContent=width+' px'};
async function api(path,data){const r=await fetch('/api/'+path+q,data===undefined?{}:
{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v}
async function run(path,data){if(busy||finished)return;busy=true;$('screen').classList.add('busy');
$('message').textContent='Sending…';
try{const v=await api(path,data);finished=!!v.finished;
$('message').textContent=finished?'Saved. Tell the AI you are done.':path==='save'?
'Bookmark requested. Pause briefly on this screen.':'Input sent. Wait for the screen to respond.';
if(finished)refresh()}
catch(e){$('message').textContent=e.message}
finally{busy=false;$('screen').classList.remove('busy')}}
function point(e){const r=$('screen').getBoundingClientRect();return {
x:Math.min($('screen').naturalWidth-1,Math.max(0,Math.round((e.clientX-r.left)*$('screen').naturalWidth/r.width))),
y:Math.min($('screen').naturalHeight-1,Math.max(0,Math.round((e.clientY-r.top)*$('screen').naturalHeight/r.height)))}}
$('screen').onpointerdown=e=>{if(busy||finished||!$('screen').naturalWidth)return;
down={...point(e),at:performance.now()};$('screen').setPointerCapture(e.pointerId)};
$('screen').onpointerup=e=>{if(!down)return;const a=down,b=point(e);down=null;
const ms=Math.min(3000,Math.max(100,Math.round(performance.now()-a.at)));
run('action',Math.hypot(b.x-a.x,b.y-a.y)>12||ms>500?
{action:'swipe',x:a.x,y:a.y,end_x:b.x,end_y:b.y,duration_ms:ms}:{action:'tap',x:a.x,y:a.y})};
$('screen').onpointercancel=()=>down=null;
$('back').onclick=()=>run('action',{action:'back'});$('save').onclick=()=>run('save',{});
$('done').onclick=()=>run('finish',{});
$('typing').onsubmit=e=>{e.preventDefault();run('action',{action:'type',text:$('text').value})};
async function refresh(){try{const v=await api('status');finished=v.finished;
if(!$('guide').children.length)for(const c of v.checkpoints){const li=document.createElement('li');
li.textContent=c.capture_when||c.setup||c.id;$('guide').append(li)}
$('counts').textContent=v.saved+' screenshots · '+v.xml_saved+' XML · '+v.actions+' inputs'+
(v.warning?' · '+v.warning:'');
const folder=v.capture_active?v.staging_dir:v.export_dir;
$('saved-path').textContent=folder||'No capture folder yet';
if(folder)$('saved-files').href='/api/files'+q;
if(!down){const r=await fetch('/api/preview'+q);if(r.ok){const next=URL.createObjectURL(await r.blob());
$('screen').src=next;if(blobUrl)URL.revokeObjectURL(blobUrl);blobUrl=next}}
}catch(e){$('counts').textContent=e.message}if(!finished)setTimeout(refresh,700)}refresh();
</script></html>'''


class Recorder:
    def __init__(self, mobile, contract_path, lock=None, checkpoint_ids=None):
        self.mobile = mobile
        self.lock = lock or threading.RLock()
        self.contract = json.loads(Path(contract_path).read_text(encoding='utf-8'))
        checkpoints = self.contract.get('checkpoints')
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError('phase contract has no checkpoints')
        ids = [item['id'] for item in checkpoints]
        if len(set(ids)) != len(ids):
            raise ValueError('phase contract has duplicate checkpoint IDs')
        if checkpoint_ids is not None:
            if (not isinstance(checkpoint_ids, list) or not checkpoint_ids
                    or len(set(checkpoint_ids)) != len(checkpoint_ids)
                    or any(key not in ids for key in checkpoint_ids)):
                raise ValueError('checkpoint_ids contains unknown, duplicate, or no checkpoints')
            checkpoints = [item for item in checkpoints if item['id'] in checkpoint_ids]
        self.checkpoints = checkpoints
        self.shots = []
        self.events = []
        self.finished = False
        self.closed = False
        self.stopped = False
        self.token = secrets.token_urlsafe(24)
        self.server = self.thread = self.worker = self.display = None
        self.halt = threading.Event()
        self.preview = None
        self.preview_revision = -1
        self.stable_frames = 0
        self.revision = 0
        self.last_input = time.monotonic()
        self.bookmark = False
        self.warning = ''
        self.output = mobile.output

    def status(self):
        return {'phase_id': self.contract['phase_id'], 'saved': len(self.shots),
                'xml_saved': sum(shot.get('xml') is not None for shot in self.shots),
                'actions': len(self.events), 'finished': self.finished,
                'warning': self.warning, 'capture_active': self.mobile.stage is not None,
                'staging_dir': str(self.mobile.stage) if self.mobile.stage is not None else None,
                'export_dir': str(self.output),
                'checkpoints': [{'id': c['id'], 'setup': c.get('setup'),
                                 'capture_when': c.get('capture_when')} for c in self.checkpoints]}

    def _active(self):
        if self.finished or self.closed or self.mobile.stage is None:
            raise ValueError('no active recorder; start a new walkthrough')

    def _journal(self, value):
        with (self.mobile.stage / 'actions.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def action(self, payload):
        with self.lock:
            self._active()
            fields = {'tap': {'x', 'y'}, 'swipe': {'x', 'y', 'end_x', 'end_y', 'duration_ms'},
                      'type': {'text'}, 'back': set()}
            kind = payload.get('action')
            if kind not in fields or set(payload) != fields[kind] | {'action'}:
                raise ValueError('use tap, swipe, type, or back with their required arguments')
            if kind == 'type' and (not isinstance(payload['text'], str) or len(payload['text']) > 500):
                raise ValueError('text must be at most 500 characters')
            for key in fields[kind] - {'text'}:
                if type(payload[key]) is not int or not 0 <= payload[key] <= 30000:
                    raise ValueError('coordinates/duration must be integers in 0..30000')
            number = len(self.events) + 1
            event = {'number': number, 'input': payload, 'at': datetime.now(timezone.utc).isoformat()}
            self._journal({**event, 'status': 'requested'})
            self.revision += 1  # Even a failed transport may have changed device state.
            try:
                event['executed'] = self.mobile.perform(**payload)
                event['status'] = 'executed'
            except Exception as error:
                event.update(status='uncertain', error=str(error))
                raise
            finally:
                self.events.append(event)
                self._journal(event)
                self.last_input = time.monotonic()
            return {'number': number, 'status': event['status']}

    def save(self):
        with self.lock:
            self._active()
            if len(self.shots) >= 100:
                raise ValueError('100-screen limit reached; finish this batch')
            self.bookmark = True
            return {'queued': True}

    def _manifest(self):
        payload = {'schema_version': 1, 'kind': 'ditto_exploration',
                   'eligible_for_phase': False, 'phase_id': self.contract['phase_id'],
                   'target': self.mobile.target, 'environment': self.mobile.environment,
                   'package_name': self.mobile.package_name,
                   'installed_package_sha256': self.mobile.package_sha,
                   'mcp_receipt': self.mobile.receipt, 'action_log': 'actions.jsonl',
                   'replay_status': 'unverified', 'shots': self.shots}
        temporary = self.mobile.stage / 'exploration.tmp'
        temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, self.mobile.stage / 'exploration.json')

    def _sample(self):
        with self.lock:
            revision = self.revision
            png = self.preview
            if (self.finished or self.closed or png is None
                    or self.preview_revision != revision
                    or time.monotonic() - self.last_input < 1.2):
                return
            manual = self.bookmark
            if not manual and self.stable_frames < 2:
                return
            if len(self.shots) >= 100:
                self.warning = '100-screen limit reached; finish this batch.'
                return
            fingerprint = sha256(png).hexdigest()
            if not manual and self.shots and fingerprint == self.shots[-1]['png_sha256']:
                return
        # Slow hierarchy capture never holds the input lock. If navigation occurs
        # concurrently, discard this sample instead of joining different states.
        xml, warning = None, ''
        try:
            xml = self.mobile._hierarchy(timeout=8)
            for node in ET.fromstring(xml).iter('node'):
                title = node.get('text', '').lower().replace('’', "'")
                if (node.get('resource-id') == 'android:id/alertTitle'
                        and ("isn't responding" in title or 'not responding' in title)):
                    warning = 'Android ANR dialog captured; finish and diagnose before replay.'
        except Exception as error:
            xml = None
            warning = f'XML unavailable: {error}'
        with self.lock:
            if self.finished or self.closed or revision != self.revision:
                return
            if self.preview != png:
                xml = None
                warning = 'Screen changed during XML capture; review image and recapture XML if needed.'
            number = len(self.shots) + 1
            stem = f'{number:03d}'
            (self.mobile.stage / f'{stem}.png').write_bytes(png)
            shot = {'number': number, 'png': f'{stem}.png', 'png_sha256': fingerprint,
                    'after_input': revision, 'manual': manual,
                    'captured_at': datetime.now(timezone.utc).isoformat(),
                    'xml': None, 'warning': warning}
            if xml is not None:
                (self.mobile.stage / f'{stem}.xml').write_bytes(xml)
                shot.update(xml=f'{stem}.xml', xml_sha256=sha256(xml).hexdigest())
            self.shots.append(shot)
            self.bookmark = False
            self.warning = warning
            self._manifest()

    def _refresh(self):
        while not self.halt.is_set():
            try:
                with self.lock:
                    revision = self.revision
                png = self.mobile._screen(timeout=5)
                with self.lock:
                    if revision == self.revision:
                        self.stable_frames = self.stable_frames + 1 if png == self.preview else 1
                        self.preview, self.preview_revision = png, revision
            except Exception as error:
                with self.lock:
                    self.warning = str(error)
            if self.halt.wait(0.7):
                break

    def _collect(self):
        failures = 0
        while not self.halt.is_set():
            try:
                self._sample()
                failures = 0
            except Exception as error:
                failures += 1
                with self.lock:
                    self.warning = str(error)
            if self.halt.wait(min(5, 0.7 * (failures + 1))):
                break

    def finish(self):
        with self.lock:
            if not self.finished:
                if self.mobile.stage is None:
                    raise ValueError('no active capture')
                if self.bookmark:
                    raise ValueError('bookmark is still saving; pause briefly before Done')
                if not self.shots:
                    raise ValueError('wait for a screen to save before Done')
                self._manifest()
                os.replace(self.mobile.stage, self.output)
                self.mobile.stage = self.mobile.output = None
                self.finished = True
                self.halt.set()
        self._join_workers()
        with self.lock:
            self.mobile.abort()
        return {'finished': True, 'export_dir': str(self.output), 'saved': len(self.shots),
                'eligible_for_phase': False}

    def _join_workers(self):
        for worker in (self.worker, self.display):
            if worker is not None:
                worker.join(timeout=15)
                if worker.is_alive():
                    raise RuntimeError('capture worker is still stopping; retry finish/stop')

    def start(self):
        self._active()
        if self.mobile.records:
            raise ValueError('start exploration in a fresh capture, without verified checkpoints')
        if self.server is not None:
            return self.url
        recorder = self
        # Keep setup actions performed before the panel, including declared resets.
        self._journal({'status': 'initial', 'actions': list(self.mobile.actions),
                       'fixture_note': 'begin launches existing app data; reset only if explicitly prepared'})
        self._manifest()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _authorized(self):
                return parse_qs(urlparse(self.path).query).get('token', [''])[0] == recorder.token

            def _send(self, code, data, mime='application/json', download_name=None):
                payload = json.dumps(data).encode() if mime == 'application/json' else data
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                if download_name is not None:
                    self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
                self.end_headers()
                self.wfile.write(payload)

            def _saved_files(self):
                folder = recorder.mobile.stage if recorder.mobile.stage is not None else (
                    recorder.output if recorder.finished else None)
                if folder is None:
                    return None, []
                names = {'actions.jsonl', 'exploration.json'}
                for shot in recorder.shots:
                    names.add(shot['png'])
                    if shot['xml']:
                        names.add(shot['xml'])
                root = folder.resolve()
                available = sorted(name for name in names
                                   if name == Path(name).name
                                   and (folder / name).resolve().parent == root
                                   and (folder / name).is_file())
                return folder, available

            def do_GET(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                path = urlparse(self.path).path
                with recorder.lock:
                    if path == '/':
                        return self._send(200, PANEL.replace('__TOKEN__', json.dumps(recorder.token)).encode(),
                                          'text/html; charset=utf-8')
                    if path == '/api/status':
                        return self._send(200, recorder.status())
                    if path == '/api/preview' and recorder.preview:
                        return self._send(200, recorder.preview, 'image/png')
                    if path in ('/api/files', '/api/file'):
                        folder, names = self._saved_files()
                        if folder is None:
                            return self._send(404, {'error': 'capture folder unavailable'})
                        if path == '/api/files':
                            links = ''.join('<li><a href="/api/file?token='
                                            + quote(recorder.token, safe='') + '&amp;name='
                                            + quote(name, safe='') + '">' + escape(name) + '</a></li>'
                                            for name in names)
                            page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                                    '<title>Saved files</title><h1>Saved files</h1><p>'
                                    + escape(str(folder)) + '</p><ul>' + links + '</ul></html>')
                            return self._send(200, page.encode(), 'text/html; charset=utf-8')
                        requested = parse_qs(urlparse(self.path).query).get('name', [])
                        if len(requested) != 1 or requested[0] not in names:
                            return self._send(404, {'error': 'unknown saved file'})
                        name = requested[0]
                        mime = {'.png': 'image/png', '.xml': 'application/xml',
                                '.json': 'application/json', '.jsonl': 'application/x-ndjson'}[Path(name).suffix]
                        return self._send(200, (folder / name).read_bytes(), mime, name)
                self._send(404, {'error': 'screen not ready or unknown route'})

            def do_POST(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise ValueError('invalid request body size')
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError('request must be an object')
                    path = urlparse(self.path).path
                    if path == '/api/action':
                        result = recorder.action(payload)
                    elif path == '/api/save':
                        result = recorder.save()
                    elif path == '/api/finish':
                        result = recorder.finish()
                    else:
                        return self._send(404, {'error': 'unknown route'})
                    self._send(200, result)
                except Exception as error:
                    self._send(400, {'error': str(error)})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker = threading.Thread(target=self._collect, daemon=True)
        self.display = threading.Thread(target=self._refresh, daemon=True)
        self.thread.start()
        self.display.start()
        self.worker.start()
        return self.url

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/?token={self.token}'

    def stop(self):
        with self.lock:
            self.closed = True
            self.bookmark = False
            self.halt.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = self.thread = None
        self._join_workers()
        self.stopped = True
