"""
title: Magazine Simple Sidebar Launcher
author: Nundrian
version: 0.1.0
required_open_webui_version: 0.12.5
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

SCRIPT = "/* Magazine Simple Sidebar Launcher 0.1.0. No tokens, API calls or workflow actions. */\n(() => {\n  'use strict';\n  const key = '__magazineSimpleSidebarLauncher';\n  if (window[key]) { window[key].refresh(); return; }\n  let frame = null;\n  let previousAnchor = null;\n  let wrapper = null;\n  function mount() {\n    frame = null;\n    const sidebar = document.getElementById('sidebar');\n    const anchor = sidebar && (sidebar.querySelector('a[href=\"/workspace\"]') || sidebar.querySelector('a[href=\"/notes\"]') || sidebar.querySelector('#sidebar-new-chat-button') || sidebar.querySelector('a[href=\"/\"]'));\n    if (!anchor) {\n      if (wrapper) wrapper.remove();\n      wrapper = null; previousAnchor = null;\n      return;\n    }\n    if (anchor === previousAnchor && wrapper && wrapper.isConnected) return;\n    if (wrapper) wrapper.remove();\n    previousAnchor = anchor;\n    const link = document.createElement('a');\n    link.id = 'sidebar-magazine-simple-button';\n    link.className = anchor.className;\n    link.href = '/magazine-simple';\n    link.target = '_blank';\n    link.rel = 'noopener noreferrer';\n    link.title = 'Magazine';\n    link.setAttribute('aria-label', 'Magazine');\n    link.setAttribute('draggable', 'false');\n    link.addEventListener('click', event => event.stopPropagation());\n    const iconBox = document.createElement('div');\n    iconBox.className = anchor.firstElementChild ? anchor.firstElementChild.className : 'flex items-center justify-center';\n    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');\n    for (const [name, value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.5','aria-hidden':'true',width:'16',height:'16'})) svg.setAttribute(name,value);\n    const path = document.createElementNS('http://www.w3.org/2000/svg','path');\n    path.setAttribute('d','M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1m0-15c3-2 6-2 9-1v15c-3-1-6-1-9 1m0-15v15');\n    path.setAttribute('stroke-linejoin','round');\n    svg.appendChild(path); iconBox.appendChild(svg); link.appendChild(iconBox);\n    if (anchor.textContent.trim()) {\n      const label = document.createElement('span');\n      label.className = 'text-[0.8125rem] leading-5'; label.textContent = 'Magazine'; link.appendChild(label);\n    }\n    wrapper = document.createElement('div');\n    wrapper.dataset.magazineSimpleLauncher = '0.1.0';\n    wrapper.className = anchor.parentElement.className;\n    wrapper.appendChild(link);\n    anchor.parentElement.insertAdjacentElement('afterend',wrapper);\n  }\n  function refresh() { if (frame === null) frame = requestAnimationFrame(mount); }\n  const observer = new MutationObserver(records => {\n    if (!wrapper || !wrapper.isConnected || records.some(record => record.target instanceof Element && record.target.closest('#sidebar'))) refresh();\n  });\n  function start() { observer.observe(document.body,{childList:true,subtree:true}); refresh(); }\n  window[key] = {refresh,destroy() {\n    observer.disconnect(); if (frame !== null) cancelAnimationFrame(frame);\n    if (wrapper) wrapper.remove(); delete window[key];\n  }};\n  if (document.body) start(); else document.addEventListener('DOMContentLoaded',start,{once:true});\n})();\n"
BEGIN = '// BEGIN MAGAZINE SIMPLE SIDEBAR LAUNCHER 0.1.0\n'
END = '// END MAGAZINE SIMPLE SIDEBAR LAUNCHER 0.1.0\n'

def merged_loader(original, enabled):
    if original.count(BEGIN) != original.count(END) or original.count(BEGIN)>1:
        raise ValueError('Ambiguous Magazine Simple launcher markers; preserve loader and inspect before changing it')
    if BEGIN in original:
        head,rest=original.split(BEGIN,1);_,tail=rest.split(END,1);original=head+tail
    return original + (BEGIN+SCRIPT.rstrip()+'\n'+END if enabled else '')

def publish(enabled):
    from open_webui.config import STATIC_DIR, DATA_DIR
    target=Path(STATIC_DIR)/'loader.js'
    if target.is_symlink():raise ValueError('Refusing symlink loader')
    original=target.read_text(encoding='utf-8') if target.exists() else ''
    updated=merged_loader(original,enabled)
    if updated==original:return
    backup=Path(DATA_DIR)/'magazine-simple/sidebar-launcher'
    backup.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(original.encode()).hexdigest()
    saved=backup/('loader-before-'+digest+'.js')
    if not saved.exists():saved.write_text(original,encoding='utf-8')
    fd,temporary=tempfile.mkstemp(prefix='.magazine-simple-sidebar-',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(updated);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o644)
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    (backup/'installation.json').write_text(json.dumps({'version':'0.1.0','enabled':enabled,
        'loader':str(target),'previous_sha256':digest,'installed_sha256':hashlib.sha256(updated.encode()).hexdigest()},indent=2))

class Event:
    async def event(self,__app__=None,**kwargs):
        if __app__ is None:return
        payload=kwargs.get('event') or {};subject=payload.get('subject') or {}
        own=subject.get('id')==kwargs.get('__id__','magazine_simple_sidebar')
        name=kwargs.get('__event_name__') or payload.get('event')
        if own and name in {'function.disable_started','function.deleted'}:
            publish(False);setattr(__app__.state,'magazine_simple_sidebar_published',False);return
        if not getattr(__app__.state,'magazine_simple_sidebar_published',False):
            publish(True);setattr(__app__.state,'magazine_simple_sidebar_published',True)
