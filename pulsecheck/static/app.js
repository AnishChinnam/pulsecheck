const $=id=>document.getElementById(id);
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let down=false,busy=false,refreshing=false;
async function request(url,options={}){
  const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(typeof error.detail==='string'?error.detail:`Request failed (${response.status})`);}
  return response.json();
}
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function when(value){return value?new Date(value).toLocaleString():'Not checked yet';}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
    const [report,incidents]=await Promise.all([request('/api/summary'),request('/api/incidents')]);
    down=report.demo_down;$('public-note').classList.toggle('hidden',!report.public_demo);$('add').classList.toggle('hidden',report.public_demo);$('demo-title').textContent=report.public_demo?'Try a simulated outage':'Local demo environment';$('demo').classList.toggle('hidden',!report.demo);
    $('outage').textContent=down?'Restore checkout service':'Simulate checkout outage';
    $('total').textContent=report.monitors.length;$('open').textContent=report.open_incidents;
    $('samples').textContent=report.monitors.reduce((sum,m)=>sum+m.checks_24h,0);
    $('updated').textContent='Updated '+new Date(report.as_of).toLocaleTimeString();
    $('rules').textContent=`Checked every ${report.interval_seconds}s after each cycle finishes. Opens after ${report.failure_threshold} failed checks; resolves after ${report.recovery_threshold} successful checks.`;
    $('monitors').innerHTML=report.monitors.length?report.monitors.map(m=>`<article class="monitor"><div><h3>${escape(m.name)}</h3><span class="url">${escape(m.url)}</span></div><div><span class="state ${m.enabled?m.state:'paused'}">${m.enabled?escape(m.state):'paused'}</span></div><div class="metric">${m.success_rate_24h===null?'—':m.success_rate_24h+'%'}<small>CHECK SUCCESS · ${m.checks_24h} SAMPLES</small></div><div class="metric">${m.p95_ms===null?'—':m.p95_ms+' ms'}<small>SUCCESSFUL PROBE P95</small></div>${report.public_demo?'':`<button class="secondary pause" type="button" data-id="${m.id}" data-enabled="${m.enabled}">${m.enabled?'Pause':'Resume'}</button>`}</article>`).join(''):'<p class="empty">No monitors yet. Add an allowlisted URL to begin.</p>';
    $('incidents').innerHTML=incidents.length?incidents.map(i=>`<article class="incident"><span class="state ${i.resolved_at?'up':'down'}">${i.resolved_at?'resolved':'open'}</span><strong>${escape(i.name)}</strong><p>Opened ${escape(when(i.opened_at))}</p><p>${i.resolved_at?'Resolved '+escape(when(i.resolved_at)):'Waiting for sustained recovery'}</p></article>`).join(''):'<p class="empty">No incidents recorded. The next confirmed outage will appear here.</p>';
    document.querySelectorAll('.pause').forEach(button=>button.onclick=async()=>{button.disabled=true;try{await request(`/api/monitors/${button.dataset.id}/enabled`,{method:'PUT',body:JSON.stringify({enabled:button.dataset.enabled==='0'})});await refresh();}catch(e){message(e.message,true);button.disabled=false;}});
  }catch(e){message('Could not refresh: '+e.message,true);}finally{refreshing=false;}
}
$('run').onclick=async()=>{if(busy)return;busy=true;$('run').disabled=true;message('Running bounded concurrent checks…');try{const r=await request('/api/checks/run',{method:'POST'});message(`Recorded ${r.results.length} checks.`);await refresh();}catch(e){message(e.message,true);}finally{busy=false;$('run').disabled=false;}};
$('outage').onclick=async()=>{try{await request('/api/demo/outage',{method:'POST',body:JSON.stringify({down:!down})});message(down?'Checkout restored. Two successful checks will resolve its incident.':'Checkout outage enabled. Two failed checks will open an incident.');await refresh();}catch(e){message(e.message,true);}};
$('add').onsubmit=async event=>{event.preventDefault();const form=new FormData(event.target);const body=Object.fromEntries(form);for(const key of ['expected_status','timeout_seconds','attempts'])body[key]=Number(body[key]);try{await request('/api/monitors',{method:'POST',body:JSON.stringify(body)});event.target.reset();message('Monitor added.');await refresh();}catch(e){message(e.message,true);}};
refresh();setInterval(refresh,5000);
