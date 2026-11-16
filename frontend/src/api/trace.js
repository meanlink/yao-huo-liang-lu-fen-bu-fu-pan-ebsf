import {api} from './client.js';

const post=(path,body)=>api(path,{method:'POST',body:body?JSON.stringify(body):undefined});

export const trace={
  materials:()=>api('/trace/materials'),
  registerRaw:data=>post('/trace/materials/raw',data),
  genealogy:code=>api('/trace/materials/'+encodeURIComponent(code)+'/genealogy'),
  ledger:code=>api('/trace/materials/'+encodeURIComponent(code)+'/ledger'),
  createMix:data=>post('/trace/mixes',data),
  mixes:()=>api('/trace/mixes'),
  kilns:()=>api('/trace/kilns'),
  registerKiln:data=>post('/trace/kilns',data),
  issues:()=>api('/trace/issues'),
  createIssue:data=>post('/trace/issues',data),
  createPending:data=>post('/trace/issues/pending',data),
  resolveIssue:(id,batch_code)=>post('/trace/issues/'+id+'/resolve',{batch_code}),
  reviews:()=>api('/trace/reviews'),
  registerReview:data=>post('/trace/reviews',data),
  approveReview:id=>post('/trace/reviews/'+id+'/approve'),
  briefings:()=>api('/trace/briefings'),
  registerBriefing:data=>post('/trace/briefings',data),
  downloadBriefing:id=>'/api/trace/briefings/'+id+'/download',
  recalls:()=>api('/trace/recalls'),
  recall:id=>api('/trace/recalls/'+id),
  createRecall:data=>post('/trace/recalls',data),
  trace:(id,max_expansions)=>post('/trace/recalls/'+id+'/trace'+(max_expansions?('?max_expansions='+max_expansions):'')),
  recheck:id=>post('/trace/recalls/'+id+'/recheck'),
  markAffected:(rid,cid)=>post('/trace/recalls/'+rid+'/candidates/'+cid+'/affected'),
  exclude:(rid,cid,reason)=>post('/trace/recalls/'+rid+'/candidates/'+cid+'/exclude',{reason}),
  publish:(id,note)=>post('/trace/recalls/'+id+'/publish',{note:note||''}),
};
