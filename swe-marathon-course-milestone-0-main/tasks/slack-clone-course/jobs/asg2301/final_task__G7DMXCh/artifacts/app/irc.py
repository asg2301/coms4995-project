import socket,threading,json,os,re,uuid,hashlib
from datetime import datetime,timezone
DB='/app/state.json';LOCK='/app/state.lock'
def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 try:
  with open(DB) as f:return json.load(f)
 except:return {}
def save(d):
 import fcntl
 with open(LOCK,'a+') as l:
  fcntl.flock(l,fcntl.LOCK_EX)
  with open(DB,'w') as f:json.dump(d,f)
  fcntl.flock(l,fcntl.LOCK_UN)
def send(c,s):c.sendall((s+'\r\n').encode())
def findchan(d,name):
 if not name.startswith('#') or '/' not in name:return None
 slug,cn=name[1:].split('/',1)
 for w in d.get('workspaces',{}).values():
  if w['slug']==slug:
   for c in d.get('channels',{}).values():
    if c['workspace_id']==w['id'] and c['name']==cn:return c
 return None
def client(c,addr):
 token=None;nick=None;user=None;joined={}
 try:
  f=c.makefile('r',encoding='utf8',errors='replace')
  for line in f:
   z=line.rstrip('\r\n'); parts=z.split(' ',2); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''; rest=parts[2] if len(parts)>2 else ''
   if cmd=='PASS':
    token=arg
    if token.startswith(':'):token=token[1:]
    d=db(); user=d.get('tokens',{}).get(token)
    if not user:send(c,':huddle 464 * :Password incorrect')
   elif cmd=='NICK':
    n=arg.lstrip(':')
    if nick and n!=nick:send(c,':huddle 433 * '+n+' :Nickname is already in use')
    else:
     d=db()
     if any(x.get('irc_nick')==n for x in d.get('users',{}).values()):send(c,':huddle 433 * '+n+' :Nickname is already in use')
     else:nick=n
   elif cmd=='USER':
    if not user:continue
    if nick:
     send(c,':huddle 001 '+nick+' :Welcome to Huddle IRC')
     for n,msg in [('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1'),('005','CHANTYPES=# PREFIX=(ov)@+'),('422','MOTD File is missing')]:send(c,':huddle '+n+' '+nick+' :'+msg)
   elif cmd=='PING':send(c,':huddle PONG huddle :'+(rest.lstrip(':') or arg))
   elif cmd=='JOIN':
    ch=findchan(db(),arg)
    if not ch:send(c,':huddle 403 '+(nick or '*')+' '+arg+' :No such channel');continue
    joined[ch['id']]=ch
    send(c,':'+(nick or '*')+'!'+str(user)+'@localhost JOIN '+arg)
    d=db();names=[]
    for uid in d.get('members',{}).get(ch['id'],{}):
     if uid in d.get('users',{}):names.append(d['users'][uid]['username'])
    send(c,':huddle 353 '+nick+' = '+arg+' :'+(' '.join(names)))
    send(c,':huddle 366 '+nick+' '+arg+' :End of NAMES list')
    send(c,':huddle 331 '+nick+' '+arg+' :No topic is set')
   elif cmd=='NAMES':
    ch=findchan(db(),arg)
    if ch:
     d=db();names=[d['users'][x]['username'] for x in d.get('members',{}).get(ch['id'],{}) if x in d.get('users',{})]
     send(c,':huddle 353 '+(nick or '*')+' = '+arg+' :'+(' '.join(names)));send(c,':huddle 366 '+(nick or '*')+' '+arg+' :End of NAMES list')
    else:send(c,':huddle 403 '+(nick or '*')+' '+arg+' :No such channel')
   elif cmd=='PRIVMSG':
    body=rest.lstrip(':');ch=findchan(db(),arg)
    if ch and user:
     d=db();u=d['users'][user];m={'id':uuid.uuid4().hex,'channel_id':ch['id'],'author_id':user,'author':{k:u.get(k,'') for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')},'body':body,'parent_id':None,'reply_count':0,'created_at':now(),'edited_at':None,'files':[],'reactions':[],'mentions':[]}
     d['messages'][m['id']]=m;ev=d.setdefault('events',{}).setdefault(ch['id'],[]);ev.append({'type':'message.created','event_id':len(ev)+1,'message':m});save(d)
   elif cmd=='QUIT':break
   elif cmd and cmd not in ('USER','MODE','TOPIC','WHO','LIST'):send(c,':huddle 421 '+(nick or '*')+' '+cmd+' :Unknown command')
 except Exception:pass
 try:c.close()
 except:pass
def main():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen(100)
 while 1:
  c,a=s.accept();threading.Thread(target=client,args=(c,a),daemon=True).start()
if __name__=='__main__':main()
