const $=id=>document.getElementById(id);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let down=false,busy=false,refreshing=false,auto=true,report=null,analytics=null,incidents=[],selectedMonitor=null,lastRefresh=null;
async function request(url,options={}){const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...options.headers}});if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(typeof error.detail==='string'?error.detail:`Request failed (${response.status})`);}return response.json();}
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function when(value){return value?new Date(value).toLocaleString():'Not checked yet';}
function duration(start,end){const seconds=Math.max(0,Math.round((new Date(end||Date.now())-new Date(start))/1000));return seconds<60?seconds+'s':seconds<3600?Math.floor(seconds/60)+'m '+seconds%60+'s':Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m';}
function incidentHTML(items){return items.length?items.map(i=>`<article class="incident"><span class="state ${i.resolved_at?'up':'down'}">${i.resolved_at?'resolved':'open'}</span><strong>${escape(i.name)}</strong><p>Opened ${escape(when(i.opened_at))}</p><p>${i.resolved_at?'Recovered '+escape(when(i.resolved_at)):'Waiting for sustained recovery'} · ${duration(i.opened_at,i.resolved_at)} ${i.resolved_at?'duration':'elapsed'}</p><button class="text-button" data-inspect="${i.monitor_id}">Investigate service →</button></article>`).join(''):'<p class="empty">No matching incidents. Confirmed outages will appear here.</p>';}
function chart(samples,width=650,height=150){
 if(!samples.length)return '<p class="empty">No checks in this window.</p>';
 const max=Math.max(1,...samples.filter(s=>s.ok).map(s=>s.latency_ms)),x=i=>15+(width-30)*i/Math.max(1,samples.length-1),y=v=>height-22-(height-45)*v/max;
 let segments=[],line=[];
 samples.forEach((s,i)=>{if(!s.ok){if(line.length)segments.push(line.join(' '));line=[];}else line.push(x(i)+','+y(s.latency_ms));});if(line.length)segments.push(line.join(' '));
 return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Response latency for recorded checks"><line x1="15" y1="${height-22}" x2="${width-15}" y2="${height-22}" stroke="#405268"/>${segments.map(points=>`<polyline points="${points}" fill="none" stroke="#65d9b1" stroke-width="2"/>`).join('')}${samples.map((s,i)=>`<circle cx="${x(i)}" cy="${s.ok?y(s.latency_ms):height-22}" r="${s.ok?2:4}" fill="${s.ok?'#65d9b1':'#ff8c91'}"><title>${escape(when(s.checked_at))}: ${s.ok?s.latency_ms+' ms':escape(s.error||'failed')}</title></circle>`).join('')}<text x="15" y="13" fill="#96a6bd" font-size="10">${Math.round(max)} ms</text><text x="15" y="${height-4}" fill="#96a6bd" font-size="9">${escape(new Date(samples[0].checked_at).toLocaleTimeString())}</text><text x="${width-15}" y="${height-4}" text-anchor="end" fill="#96a6bd" font-size="9">${escape(new Date(samples.at(-1).checked_at).toLocaleTimeString())}</text></svg>`;
}
function renderServices(){
 if(!report||!analytics)return;const query=$('monitor-search').value.toLowerCase(),state=$('state-filter').value;
 const monitors=report.monitors.filter(m=>(m.name+' '+m.url).toLowerCase().includes(query)&&(state==='all'||(m.enabled?m.state:'paused')===state));
 $('monitors').innerHTML=monitors.length?monitors.map(m=>{
 const a=analytics.monitors.find(a=>a.monitor_id===m.id),last=a?.last_sample;
 return `<article class="monitor monitor-card"><div class="monitor-top"><div><h3><button class="monitor-name" data-inspect="${m.id}">${escape(m.name)} ↗</button></h3><span class="url">${escape(m.url)}</span></div><span class="state ${m.enabled?m.state:'paused'}">${m.enabled?escape(m.state):'paused'}</span></div><div class="metric-row"><div class="metric">${a?.success_rate==null?'—':a.success_rate+'%'}<small>CHECK SUCCESS</small></div><div class="metric">${a?.p95_ms==null?'—':a.p95_ms+' ms'}<small>SUCCESSFUL P95</small></div><div class="metric">${a?.average_ms==null?'—':a.average_ms+' ms'}<small>AVERAGE RESPONSE</small></div></div><div class="sample-strip" aria-label="Recent check results">${a?.recent.length?a.recent.map(s=>`<span class="sample ${s.ok?'ok':'failed'}" title="${escape(when(s.checked_at))} · ${s.ok?s.latency_ms+' ms':escape(s.error||'failed')}"></span>`).join(''):'<span class="no-samples">Waiting for the first observation</span>'}</div><div class="monitor-bottom"><span>${a?.checks||0} samples · ${a?.failures||0} failed</span><button class="text-button" data-inspect="${m.id}">Inspect checks →</button>${report.public_demo?'':`<button class="secondary pause" data-id="${m.id}" data-enabled="${m.enabled}">${m.enabled?'Pause':'Resume'}</button>`}</div><p class="last-response">${last?`${last.ok?'Last response':'Last failure'}: ${last.status_code??escape(last.error)} · ${escape(when(last.checked_at))}`:'No observed response yet'}</p></article>`;
 }).join(''):'<p class="empty">No services match these filters.</p>';
}
function renderIncidents(){const filter=$('incident-filter').value;$('incidents').innerHTML=incidentHTML(incidents.filter(i=>filter==='all'||(filter==='open'?!i.resolved_at:!!i.resolved_at)));}
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
 const values=await Promise.all([request('/api/summary'),request('/api/incidents'),request('/api/analytics?hours='+$('time-window').value)]);
 [report,incidents,analytics]=values;lastRefresh=Date.now();down=report.demo_down;
 $('demo').classList.toggle('hidden',!report.demo);$('public-note').classList.toggle('hidden',!report.public_demo);$('add').classList.toggle('hidden',report.public_demo);document.querySelector('.bottom').classList.toggle('full-width',report.public_demo);
 $('demo-title').textContent=report.public_demo?'Try the incident workflow':'Local demo environment';$('outage').textContent=down?'Restore checkout service':'Simulate checkout outage';
 const enabled=report.monitors.filter(m=>m.enabled),unhealthy=enabled.filter(m=>['down','degraded'].includes(m.state)),pending=enabled.filter(m=>m.state==='pending');
 $('overall-status').textContent=unhealthy.length?`${unhealthy.length} service${unhealthy.length===1?' needs':'s need'} attention`:pending.length?'Waiting for initial checks':enabled.length?'All monitored services are responding':'No active monitors';$('health-banner').classList.toggle('unhealthy',!!unhealthy.length);
 const sampled=analytics.monitors.reduce((s,m)=>s+m.checks,0),failed=analytics.monitors.reduce((s,m)=>s+m.failures,0);
 $('total').textContent=report.monitors.length;$('up-count').textContent=enabled.filter(m=>m.state==='up').length+' healthy · '+(report.monitors.length-enabled.length)+' paused';$('open').textContent=report.open_incidents;$('incident-context').textContent=incidents.filter(i=>i.resolved_at).length+' resolved in retained history';$('samples').textContent=sampled;$('sample-context').textContent=`Last ${analytics.window_hours} hour${analytics.window_hours===1?'':'s'} · ${failed} failures`;$('success').textContent=sampled?(100*(sampled-failed)/sampled).toFixed(2)+'%':'—';
 $('updated').textContent='Updated '+new Date(report.as_of).toLocaleTimeString();$('freshness').textContent=report.monitors.length&&report.monitors.some(m=>m.last_checked_at)?'Latest probe: '+when(report.monitors.map(m=>m.last_checked_at).filter(Boolean).sort().at(-1)):'Awaiting first probe';
 $('rules').textContent=`Fixed-delay checks every ${report.interval_seconds}s. Incident threshold: ${report.failure_threshold} failures; recovery: ${report.recovery_threshold} successes. Bars represent recent observations, not elapsed uptime.`;
 const buckets=Array.from({length:24},(_,i)=>({at:analytics.monitors[0]?.buckets[i].at,samples:analytics.monitors.reduce((s,m)=>s+m.buckets[i].samples,0),successes:analytics.monitors.reduce((s,m)=>s+m.buckets[i].successes,0)}));
 const maxBucket=Math.max(1,...buckets.map(b=>b.samples));
 $('window-chart').innerHTML=buckets.map(b=>`<div class="bucket" title="${escape(when(b.at))}: ${b.samples} checks, ${b.samples-b.successes} failed"><span class="bucket-ok" style="height:${b.successes/maxBucket*100}%"></span><span class="bucket-failed" style="height:${(b.samples-b.successes)/maxBucket*100}%"></span></div>`).join('');
 $('trend-window').textContent=analytics.window_hours*60/24+' minutes per column';
 renderServices();renderIncidents();
 }catch(e){message('Refresh failed. Showing the last available data. '+e.message,true);$('freshness').textContent=lastRefresh?'Last successful refresh: '+when(lastRefresh):'No live data yet';}finally{refreshing=false;}
}
async function inspect(id){
 selectedMonitor=id;$('monitor-dialog').showModal();$('detail-title').textContent='Loading monitor…';$('detail-error').textContent='';
 for(const el of ['detail-url','detail-config','detail-chart','detail-history','detail-incidents'])$(el).innerHTML='';
 try{const r=await request('/api/monitors/'+id);if(selectedMonitor!==id)return;
 const m=r.monitor;$('detail-title').textContent=m.name;$('detail-url').textContent=m.url;
 $('detail-config').innerHTML=`<span>Expected HTTP <strong>${m.expected_status}</strong></span><span>Deadline <strong>${m.timeout_seconds}s</strong></span><span>Max attempts <strong>${m.attempts}</strong></span><span>State <strong>${escape(m.state)}</strong></span>`;
 $('detail-chart').innerHTML=chart([...r.checks].reverse());$('detail-history').innerHTML=r.checks.length?`<table><thead><tr><th>Checked</th><th>Result</th><th>HTTP</th><th>Latency</th><th>Attempts</th></tr></thead><tbody>${r.checks.map(c=>`<tr><td>${escape(when(c.checked_at))}</td><td><span class="state ${c.ok?'up':'down'}">${c.ok?'success':escape(c.error||'failed')}</span></td><td>${c.status_code??'—'}</td><td>${c.latency_ms} ms</td><td>${c.attempts_used}</td></tr>`).join('')}</tbody></table>`:'<p class="empty">No checks recorded.</p>';
 $('detail-incidents').innerHTML=incidentHTML(r.incidents);
 }catch(e){$('detail-error').textContent=e.message;}
}
$('detail-close').onclick=()=>{selectedMonitor=null;$('monitor-dialog').close();};
document.addEventListener('click',async event=>{
 const inspectButton=event.target.closest('[data-inspect]');if(inspectButton){if($('monitor-dialog').open)$('monitor-dialog').close();inspect(Number(inspectButton.dataset.inspect));}
 const button=event.target.closest('.pause');if(button){button.disabled=true;try{await request(`/api/monitors/${button.dataset.id}/enabled`,{method:'PUT',body:JSON.stringify({enabled:button.dataset.enabled==='0'})});await refresh();}catch(e){message(e.message,true);button.disabled=false;}}
});
$('run').onclick=async()=>{if(busy)return;busy=true;$('run').disabled=true;message('Running bounded concurrent checks…');try{const r=await request('/api/checks/run',{method:'POST'});message(`Recorded ${r.results.length} checks. Inspect a service to investigate its responses.`);await refresh();}catch(e){message(e.message,true);}finally{busy=false;$('run').disabled=false;}};
$('outage').onclick=async()=>{const button=$('outage');button.disabled=true;try{await request('/api/demo/outage',{method:'POST',body:JSON.stringify({down:!down})});message(down?'Checkout restored. Two successful checks will resolve its incident.':'Checkout outage enabled. Two failed checks will open an incident.');await refresh();}catch(e){message(e.message,true);}finally{button.disabled=false;}};
$('add').onsubmit=async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.target));for(const key of ['expected_status','timeout_seconds','attempts'])body[key]=Number(body[key]);try{await request('/api/monitors',{method:'POST',body:JSON.stringify(body)});event.target.reset();message('Monitor added.');await refresh();}catch(e){message(e.message,true);}};
$('monitor-search').oninput=renderServices;$('state-filter').onchange=renderServices;$('incident-filter').onchange=renderIncidents;$('time-window').onchange=refresh;
$('auto-refresh').onclick=()=>{auto=!auto;$('auto-refresh').textContent='Auto refresh: '+(auto?'on':'off');$('auto-refresh').setAttribute('aria-pressed',String(auto));if(auto)refresh();};
$('theme').onclick=()=>{document.body.classList.toggle('light');try{localStorage.setItem('pulsecheck-theme',document.body.classList.contains('light')?'light':'dark');}catch{}};
try{document.body.classList.toggle('light',localStorage.getItem('pulsecheck-theme')==='light');}catch{}
refresh();setInterval(()=>{if(auto&&!document.hidden)refresh();},10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&auto)refresh();});
