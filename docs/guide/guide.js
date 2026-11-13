/* Only local illustration controls; no Open WebUI calls or destructive actions. */
(function(){
  'use strict';
  const byId=id=>document.getElementById(id);
  const mode=byId('model-mode');
  const steps=byId('per-step');
  const common=byId('same-model');
  const feedback=byId('demo-feedback');
  if(mode&&steps&&common){
    mode.addEventListener('change',()=>{
      const one=mode.value==='same';
      common.hidden=!one;
      steps.hidden=one;
      if(feedback)feedback.textContent=one?'Illustration: one model is now shown for every step.':'Illustration: each step can have its own model.';
    });
  }
  const boxes=[...document.querySelectorAll('[data-select-step]')];
  const selectionCount=byId('selected-count');
  function updateSelection(){if(selectionCount)selectionCount.textContent=boxes.filter(b=>b.checked).length+' of '+boxes.length+' selected';}
  boxes.forEach(b=>b.addEventListener('change',updateSelection));
  byId('all-steps')?.addEventListener('click',()=>{boxes.forEach(b=>b.checked=true);updateSelection();});
  byId('clear-steps')?.addEventListener('click',()=>{boxes.forEach(b=>b.checked=false);updateSelection();});
  byId('apply-model')?.addEventListener('click',()=>{
    const chosen=byId('bulk-model')?.value;
    if(chosen){boxes.filter(b=>b.checked).forEach(b=>{const select=byId('model-'+b.value);if(select)select.value=chosen;});}
    if(feedback)feedback.textContent=chosen?'Illustration only: selected rows now show '+chosen+'.':'Choose a sample model first.';
  });
  updateSelection();
})();