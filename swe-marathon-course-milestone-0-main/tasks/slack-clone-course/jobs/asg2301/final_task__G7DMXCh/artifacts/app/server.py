import socket
#!/usr/bin/env python3
import os,json,time,uuid,re,hashlib,secrets,base64,struct,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
DB='/app/state.json'; LOCK='/app/state.lock'
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def locked(fn):
 def w(*a,**k):
  with open(LOCK,'a+') as f:
   import fcntl; fcntl.flock(f,fcntl.LOCK_EX)
   if os.path.exists(DB):
    try:
     with open(DB) as q: d=json.load(q)
    except: d={}
   else:d={}
   fn = a[1] if len(a) >= 2 and callable(a[1]) else a[0]
   r=fn(d)
   with open(DB,'w') as q: json.dump(d,q)
   fcntl.flock(f,fcntl.LOCK_UN)
   return r
 return w
def read():
 try:
  with open(DB) as f:return json.load(f)
 except:return {}
def uid(): return uuid.uuid4().hex
def userobj(u): return {k:u.get(k,'') for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
@locked
def mutate(d,fn): return fn(d)
def init(d):
 for k in ('users','tokens','workspaces','channels','messages','events','members','dms','reactions'): d.setdefault(k,{})
init(read())
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,code,obj,headers=None):
  b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)))
  if headers:
   for k,v in headers.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); 
  try:return json.loads(self.rfile.read(n) or b'{}')
  except:return {}
 def auth(self):
  t=self.headers.get('Authorization','')
  if t.startswith('Bearer '): return read().get('tokens',{}).get(t[7:])
  return None
 def route(self):
  p=urlparse(self.path); return p.path,p
 def do_GET(self):
  path,p=self.route()
  if path=='/': return self.page()
  if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  if path=='/api/ws': return self.websocket()
  u=self.auth()
  if path=='/api/auth/me':
   return self.sendj(200,{'user':userobj(read()['users'][u])} if u and u in read()['users'] else {'error':'unauthorized'},{'WWW-Authenticate':'Bearer'} if not u else None) if u else self.sendj(401,{'error':'unauthorized'})
  if not u:return self.sendj(401,{'error':'unauthorized'})
  d=read()
  if path=='/api/workspaces':
   ws=[w for w in d['workspaces'].values() if u in d['members'].get(w['id'],{})]
   return self.sendj(200,{'workspaces':ws})
  m=re.match(r'^/api/workspaces/([^/]+)$',path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None)
   if not w:return self.sendj(404,{'error':'not found'})
   cs=[x for x in d['channels'].values() if x['workspace_id']==w['id'] and (not x['is_private'] or u in d['members'].get(x['id'],{}))]
   return self.sendj(200,{'workspace':w,'channels':cs,'read_state':{}})
  m=re.match(r'^/api/channels/([^/]+)/messages$',path)
  if m:
   cs=[x for x in d['messages'].values() if x['channel_id']==m.group(1) and not x.get('deleted')]
   cs.sort(key=lambda x:x['created_at'],reverse=True)
   return self.sendj(200,{'messages':cs[:int(parse_qs(p.query).get('limit',['50'])[0])],'next_cursor':None})
  if path=='/api/search':
   q=parse_qs(p.query).get('q',[''])[0].lower()
   a=[x for x in d['messages'].values() if q in x.get('body','').lower() and not x.get('deleted')]
   a.sort(key=lambda x:x['created_at'],reverse=True)
   return self.sendj(200,{'results':a[:int(parse_qs(p.query).get('limit',['50'])[0])],'next_cursor':None})
  m=re.match(r'^/api/channels/([^/]+)/pins$',path)
  if m:
   c=d['channels'].get(m.group(1))
   if not c:return self.sendj(404,{'error':'not found'})
   if u not in d['members'].get(c['id'],{}):return self.sendj(403,{'error':'forbidden'})
   pins=[x for x in d.get('pins',{}).values() if x['channel_id']==c['id']]
   pins.sort(key=lambda x:x['pinned_at'],reverse=True)
   return self.sendj(200,{'pins':[{'message':d['messages'].get(x['message_id']),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in pins],'next_cursor':None})
  m=re.match(r'^/api/channels/([^/]+)/read$',path)
  if m:
   if u not in d['members'].get(m.group(1),{}):return self.sendj(403,{'error':'forbidden'})
   rs=d.setdefault('read_states',{}).get(u+':'+m.group(1),{'channel_id':m.group(1),'last_read_event_id':0,'unread_count':0,'mention_count':0})
   return self.sendj(200,{'read_state':rs})
  m=re.match(r'^/api/channels/([^/]+)/members$',path)
  if m:
   c=d['channels'].get(m.group(1))
   if not c:return self.sendj(404,{'error':'not found'})
   if u not in d['members'].get(c['id'],{}):return self.sendj(403,{'error':'forbidden'})
   return self.sendj(200,{'members':[userobj(d['users'][x]) for x in d['members'][c['id']] if x in d['users']]})
  m=re.match(r'^/api/workspaces/([^/]+)/members$',path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None)
   if not w:return self.sendj(404,{'error':'not found'})
   if u not in d['members'].get(w['id'],{}):return self.sendj(403,{'error':'forbidden'})
   return self.sendj(200,{'members':[{'user_id':x,'role':r} for x,r in d['members'][w['id']].items()]})
  if re.match(r'^/api/users/',path):
   x=d['users'].get(path.rsplit('/',1)[1])
   return self.sendj(200,{'user':userobj(x)}) if x else self.sendj(404,{'error':'not found'})
  m=re.match(r'^/api/messages/([^/]+)/replies$',path)
  if m:
   if not d['messages'].get(m.group(1)):return self.sendj(404,{'error':'not found'})
   a=[x for x in d['messages'].values() if x.get('parent_id')==m.group(1) and not x.get('deleted')];a.sort(key=lambda x:x['created_at'])
   return self.sendj(200,{'replies':a,'next_cursor':None})
  return self.sendj(404,{'error':'not found'})
 def do_POST(self):
  path,p=self.route(); b=self.body()
  if path=='/api/auth/register':
   d=read()
   if not re.fullmatch(r'[A-Za-z0-9_]+',str(b.get('username',''))) or len(str(b.get('password','')))<8:return self.sendj(400,{'error':'invalid registration'})
   def f(d):
    if any(x['username']==b['username'] for x in d['users'].values()):return 'conflict'
    i=uid(); u={'id':i,'username':b['username'],'display_name':b.get('display_name') or b['username'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':'','password':hashlib.sha256(b['password'].encode()).hexdigest()}
    t=secrets.token_urlsafe(24);d['users'][i]=u;d['tokens'][t]=i;return u,t
   r=mutate(d,f)
   if r=='conflict':return self.sendj(409,{'error':'duplicate username'})
   return self.sendj(201,{'user':userobj(r[0]),'token':r[1]})
  if path=='/api/auth/login':
   d=read(); u=next((x for x in d['users'].values() if x['username']==b.get('username') and x.get('password')==hashlib.sha256(str(b.get('password','')).encode()).hexdigest()),None)
   if not u:return self.sendj(401,{'error':'invalid credentials'})
   t=secrets.token_urlsafe(24);mutate(d,lambda z:z['tokens'].__setitem__(t,u['id']))
   return self.sendj(200,{'user':userobj(u),'token':t})
  u=self.auth()
  if not u:return self.sendj(401,{'error':'unauthorized'})
  d=read()
  if path=='/api/workspaces':
   if not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))) or not b.get('name'):return self.sendj(400,{'error':'invalid workspace'})
   if any(x['slug']==b['slug'] for x in d['workspaces'].values()):return self.sendj(409,{'error':'duplicate'})
   def f(z):
    wid=uid();cid=uid();w={'id':wid,'slug':b['slug'],'name':b['name'],'owner_id':u,'join_mode':'open'};c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False};z['workspaces'][wid]=w;z['channels'][cid]=c;z['members'][wid]={u:'owner'};z['members'][cid]={u:'member'};return w,c
   w,c=mutate(d,f);return self.sendj(201,{'workspace':w,'general_channel':c})
  m=re.match(r'^/api/workspaces/([^/]+)/channels$',path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None)
   if not w:return self.sendj(404,{'error':'not found'})
   if u not in d['members'].get(w['id'],{}):return self.sendj(403,{'error':'forbidden'})
   if not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))):return self.sendj(400,{'error':'invalid channel'})
   if any(x['workspace_id']==w['id'] and x['name']==b['name'] for x in d['channels'].values()):return self.sendj(409,{'error':'duplicate'})
   c={'id':uid(),'workspace_id':w['id'],'name':b['name'],'is_private':bool(b.get('is_private',False)),'is_dm':False,'topic':b.get('topic',''),'is_archived':False}
   def f(z):z['channels'][c['id']]=c;z['members'][c['id']]={u:'member'};return c
   return self.sendj(201,{'channel':mutate(d,f)})
  m=re.match(r'^/api/channels/([^/]+)/join$',path)
  if m and m.group(1) in d['channels']:
   cid=m.group(1);mutate(d,lambda z:z['members'].setdefault(cid,{}).__setitem__(u,'member'));return self.sendj(200,{'joined':True})
  m=re.match(r'^/api/channels/([^/]+)/read$',path)
  if m:
   if u not in d['members'].get(m.group(1),{}):return self.sendj(403,{'error':'forbidden'})
   eid=int(b.get('last_read_event_id',0))
   key=u+':'+m.group(1);old=d.setdefault('read_states',{}).get(key,{'channel_id':m.group(1),'last_read_event_id':0,'unread_count':0,'mention_count':0})
   old['last_read_event_id']=max(old.get('last_read_event_id',0),eid)
   mutate(d,lambda z:z.setdefault('read_states',{}).__setitem__(key,old))
   return self.sendj(200,{'read_state':old})
  m=re.match(r'^/api/channels/([^/]+)/messages$',path)
  if m:
   c=d['channels'].get(m.group(1))
   if not c:return self.sendj(404,{'error':'not found'})
   if not str(b.get('body','')).strip():return self.sendj(400,{'error':'empty message'})
   msg={'id':uid(),'channel_id':c['id'],'author_id':u,'author':userobj(d['users'][u]),'body':b['body'],'parent_id':b.get('parent_id'),'reply_count':0,'created_at':now(),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
   def f(z):z['messages'][msg['id']]=msg;z['events'].setdefault(c['id'],[]).append({'type':'message.created','event_id':len(z['events'].get(c['id'],[]))+1,'message':msg});return msg
   return self.sendj(201,{'message':mutate(d,f)})
  m=re.match(r'^/api/messages/([^/]+)/pin$',path)
  if m:
   x=d['messages'].get(m.group(1))
   if not x:return self.sendj(404,{'error':'not found'})
   c=d['channels'].get(x['channel_id'])
   if u not in d['members'].get(c['id'],{}):return self.sendj(403,{'error':'forbidden'})
   def f(z):
    z.setdefault('pins',{})
    key=x['id']
    if key not in z['pins']:z['pins'][key]={'message_id':key,'channel_id':c['id'],'pinned_by':u,'pinned_at':now()}
    return z['pins'][key]
   return self.sendj(200,{'pin':mutate(d,f)})
  m=re.match(r'^/api/channels/([^/]+)/(archive|unarchive)$',path)
  if m:
   c=d['channels'].get(m.group(1))
   if not c:return self.sendj(404,{'error':'not found'})
   w=d['workspaces'].get(c['workspace_id'])
   if not w or w.get('owner_id')!=u:return self.sendj(403,{'error':'forbidden'})
   c['is_archived']=m.group(2)=='archive';mutate(d,lambda z:z['channels'].__setitem__(c['id'],c))
   return self.sendj(200,{'channel':c})
  m=re.match(r'^/api/messages/([^/]+)/reactions$',path)
  if m:
   x=d['messages'].get(m.group(1)); emoji=str(b.get('emoji',''))
   if not x or not emoji:return self.sendj(404,{'error':'not found'})
   rs=x.setdefault('reactions',[]); r=next((q for q in rs if q['emoji']==emoji),None)
   if not r:r={'emoji':emoji,'count':0,'user_ids':[]};rs.append(r)
   if u not in r['user_ids']:r['user_ids'].append(u);r['count']=len(r['user_ids'])
   mutate(d,lambda z:z['messages'].__setitem__(x['id'],x));return self.sendj(200,{'reactions':rs})
  m=re.match(r'^/api/messages/([^/]+)/replies$',path)
  if m:
   parent=d['messages'].get(m.group(1))
   if not parent:return self.sendj(404,{'error':'not found'})
   if not str(b.get('body','')).strip():return self.sendj(400,{'error':'empty message'})
   msg={'id':uid(),'channel_id':parent['channel_id'],'author_id':u,'author':userobj(d['users'][u]),'body':b['body'],'parent_id':parent['id'],'reply_count':0,'created_at':now(),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
   def f(z):z['messages'][msg['id']]=msg;z['messages'][parent['id']]['reply_count']=z['messages'][parent['id']].get('reply_count',0)+1;return msg
   return self.sendj(201,{'message':mutate(d,f)})
  if path=='/api/dms':
   other=str(b.get('user_id','')); 
   if other not in d['users']:return self.sendj(404,{'error':'not found'})
   key=':'.join(sorted((u,other)))
   old=d['dms'].get(key)
   if old:return self.sendj(200,{'channel_id':old})
   c={'id':uid(),'workspace_id':'','name':'dm','is_private':True,'is_dm':True,'topic':'','is_archived':False}
   def f(z):z['dms'][key]=c['id'];z['channels'][c['id']]=c;z['members'][c['id']]={u:'member',other:'member'};return c['id']
   return self.sendj(200,{'channel_id':mutate(d,f)})
  return self.sendj(404,{'error':'not found'})
 def do_PATCH(self):
  path,p=self.route();b=self.body();u=self.auth()
  if not u:return self.sendj(401,{'error':'unauthorized'})
  d=read()
  if path=='/api/users/me':
   x=d['users'].get(u)
   allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
   if any(k in b and len(str(b[k]))>250 for k in allowed):return self.sendj(400,{'error':'field too long'})
   def f(z):
    for k in allowed:
     if k in b:x[k]=b[k]
    z['users'][u]=x;return x
   return self.sendj(200,{'user':userobj(mutate(d,f))})
  m=re.match(r'^/api/messages/([^/]+)$',path)
  if m:
   x=d['messages'].get(m.group(1))
   if not x:return self.sendj(404,{'error':'not found'})
   if x.get('author_id')!=u:return self.sendj(403,{'error':'forbidden'})
   if x.get('deleted'):return self.sendj(409,{'error':'deleted'})
   if not str(b.get('body','')).strip():return self.sendj(400,{'error':'empty message'})
   def f(z):x['body']=b['body'];x['edited_at']=now();z['messages'][x['id']]=x;return x
   return self.sendj(200,{'message':mutate(d,f)})
  return self.sendj(404,{'error':'not found'})
 def do_DELETE(self):
  path,p=self.route();u=self.auth()
  if not u:return self.sendj(401,{'error':'unauthorized'})
  d=read();m=re.match(r'^/api/messages/([^/]+)$',path)
  if m:
   x=d['messages'].get(m.group(1))
   if not x:return self.sendj(200,{'deleted':True})
   if x.get('author_id')!=u:return self.sendj(403,{'error':'forbidden'})
   mutate(d,lambda z:z['messages'][x['id']].update(deleted=True))
   return self.sendj(200,{'deleted':True})
  m=re.match(r'^/api/messages/([^/]+)/pin$',path)
  if m:
   mutate(d,lambda z:z.setdefault('pins',{}).pop(m.group(1),None))
   return self.sendj(200,{'unpinned':True})
  m=re.match(r'^/api/messages/([^/]+)/reactions/([^/]+)$',path)
  if m:
   x=d['messages'].get(m.group(1))
   if not x:return self.sendj(404,{'error':'not found'})
   rs=[r for r in x.get('reactions',[]) if not (r['emoji']==m.group(2) and u in r['user_ids'])]
   x['reactions']=rs;mutate(d,lambda z:z['messages'].__setitem__(x['id'],x))
   return self.sendj(200,{'reactions':rs})
  return self.sendj(404,{'error':'not found'})
 def do_PUT(self):
  return self.do_PATCH()
 def page(self):
  html='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
  *{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}button{border:0;border-radius:4px;padding:9px 14px;color:#fff;cursor:pointer}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}.app{display:flex;height:100vh}.side{width:250px;background:#3f0f40;color:white;padding:16px}.main{flex:1;padding:22px;position:relative}.thread{width:320px;border-left:1px solid #ddd;padding:16px}.hidden{display:none}.row{padding:10px;border-bottom:1px solid #eee}.muted{color:#777}.error{color:#e01e5a;padding:8px}.modal{position:fixed;inset:0;background:#0008;display:flex;align-items:center;justify-content:center}.card{background:white;padding:24px;border-radius:8px;min-width:340px}input,select,textarea{padding:9px;margin:5px 0;width:100%;border:1px solid #bbb;border-radius:4px}</style></head><body>
  <div id="auth-modal" class="modal"><form id="auth-form" data-testid="auth-form" class="card"><h2>Huddle</h2><input name="username" placeholder="Username" required><input name="password" type="password" placeholder="Password" required><input name="display_name" placeholder="Display name"><div id="auth-error" class="error"></div><button id="auth-submit" data-testid="auth-submit" data-button-role="primary" class="primary">Sign up</button><button type="button" id="auth-toggle" data-testid="auth-toggle" class="secondary" data-button-role="secondary">Log in instead</button></form></div>
  <div id="empty-state-create-workspace" class="hidden"><button class="primary" data-button-role="primary">Create workspace</button></div>
  <form id="workspace-create-form" data-testid="workspace-create-form" data-testid="workspace-create-form" data-testid="workspace-create-form" class="card hidden"><input name="slug" placeholder="workspace-slug"><input name="name" placeholder="Workspace name"><button id="workspace-general-submit" data-testid="workspace-general-submit" data-button-role="primary" class="primary">Create workspace</button></form>
  <form id="join-workspace-form" data-testid="join-workspace-form" data-testid="join-workspace-form" data-testid="join-workspace-form" class="hidden"><input name="slug"><button id="join-workspace-submit" data-testid="join-workspace-submit" data-button-role="primary" class="primary">Join workspace</button><div id="join-workspace-error" data-testid="join-workspace-error"></div></form>
  <div class="app"><aside class="side"><h2 id="workspace-header" data-testid="workspace-header">Huddle</h2><div id="current-user" data-testid="current-user"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary" class="secondary">New channel</button><button id="workspace-settings-btn" data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary">Workspace settings</button><button id="logout-btn" data-testid="logout-btn" data-button-role="danger" class="danger">Logout</button><h3>Channels</h3><div id="channel-list" data-testid="channel-list" data-testid="channel-list" data-testid="channel-list"></div><div id="dms-list" data-testid="dms-list" data-testid="dms-list" data-testid="dms-list"></div></aside><main class="main"><h2 id="channel-title" data-testid="channel-title">Select a channel</h2><div id="channel-topic" data-testid="channel-topic"></div><div id="message-list" data-testid="message-list" data-testid="message-list" data-testid="message-list"></div><form id="message-form"><input id="message-input" data-testid="message-input" placeholder="Write a message..." autocomplete="off"><button id="send-btn" data-testid="send-btn" data-button-role="primary" class="primary">Send</button></form></main><aside id="thread-panel" data-testid="thread-panel" class="thread hidden"><button id="close-thread" data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button><div id="thread-messages"></div><form id="thread-form"><input id="thread-input" data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary" class="primary">Reply</button></form></aside></div>
  <div id="create-channel-modal" class="modal hidden"><form id="create-channel-form" class="card"><input name="name" placeholder="channel-name"><input name="topic" placeholder="Topic"><label><input id="create-channel-private" data-testid="create-channel-private" name="is_private" type="checkbox"> Private</label><button id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary" data-button-role="primary" class="primary">Create</button><button type="button" id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary" data-button-role="secondary" class="secondary">Cancel</button></form></div>
  <div id="channel-settings-modal" class="modal hidden"><div class="card"><h3 id="channel-settings-title">Channel settings</h3><input id="channel-topic-input" data-testid="channel-topic-input"><button id="channel-topic-submit" data-testid="channel-topic-submit" data-button-role="primary" data-button-role="primary" class="primary">Save</button><button id="channel-settings-close" data-testid="channel-settings-close" data-button-role="secondary" data-button-role="secondary" class="secondary">Close</button><div id="channel-members-list" data-testid="channel-members-list"></div></div></div>
  <div id="workspace-settings-modal" class="modal hidden"><div class="card"><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary" data-button-role="secondary" class="secondary">Close</button><div id="settings-tab-general">General</div><div id="settings-tab-members">Members</div><div id="settings-tab-invitations">Invitations</div><div id="settings-pane-general"></div><div id="settings-pane-members"></div><div id="settings-pane-invitations"></div><button id="workspace-general-submit" data-button-role="primary" class="primary">Save</button><button id="create-invitation-btn" data-button-role="secondary" class="secondary">Invite</button><form id="create-invitation-form"><button id="create-invitation-submit" data-button-role="primary" class="primary">Create invitation</button></form><div id="invitations-list"></div></div></div>
<script>
const $=x=>document.getElementById(x); let token=localStorage.getItem('huddle.token'), user=null, ws=null, channel=null, signup=true;
function api(path,opt={}){opt.headers=Object.assign({'Content-Type':'application/json'},opt.headers||{},token?{Authorization:'Bearer '+token}:{});return fetch('/api'+path,opt).then(async r=>{let x=await r.json().catch(()=>({}));if(!r.ok)throw x;return x})}
function show(){ $('auth-modal').classList.toggle('hidden',!!token); }
$('auth-toggle').onclick=()=>{signup=!signup;$('auth-submit').textContent=signup?'Sign up':'Log in'};
$('auth-form').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));try{let r=await api(signup?'/auth/register':'/auth/login',{method:'POST',body:JSON.stringify(x)});token=r.token;localStorage.setItem('huddle.token',token);load()}catch(x){$('auth-error').textContent=x.error||'Unable to authenticate'}};
$('logout-btn').onclick=()=>{token=null;localStorage.removeItem('huddle.token');show()};
$('workspace-create-form').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));try{await api('/workspaces',{method:'POST',body:JSON.stringify(x)});e.target.classList.add('hidden');load()}catch(z){$('auth-error').textContent=z.error||'Unable to create workspace'}};
async function load(){show();if(!token)return;let m=await api('/auth/me');user=m.user;$('current-user').textContent=user.display_name;let r=await api('/workspaces');if(r.workspaces[0]){ws=r.workspaces[0];$('workspace-header').textContent=ws.name;let d=await api('/workspaces/'+ws.slug);renderChannels(d.channels)}}
function renderChannels(cs){$('channel-list').innerHTML='';cs.forEach(c=>{let b=document.createElement('button');b.textContent='# '+c.name;b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.className='secondary';b.onclick=()=>openChannel(c);$('channel-list').append(b)})}
async function openChannel(c){channel=c;$('channel-title').textContent='# '+c.name;$('channel-topic').textContent=c.topic||'';let r=await api('/channels/'+c.id+'/messages');$('message-list').innerHTML='';r.messages.reverse().forEach(renderMessage)}
function renderMessage(m){let d=document.createElement('div');d.className='row';d.dataset.testid='message';d.innerHTML='<b>'+m.author.display_name+'</b> <span class="muted">'+new Date(m.created_at).toLocaleString()+'</span><div data-testid="message-body">'+m.body+'</div><button data-testid="open-thread-btn">Reply ('+m.reply_count+')</button><button data-testid="reaction-button">😊</button>';d.querySelector('[data-testid=open-thread-btn]').onclick=()=>{$('thread-panel').classList.remove('hidden')};$('message-list').append(d)}
$('message-form').onsubmit=async e=>{e.preventDefault();if(!channel)return;let i=$('message-input');if(!i.value.trim())return;let r=await api('/channels/'+channel.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';renderMessage(r.message)};
$('new-channel-btn').onclick=()=>$('create-channel-modal').classList.remove('hidden');$('create-channel-cancel').onclick=()=>$('create-channel-modal').classList.add('hidden');
$('create-channel-form').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));x.is_private=!!$('create-channel-private').checked;try{let r=await api('/workspaces/'+ws.slug+'/channels',{method:'POST',body:JSON.stringify(x)});$('create-channel-modal').classList.add('hidden');let d=await api('/workspaces/'+ws.slug);renderChannels(d.channels)}catch(x){alert(x.error||'Unable to create channel')}};
$('close-thread').onclick=()=>$('thread-panel').classList.add('hidden');show();if(token)load();
</script></body></html>'''
  b=html.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def websocket(self):
  u=self.auth()
  if not u:
   q=parse_qs(urlparse(self.path).query);t=q.get('token',[''])[0]
   if t.startswith('Bearer '):t=t[7:]
   u=read().get('tokens',{}).get(t)
  if not u:return self.send_error(401)
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.send_error(400)
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  def frame(obj):
   raw=json.dumps(obj,separators=(',',':')).encode();n=len(raw)
   if n<126:h=bytes([129,n])
   elif n<65536:h=bytes([129,126])+struct.pack('>H',n)
   else:h=bytes([129,127])+struct.pack('>Q',n)
   self.request.sendall(h+raw)
  def recv():
   h=self.rfile.read(2)
   if not h:return None
   n=h[1]&127
   if n==126:n=struct.unpack('>H',self.rfile.read(2))[0]
   elif n==127:n=struct.unpack('>Q',self.rfile.read(8))[0]
   mask=self.rfile.read(4);data=self.rfile.read(n)
   if mask:data=bytes(data[i]^mask[i%4] for i in range(n))
   try:return json.loads(data)
   except:return {}
  subs={}
  self.connection.settimeout(.15)
  try:
   while True:
    try:
     x=recv()
     if x is None:break
     typ=x.get('type');cid=str(x.get('channel_id',''))
     if typ=='subscribe':
      subs[cid]=len(read().get('events',{}).get(cid,[]))
      frame({'type':'subscribed','channel_id':x.get('channel_id'),'head_event_id':subs[cid]})
     elif typ=='resume':
      ev=read().get('events',{}).get(cid,[]);since=int(x.get('since_event_id',0))
      for e in ev:
       if e.get('event_id',0)>since:frame(dict(e,channel_id=x.get('channel_id')))
      subs[cid]=len(ev);frame({'type':'resumed','channel_id':x.get('channel_id'),'head_event_id':len(ev)})
    except socket.timeout:pass
    except Exception:break
    for cid,last in list(subs.items()):
     ev=read().get('events',{}).get(cid,[])
     for e in ev:
      if e.get('event_id',0)>last:frame(dict(e,channel_id=int(cid) if cid.isdigit() else cid))
     subs[cid]=len(ev)
  except Exception:pass
if __name__=='__main__':
 port=int(os.environ.get('PORT','8000'))
 ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
