"""Owner-scoped immutable conversation evidence; derived run views are not truth.

Linux container storage uses descriptor-relative no-follow access, flock,
file/directory fsync and exclusive hard-link publication (including NTFS binds).
No snapshot is replaced. A receipt is published last and independently reread.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import secrets
import zipfile

SCHEMA = 'magazine.conversation.v1'
LIMIT = 64 * 1024 * 1024
RUN_LIMIT = 256 * 1024 * 1024


class ArchiveError(ValueError):
    """Archive failure, never an inference failure; details contain no chat text."""


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def key_for(index, cartridge_id):
    if not isinstance(index, int) or not 1 <= index <= 10000 or not isinstance(cartridge_id, str) or not cartridge_id:
        raise ArchiveError('Invalid archive cartridge association.')
    return '%04d-%s' % (index, digest(cartridge_id.encode('utf-8')))


def validate_chat(raw, intent):
    """Validate identity/relationships without projecting away unknown fields."""
    try:
        saved = json.loads(raw)
        chat = saved['chat']
        messages = chat['history']['messages']
        if (saved['id'] != intent['chat_id'] or intent['model_id'] not in chat['models'] or
                (saved.get('user_id') is not None and saved['user_id'] != intent['owner_id']) or
                not isinstance(messages, dict) or not messages):
            raise ValueError()
        user, assistant = messages[intent['user_message_id']], messages[intent['assistant_message_id']]
        parent = assistant.get('parentId')
        recorded_order = [m.get('id') for m in (chat.get('messages') or []) if isinstance(m,dict)]
        linked = (parent == intent['user_message_id'] or
                  intent['assistant_message_id'] in (user.get('childrenIds') or []) or
                  (intent['user_message_id'] in recorded_order and intent['assistant_message_id'] in recorded_order and
                   recorded_order.index(intent['user_message_id']) < recorded_order.index(intent['assistant_message_id'])))
        if (user.get('role') != 'user' or user.get('content') != intent['prompt'] or
                assistant.get('role') != 'assistant' or assistant.get('model') != intent['model_id'] or
                assistant.get('done') is not True or not isinstance(assistant.get('content'), str) or
                not assistant['content'].strip() or (parent is not None and parent != intent['user_message_id']) or not linked):
            raise ValueError()
        # Preserve all branches. Parent relationships establish stable ordering;
        # insertion order breaks ties rather than invented timestamps.
        parents = {mid: {message['parentId']} if message.get('parentId') else set() for mid,message in messages.items()}
        for mid,message in messages.items():
            for child in (message.get('childrenIds') or []):
                if child not in parents: raise ValueError()
                parents[child].add(mid)
        ordered, remaining = [], list(messages)
        while remaining:
            eligible = [mid for mid in remaining if parents[mid].issubset(ordered)]
            if recorded_order:
                eligible.sort(key=lambda mid: recorded_order.index(mid) if mid in recorded_order else len(recorded_order)+list(messages).index(mid))
            if not eligible:
                raise ValueError()
            for mid in eligible:
                message = messages[mid]
                if not isinstance(message, dict) or message.get('id', mid) != mid:
                    raise ValueError()
                ordered.append(mid)
                remaining.remove(mid)
        return saved, [(mid, messages[mid]) for mid in ordered]
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ArchiveError('Saved conversation is incomplete or has mismatched identities.') from None


def jsonl(raw, intent):
    saved, messages = validate_chat(raw, intent)
    metadata = {k:intent[k] for k in ('owner_id','run_id','cartridge_id','index','chat_id','model_id')}
    records = [{'record_type':'cartridge_start','contents':{'completion':'saved_assistant_done'}},
               {'record_type':'saved_chat','contents':saved}]
    for mid, message in messages:
        record = {'record_type':'message','source_message_id':mid,'contents':message}
        if 'timestamp' in message:
            record['source_timestamp'] = message['timestamp']
        records.append(record)
    records.append({'record_type':'cartridge_end','contents':{'message_count':len(messages)}})
    return b''.join(encoded(dict(record, schema=SCHEMA, **metadata, cartridge_sequence=i)) for i,record in enumerate(records,1))


class ArchiveStore:
    def __init__(self, root, owner_id, run_id):
        if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in (owner_id,run_id)):
            raise ArchiveError('Invalid archive owner or run identifier.')
        self.root, self.owner_id, self.run_id = str(root), owner_id, run_id

    @contextlib.contextmanager
    def locked(self, create=True):
        import fcntl
        path = Path(self.root)
        if not path.is_absolute() or '..' in path.parts:
            raise ArchiveError('Archive root must be an absolute contained directory.')
        fd = os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                if create:
                    try: os.mkdir(part,0o700,dir_fd=fd); os.fsync(fd)
                    except FileExistsError: pass
                nxt = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd); fd=nxt
            fcntl.flock(fd,fcntl.LOCK_EX)
            scope={'schema':SCHEMA,'owner_id':self.owner_id,'run_id':self.run_id}
            if create: self.put(fd,'scope.json',encoded(scope))
            elif self.read(fd,'scope.json') != encoded(scope): raise ArchiveError('Archive scope mismatch.')
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def read(fd, name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.','..'):
            raise ArchiveError('Invalid archive filename.')
        handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(handle,'rb') as stream:
            st=os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>LIMIT:
                raise ArchiveError('Archive file is unsafe or exceeds the size limit.')
            data=stream.read(LIMIT+1)
            if len(data)!=st.st_size: raise ArchiveError('Archive file changed while being read.')
            return data

    @classmethod
    def put(cls,fd,name,data,replace=False):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.','..'):
            raise ArchiveError('Invalid archive filename.')
        if len(data)>LIMIT: raise ArchiveError('Archive exceeds the file size limit.')
        if not replace:
            try:
                if cls.read(fd,name)!=data: raise ArchiveError('Conflicting archive evidence; nothing was overwritten.')
                os.fsync(fd)
                return
            except FileNotFoundError: pass
        else:
            try: cls.read(fd,name)
            except FileNotFoundError: pass
        temporary='.archive-'+secrets.token_hex(16)
        try:
            handle=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(handle,'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if replace: os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd)
            else:
                os.link(temporary,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                os.unlink(temporary,dir_fd=fd)
            os.fsync(fd)
            if cls.read(fd,name)!=data: raise ArchiveError('Archive read-back verification failed.')
        finally:
            try: os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError: pass

    def intent(self, association):
        record=dict(association,owner_id=self.owner_id,run_id=self.run_id,schema=SCHEMA)
        key=key_for(record['index'],record['cartridge_id'])
        if (not all(isinstance(record.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',record[k]) for k in ('chat_id','user_message_id','assistant_message_id')) or
                not all(isinstance(record.get(k),str) and record[k] for k in ('model_id','prompt'))):
            raise ArchiveError('Invalid archive source identity.')
        with self.locked() as fd:
            self.put(fd,key+'.intent.json',encoded(record))
        return record

    def status_event(self, intent, stage, status):
        # Fixed messages/status enums only: never persist exception repr or secrets.
        key=key_for(intent['index'],intent['cartridge_id'])
        with self.locked() as fd:
            self.put(fd,key+'.status.json',encoded({'stage':stage,'status':status,'chat_id':intent['chat_id']}),replace=True)

    def verify_at(self,fd,key):
        if not re.fullmatch(r'\d{4,5}-[0-9a-f]{64}',key): raise ArchiveError('Invalid archive key.')
        intent=json.loads(self.read(fd,key+'.intent.json'))
        if intent['owner_id']!=self.owner_id or intent['run_id']!=self.run_id or key_for(intent['index'],intent['cartridge_id'])!=key:
            raise ArchiveError('Archive association mismatch.')
        receipt=json.loads(self.read(fd,key+'.receipt.json'))
        raw=self.read(fd,key+'.original.json'); lines=self.read(fd,key+'.jsonl')
        expected={key+'.original.json':{'sha256':digest(raw),'bytes':len(raw)},key+'.jsonl':{'sha256':digest(lines),'bytes':len(lines)}}
        if receipt!={'schema':SCHEMA,'intent_sha256':digest(encoded(intent)),'files':expected} or lines!=jsonl(raw,intent):
            raise ArchiveError('Archive checksum or structure verification failed.')
        return intent,raw,lines,receipt

    def save(self,intent,raw):
        if intent.get('owner_id')!=self.owner_id or intent.get('run_id')!=self.run_id or intent.get('schema')!=SCHEMA:
            raise ArchiveError('Archive association mismatch.')
        key=key_for(intent['index'],intent['cartridge_id']); lines=jsonl(raw,intent)
        with self.locked() as fd:
            self.put(fd,key+'.intent.json',encoded(intent))
            self.put(fd,key+'.original.json',raw)
            self.put(fd,key+'.jsonl',lines)
            if self.read(fd,key+'.original.json') != raw or self.read(fd,key+'.jsonl') != lines:
                raise ArchiveError('Archive read-back verification failed before receipt publication.')
            receipt={'schema':SCHEMA,'intent_sha256':digest(encoded(intent)),
                     'files':{key+'.original.json':{'sha256':digest(raw),'bytes':len(raw)},key+'.jsonl':{'sha256':digest(lines),'bytes':len(lines)}}}
            self.put(fd,key+'.receipt.json',encoded(receipt))
            self.verify_at(fd,key)
        return key

    def verify(self,key):
        with self.locked(create=False) as fd: return self.verify_at(fd,key)

    def view(self,cartridges,results=None):
        """Derive a deterministic run JSONL/manifest from verified cartridge truth.

        No read/status request creates archives for historical runs. Invalid or
        missing evidence is explicitly PARTIAL, never included as verified data.
        """
        files={}; entries=[]; combined=[]; global_sequence=0
        try:
            with self.locked(create=False) as fd:
                for index,cid in enumerate(cartridges,1):
                    key=key_for(index,cid); entry={'id':cid,'index':index,'status':'MISSING'}
                    try:
                        intent,raw,lines,receipt=self.verify_at(fd,key)
                        entry.update(status='VERIFIED',chat_id=intent['chat_id'],model_id=intent['model_id'])
                        files['original-chats/'+key+'.json']=raw
                        files['cartridges/'+key+'.jsonl']=lines
                        if sum(len(data) for data in files.values()) > RUN_LIMIT:
                            raise ArchiveError('Full run archive exceeds the download size limit.')
                        for line in lines.splitlines():
                            global_sequence+=1; record=json.loads(line);record['run_sequence']=global_sequence;combined.append(encoded(record))
                    except FileNotFoundError:
                        try: entry.update(chat_id=json.loads(self.read(fd,key+'.intent.json'))['chat_id'],status='PENDING')
                        except FileNotFoundError: pass
                    except (ArchiveError,OSError,ValueError,KeyError): entry['status']='FAILED'
                    try: entry['cleanup']=json.loads(self.read(fd,key+'.status.json'))
                    except FileNotFoundError: pass
                    if entry['status'] != 'VERIFIED' and entry.get('cleanup',{}).get('status') == 'FAILED':
                        entry['status']='FAILED'
                    entries.append(entry)
        except FileNotFoundError:
            entries=[{'id':cid,'index':i,'status':'MISSING'} for i,cid in enumerate(cartridges,1)]
        files['run.jsonl']=b''.join(combined)
        # Result filenames use the original cartridge positions, not the
        # insertion order of a partial/done-only result dictionary.
        positions={cid:i for i,cid in enumerate(cartridges,1)}
        for cid,text in (results or {}).items():
            if cid not in positions:
                raise ArchiveError('Result cartridge is not part of this run.')
            if text is not None:
                files['results/'+key_for(positions[cid],cid)+'.txt']=text.encode('utf-8')
        if sum(len(data) for data in files.values()) > RUN_LIMIT:
            raise ArchiveError('Full run archive exceeds the download size limit.')
        manifest={'schema':SCHEMA,'owner_id':self.owner_id,'run_id':self.run_id,
                  'status':'COMPLETE' if entries and all(e['status']=='VERIFIED' for e in entries) else 'PARTIAL',
                  'cartridges':entries,'files':{name:{'sha256':digest(data),'bytes':len(data)} for name,data in sorted(files.items())}}
        files['manifest.json']=encoded(manifest)
        return manifest,files

    def pending_intents(self,cartridges):
        intents=[]
        try:
            with self.locked(create=False) as fd:
                for index,cid in enumerate(cartridges,1):
                    key=key_for(index,cid)
                    try:
                        value=json.loads(self.read(fd,key+'.intent.json'))
                        if (value['owner_id']!=self.owner_id or value['run_id']!=self.run_id or
                                value['index']!=index or value['cartridge_id']!=cid or
                                not all(isinstance(value.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value[k]) for k in ('chat_id','user_message_id','assistant_message_id'))):
                            raise ArchiveError('Archive scope mismatch.')
                        intents.append(value)
                    except FileNotFoundError: pass
        except FileNotFoundError: pass
        return intents

    def materialize(self,cartridges):
        manifest,files=self.view(cartridges)
        with self.locked() as fd:
            self.put(fd,'run.jsonl',files['run.jsonl'],replace=True)
            self.put(fd,'manifest.json',encoded(manifest),replace=True)
        return manifest

    def inspect(self,cartridges):
        """Verify receipts for status without assembling a combined stream/ZIP."""
        entries=[]
        try:
            with self.locked(create=False) as fd:
                for index,cid in enumerate(cartridges,1):
                    key=key_for(index,cid);entry={'id':cid,'index':index,'status':'MISSING'}
                    try:
                        intent,_,_,_=self.verify_at(fd,key)
                        entry.update(status='VERIFIED',chat_id=intent['chat_id'],model_id=intent['model_id'])
                    except FileNotFoundError:
                        try:entry.update(status='PENDING',chat_id=json.loads(self.read(fd,key+'.intent.json'))['chat_id'])
                        except FileNotFoundError:pass
                    except (ArchiveError,OSError,ValueError,KeyError):entry['status']='FAILED'
                    try:entry['cleanup']=json.loads(self.read(fd,key+'.status.json'))
                    except FileNotFoundError:pass
                    if entry['status']!='VERIFIED' and entry.get('cleanup',{}).get('status')=='FAILED':entry['status']='FAILED'
                    entries.append(entry)
        except FileNotFoundError:
            entries=[{'id':cid,'index':i,'status':'MISSING'} for i,cid in enumerate(cartridges,1)]
        return {'status':'COMPLETE' if entries and all(e['status']=='VERIFIED' for e in entries) else 'PARTIAL','cartridges':entries}


def zip_files(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.external_attr=0o100600<<16
            archive.writestr(info,data)
    return buffer.getvalue()
