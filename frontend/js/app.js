const toast = document.querySelector('#toast');
const dialog = document.querySelector('#loginDialog');
let toastTimer;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const currency = amount => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(amount || 0));
function notify(message) { toast.textContent = message; toast.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('show'), 2800); }
function setSignedIn(signedIn) { document.querySelector('#loginButton').hidden = signedIn; document.querySelector('#logoutButton').hidden = !signedIn; }
function applyIdentity(user) {
  const labels = {customer:'Customer', staff:'Staff', admin:'Administrator'};
  const label = labels[user.role] || 'Account';
  const badge = document.querySelector('#roleBadge');
  badge.textContent = label;
  badge.hidden = false;
  document.querySelector('#currentUserName').textContent = user.full_name;
  document.querySelector('#currentUserRole').textContent = `${label} account`;
  document.querySelector('#currentUserAvatar').textContent = user.full_name.split(/\s+/).slice(0,2).map(part=>part[0]).join('').toUpperCase();
}
function applyRole(role) {
  document.body.dataset.role = role;
  const allowed = role === 'customer' ? new Set(['overview','subscriptions','invoices','quotations','plans']) : role === 'staff' ? new Set(['overview','subscriptions','customers','invoices','products','taxes','quotations','discounts','payments']) : new Set(['overview','subscriptions','customers','staff','invoices','plans','products','taxes','quotations','discounts','payments']);
  document.querySelectorAll('.nav-item').forEach(link => { const key=link.getAttribute('href').slice(1); link.hidden=!allowed.has(key); });
  document.querySelector('.nav-label')?.toggleAttribute('hidden',role==='customer');
  document.querySelector('.tools-label')?.toggleAttribute('hidden',role==='customer');
}
const tokenHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` });
async function api(path, options = {}) {
  const headers = { ...tokenHeaders(), ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) { const body = await response.json().catch(() => ({})); const detail=Array.isArray(body.detail)?body.detail.map(item=>item.msg||item.message||String(item)).join('; '):body.detail; throw new Error(detail || `Request failed (${response.status})`); }
  return response.status === 204 ? null : response.json();
}
async function loadWorkspace() {
  const token = localStorage.getItem('access_token');
  if (!token) { setSignedIn(false); return; }
  const headers = tokenHeaders();
  try {
    const response = await fetch('/api/dashboard/summary', { headers });
    if (response.status === 401) { localStorage.removeItem('access_token'); setSignedIn(false); return; }
    if (response.status === 403) { await loadCustomerPortal(headers); return; }
    if (!response.ok) throw new Error('Could not load workspace data');
    const data = await response.json();
    const me = await api('/api/auth/me'); applyRole(me.role); applyIdentity(me);
    document.querySelector('#activeCount').textContent = Number(data.active_subscriptions).toLocaleString('en-IN');
    document.querySelector('#customerCount').textContent = Number(data.active_customers).toLocaleString('en-IN');
    document.querySelector('#outstanding').textContent = currency(data.outstanding_amount);
    document.querySelector('#mrr').textContent = currency(data.monthly_recurring_revenue);
    document.querySelector('#revenueTotal').textContent = currency(data.revenue_received);
    document.querySelector('.negative').textContent = `${data.overdue_invoices} overdue`;
    const [invoices, plans, trend] = await Promise.all([api('/api/dashboard/invoices?limit=5'), api('/api/plans'), api('/api/dashboard/revenue-trend')]);
    document.querySelector('#activityRows').innerHTML = invoices.length ? invoices.map(i => `<tr><td>${esc(i.number)}</td><td>Invoice</td><td>Subscription #${i.subscription_id}</td><td>${new Date(i.due_at).toLocaleDateString('en-IN')}</td><td><span class="status-pill ${i.status==='paid'?'paid':'active'}">${esc(i.status)}${i.overdue?' · overdue':''}</span></td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">No invoices yet.</td></tr>';
    document.querySelector('#planList').innerHTML = plans.length ? plans.slice(0,5).map((p,i)=>`<div class="plan-row"><div class="plan-symbol ${['starter','growth','scale','enterprise'][i%4]}">${esc(p.name.slice(0,1))}</div><div class="plan-info"><strong>${esc(p.name)}</strong><small>${currency(p.price)} / ${esc(p.interval)}</small></div><strong>—</strong><div class="progress"><i style="width:0%"></i></div></div>`).join('') : '<div class="plan-empty">No plans published yet.</div>';
    drawRevenue(trend); setSignedIn(true);
  } catch (error) { notify(error.message); }
}
async function loadCustomerPortal(headers) {
  const [me, subscriptions, invoices] = await Promise.all([api('/api/auth/me'), api('/api/subscriptions'), api('/api/invoices')]);
  applyRole('customer'); applyIdentity(me);
  document.querySelectorAll('.metric-title')[0].childNodes[0].textContent='Amount due ';
  document.querySelectorAll('.metric-title')[2].childNodes[0].textContent='My subscriptions ';
  document.querySelector('h1').textContent = 'Your Brightloop account';
  document.querySelector('.subtitle').innerHTML = `Welcome, ${esc(me.full_name)}. Manage your subscriptions and billing here. <span id="roleBadge" class="role-badge">Customer</span>`;
  document.querySelector('#activeCount').textContent = subscriptions.filter(s=>s.status==='active').length;
  document.querySelector('#customerCount').textContent = subscriptions.length;
  const owed = invoices.filter(i=>i.status!=='paid').reduce((sum,i)=>sum+Number(i.amount_due),0);
  document.querySelector('#mrr').textContent = currency(owed); document.querySelector('#outstanding').textContent = currency(owed);
  document.querySelector('#revenueTotal').textContent = `${invoices.length} invoices`;
  document.querySelector('#activityRows').innerHTML = invoices.length ? invoices.map(i=>`<tr><td>${esc(i.number)}</td><td>Invoice</td><td>Subscription #${i.subscription_id}</td><td>${new Date(i.due_at).toLocaleDateString('en-IN')}</td><td><span class="status-pill ${i.status==='paid'?'paid':'active'}">${esc(i.status)}</span><a class="text-link" href="/api/invoices/${i.id}/print" target="_blank">Print</a></td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">No invoices yet.</td></tr>';
  document.querySelector('#planList').innerHTML = subscriptions.length ? subscriptions.map(s=>`<div class="plan-row"><div class="plan-symbol growth">S</div><div class="plan-info"><strong>${esc(s.plan)}</strong><small>Renews ${new Date(s.current_period_end).toLocaleDateString('en-IN')}</small></div><strong>${esc(s.status)}</strong><button class="select-button" data-cancel="${s.id}">Cancel</button></div>`).join('') : '<div class="plan-empty">No subscriptions yet.</div>';
  document.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=async()=>{try{await api(`/api/subscriptions/${b.dataset.cancel}/cancel`,{method:'POST'});notify('Cancellation scheduled.');await loadCustomerPortal(headers);}catch(e){notify(e.message);}});
  document.querySelector('#newSubscription').textContent='Browse plans'; setSignedIn(true);
}
function drawRevenue(items) {
  const svg=document.querySelector('#revenueChart'), labels=document.querySelector('#revenueMonths');
  labels.innerHTML=items.map(i=>`<span>${esc(i.month)}</span>`).join('');
  const max=Math.max(...items.map(i=>i.revenue),1), points=items.map((i,n)=>({x:n*720/Math.max(items.length-1,1),y:190-i.revenue/max*155}));
  const path=points.map((p,n)=>`${n?'L':'M'}${p.x} ${p.y}`).join(' '); svg.querySelectorAll('.dynamic-chart').forEach(n=>n.remove());
  const area=document.createElementNS('http://www.w3.org/2000/svg','path'); area.setAttribute('class','area dynamic-chart');area.setAttribute('d',`${path} L720 205 L0 205Z`);svg.append(area);
  const line=document.createElementNS('http://www.w3.org/2000/svg','path');line.setAttribute('class','line dynamic-chart');line.setAttribute('d',path);svg.append(line);
  if(points.length){const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('class','dynamic-chart');dot.setAttribute('cx',points.at(-1).x);dot.setAttribute('cy',points.at(-1).y);dot.setAttribute('r','4');svg.append(dot);}
}
const screens = {
  subscriptions:{title:'Subscriptions',subtitle:'Track customer plans, renewals, and lifecycle status.',url:'/api/subscriptions',cols:['Number','Customer','Plan','Status','Next billing'], keys:['number','customer_name','plan','status','next_billing_at']},
  customers:{title:'Customers',subtitle:'Customer accounts and billing relationships.',url:'/api/customers',cols:['ID','Name','Email','Joined','Status'],keys:['id','name','email','created_at','active']},
  staff:{title:'Staff members',subtitle:'Internal team accounts. Only admins can add staff.',url:'/api/customers/staff',cols:['ID','Name','Email','Joined','Status'],keys:['id','name','email','created_at','active']},
  invoices:{title:'Invoices',subtitle:'Review balances, due dates, and payment status.',url:'/api/dashboard/invoices?limit=100',cols:['Invoice','Subscription','Amount','Due','Status'],keys:['number','subscription_id','amount_due','due_at','status']},
  products:{title:'Products',subtitle:'Manage your product catalog, variants, and pricing.',url:'/api/products',cols:['Product','Type','Sales price','Cost price','Variants'],keys:['name','product_type','sales_price','cost_price','variants']},
  taxes:{title:'Taxes',subtitle:'Tax rates applied to invoices automatically.',url:'/api/taxes',cols:['Name','Rate','Status'],keys:['name','rate_percent','is_active']},
  quotations:{title:'Quotations',subtitle:'Reusable quote templates and customer offers.',url:'/api/quotation-templates',cols:['Template','Plan ID','Validity (days)'],keys:['name','plan_id','validity_days']},
  plans:{title:'Plans',subtitle:'Set pricing and availability for recurring offers.',url:'/api/plans',cols:['Plan','Price','Interval','Minimum qty','Status'],keys:['name','price','interval','minimum_quantity','is_active']},
  discounts:{title:'Discounts',subtitle:'Manage automatic billing discounts and use limits.',url:'/api/discounts',cols:['Code','Percent','Fixed amount','Uses','Status'],keys:['code','percent_off','amount_off','usage_count','active']},
  payments:{title:'Payments',subtitle:'Record and reconcile invoice payments.',url:'/api/dashboard/invoices?limit=100',cols:['Invoice','Subscription','Amount due','Status','Action'],keys:['number','subscription_id','amount_due','status','id']},
};
const forms = {
  plans:{label:'New plan',fields:[['name','Plan name'],['description','Description'],['price','Price (INR)','number'],['interval','Billing interval','select','daily,weekly,monthly,yearly'],['minimum_quantity','Minimum quantity','number'],['product_id','Product ID (optional)','number']]},
  products:{label:'New product',fields:[['name','Product name'],['product_type','Type','select','service,goods,digital'],['sales_price','Sales price','number'],['cost_price','Cost price','number'],['notes','Notes']]},
  taxes:{label:'New tax rule',fields:[['name','Tax name'],['rate_percent','Rate (%)','number']]},
  discounts:{label:'New discount',fields:[['code','Code'],['percent_off','Percent off (0 if fixed)','number'],['amount_off','Fixed amount (0 if percent)','number'],['minimum_quantity','Minimum quantity','number'],['minimum_amount','Minimum purchase','number'],['product_id','Product ID (optional)','number'],['usage_limit','Usage limit (optional)','number']]},
  subscriptions:{label:'New subscription',fields:[['plan_id','Plan ID','number'],['quantity','Quantity','number'],['customer_id','Customer ID','number'],['variant_id','Variant ID (optional)','number']]},
  invoices:{label:'Create invoice',fields:[['subscription_id','Subscription ID','number'],['discount_code','Discount code (optional)']]},
  payments:{label:'Record payment',fields:[['invoice_id','Invoice ID','number'],['provider_reference','Reference'],['method','Method','select','cash,bank_transfer,card,upi,other'],['note','Note']]},
  quotations:{label:'New quotation template',fields:[['name','Template name'],['plan_id','Plan ID','number'],['validity_days','Validity days','number']]},
  taxes:{label:'New tax rule',fields:[['name','Tax name'],['rate_percent','Rate (%)','number']]},
  staff:{label:'New staff member',fields:[['email','Work email','email'],['full_name','Full name'],['password','Temporary password','password']]},
};
async function showScreen(name) {
  const config={...screens[name]}; if(!config)return;
  const role=document.body.dataset.role||'customer';
  if(name==='quotations'&&role==='customer'){config.url='/api/quotations';config.cols=['Quote','Plan','Status','Expires','Action'];config.keys=['number','plan','status','expires_at'];}
  document.querySelector('.main').classList.add('manage-mode');
  const root=document.querySelector('.page'); root.innerHTML=`<div class="page-heading"><div><p class="eyebrow">BRIGHTLOOP WORKSPACE</p><h1>${config.title}</h1><p class="subtitle">${config.subtitle}</p></div><button class="primary-button" id="createAction">＋ ${forms[name]?.label||'Create'}</button></div><section class="panel data-panel"><div class="panel-heading"><div><h2>${config.title}</h2><p>Live data from your workspace</p></div><input class="table-search" id="screenSearch" placeholder="Filter results" aria-label="Filter results"></div><div class="table-wrap"><table><thead><tr>${config.cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody id="screenRows"><tr><td colspan="${config.cols.length}" class="empty-cell">Loading…</td></tr></tbody></table></div></section>`;
  try { let rows=await api(config.url); if(name==='payments')rows=rows.filter(r=>r.status!=='paid'); const render=(arr)=>{document.querySelector('#screenRows').innerHTML=arr.length?arr.map(row=>`<tr>${config.keys.map(k=>`<td>${k==='amount_due'?currency(row[k]):k==='variants'?(row[k]||[]).map(v=>esc(v.name)).join(', '):esc(row[k]??'—')}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${config.cols.length}" class="empty-cell">No ${name} to show yet.</td></tr>`;};render(rows);
    if(name==='quotations'&&role==='customer'){
      const body=document.querySelector('#screenRows');
      body.innerHTML=rows.length?rows.map(q=>`<tr><td>${esc(q.number)}</td><td>${esc(q.plan)}</td><td>${esc(q.status)}</td><td>${new Date(q.expires_at).toLocaleDateString('en-IN')}</td><td>${q.status==='quotation'?`<button class="select-button" data-accept="${q.id}">Accept offer</button>`:'—'}</td></tr>`).join(''):`<tr><td colspan="5" class="empty-cell">No offers have been sent to your account.</td></tr>`;
      body.querySelectorAll('[data-accept]').forEach(button=>button.onclick=async()=>{try{await api(`/api/quotations/${button.dataset.accept}/accept`,{method:'POST'});notify('Offer accepted. Your subscription is awaiting activation.');await showScreen('quotations');}catch(error){notify(error.message);}});
    }
    document.querySelector('#screenSearch').oninput=e=>render(rows.filter(r=>JSON.stringify(r).toLowerCase().includes(e.target.value.toLowerCase())));
    const create=document.querySelector('#createAction');
    const adminOnly=['products','plans','taxes','discounts','staff'].includes(name);
    const staffOrAdmin=['invoices','payments'].includes(name);
    const customerAllowed=false;
    const canCreate=!!forms[name]&&(role==='admin'||(role==='staff'&&!adminOnly)|| (role==='customer'&&customerAllowed));
    create.hidden=!canCreate;
    if(forms[name]&&canCreate)create.onclick=()=>showCreateForm(name);else create.onclick=null;
  } catch(e){document.querySelector('#screenRows').innerHTML=`<tr><td colspan="${config.cols.length}" class="empty-cell">${esc(e.message)}</td></tr>`;}
}
async function showCreateForm(name){
  let form=forms[name];
  if(name==='products')form.fields=[...form.fields,['variant_name','First variant (optional)'],['price_adjustment','Variant price adjustment','number']];
  if(name==='subscriptions'){
    const [plans, customers, products]=await Promise.all([api('/api/plans'),api('/api/customers'),api('/api/products')]);
    const planOptions=plans.map(p=>`${p.id}::${p.name} — ${currency(p.price)} / ${p.interval}`).join(';');
    const customerOptions=customers.map(c=>`${c.id}::${c.name} — ${c.email}`).join(';');
    const variantOptions=['::No variant',...products.flatMap(p=>(p.variants||[]).map(v=>`${v.id}::${p.name} · ${v.name} (+${currency(v.price_adjustment)})`))].join(';');
    form={...form,fields:[['plan_id','Plan','select',planOptions],['customer_id','Customer','select',customerOptions],['quantity','Quantity','number'],['variant_id','Product variant (optional)','select',variantOptions]]};
  }
  const dialogEl=document.querySelector('#actionDialog');
  dialogEl.innerHTML=`<form id="actionForm" class="action-form"><button class="dialog-close" type="button">×</button><p class="eyebrow">BRIGHTLOOP WORKSPACE</p><h2>${form.label}</h2>${form.fields.map(([key,label,type='text',opts])=>`<label>${label}${type==='select'?`<select name="${key}">${opts.includes('::')?opts.split(';').map(item=>{const [value,...label]=item.split('::');return `<option value="${esc(value)}">${esc(label.join('::'))}</option>`}).join(''):opts.split(',').map(v=>`<option>${esc(v)}</option>`).join('')}</select>`:`<input name="${key}" type="${type}" ${type==='number'?'step="0.01" min="0"':''} ${['name','price','email','plan_id','customer_id','quantity','code'].includes(key)?'required':''}>`}</label>`).join('')}<p class="form-error" id="actionError"></p><button class="primary-button" type="submit">Save</button></form>`;dialogEl.showModal();
  dialogEl.querySelector('.dialog-close').onclick=()=>dialogEl.close(); dialogEl.querySelector('form').onsubmit=async e=>{e.preventDefault();const fd=new FormData(e.currentTarget),payload=Object.fromEntries(fd.entries());
    try{let result;if(name==='products'){const variant=payload.variant_name,adjustment=payload.price_adjustment;delete payload.variant_name;delete payload.price_adjustment;payload.sales_price=Number(payload.sales_price||0);payload.cost_price=Number(payload.cost_price||0);result=await api('/api/products',{method:'POST',body:JSON.stringify(payload)});if(variant)await api(`/api/products/${result.id}/variants`,{method:'POST',body:JSON.stringify({name:variant,price_adjustment:Number(adjustment||0)})});}
    else if(name==='plans'){payload.price=Number(payload.price);payload.minimum_quantity=Number(payload.minimum_quantity||1);payload.product_id=payload.product_id?Number(payload.product_id):null;result=await api('/api/plans',{method:'POST',body:JSON.stringify(payload)});}
    else if(name==='taxes')result=await api('/api/taxes',{method:'POST',body:JSON.stringify({...payload,rate_percent:Number(payload.rate_percent)})});
    else if(name==='discounts'){payload.percent_off=Number(payload.percent_off||0);payload.amount_off=Number(payload.amount_off||0);payload.minimum_quantity=Number(payload.minimum_quantity||1);payload.minimum_amount=Number(payload.minimum_amount||0);payload.product_id=payload.product_id?Number(payload.product_id):null;payload.usage_limit=payload.usage_limit?Number(payload.usage_limit):null;result=await api('/api/discounts',{method:'POST',body:JSON.stringify(payload)});}
    else if(name==='subscriptions')result=await api('/api/subscriptions',{method:'POST',body:JSON.stringify({plan_id:Number(payload.plan_id),quantity:Number(payload.quantity||1),customer_id:payload.customer_id?Number(payload.customer_id):null,variant_id:payload.variant_id?Number(payload.variant_id):null})});
    else if(name==='quotations'){if(document.body.dataset.role==='customer')throw new Error('Customers can accept offers sent to their account.');result=await api('/api/quotation-templates',{method:'POST',body:JSON.stringify({...payload,plan_id:Number(payload.plan_id),validity_days:Number(payload.validity_days||30)})});}
    else if(name==='payments'){const invoiceId=Number(payload.invoice_id);delete payload.invoice_id;result=await api(`/api/invoices/${invoiceId}/payments`,{method:'POST',body:JSON.stringify(payload)});}
    else if(name==='invoices')result=await api('/api/invoices',{method:'POST',body:JSON.stringify({subscription_id:Number(payload.subscription_id),discount_code:payload.discount_code||null})});
    else if(name==='staff')result=await api('/api/auth/staff',{method:'POST',body:JSON.stringify(payload)});
    notify('Saved successfully.');dialogEl.close();await showScreen(name);
    }catch(err){document.querySelector('#actionError').textContent=err.message;}
  };
}
document.querySelectorAll('.nav-item').forEach(link=>link.addEventListener('click',event=>{const key=link.getAttribute('href').slice(1);if(key==='overview'){event.preventDefault();location.reload();return;}if(screens[key]){event.preventDefault();document.querySelectorAll('.nav-item').forEach(a=>a.classList.toggle('active',a===link));showScreen(key);}else if(key==='settings'){event.preventDefault();notify('Workspace settings are coming next.');}}));
document.querySelector('#loginButton').addEventListener('click',()=>dialog.showModal());
document.querySelector('#registerLink').addEventListener('click',()=>{document.querySelector('#loginForm').hidden=true;document.querySelector('#registerForm').hidden=false;});
document.querySelector('#forgotLink').addEventListener('click',()=>{document.querySelector('#loginForm').hidden=true;document.querySelector('#resetRequestForm').hidden=false;});
document.querySelector('#resetBack').addEventListener('click',()=>{document.querySelector('#resetRequestForm').hidden=true;document.querySelector('#loginForm').hidden=false;});
document.querySelector('#loginLink').addEventListener('click',()=>{document.querySelector('#registerForm').hidden=true;document.querySelector('#loginForm').hidden=false;});
document.querySelectorAll('.dialog-close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
document.querySelector('#todayLabel').textContent=new Intl.DateTimeFormat('en-IN',{weekday:'long',month:'long',day:'numeric',year:'numeric'}).format(new Date()).toUpperCase();
document.querySelector('#logoutButton').addEventListener('click',()=>{localStorage.removeItem('access_token');location.reload();});
document.querySelector('#resetRequestForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{const r=await api('/api/auth/password-reset/request',{method:'POST',body:JSON.stringify({email:f.get('email')})});document.querySelector('#resetRequestMessage').textContent=r.message;}catch{document.querySelector('#resetRequestMessage').textContent='Unable to request a reset right now.';}});
document.querySelector('#resetConfirmForm').addEventListener('submit',async e=>{e.preventDefault();const token=new URLSearchParams(location.search).get('token'),password=new FormData(e.currentTarget).get('password');try{const r=await api('/api/auth/password-reset/confirm',{method:'POST',body:JSON.stringify({token,new_password:password})});document.querySelector('#resetConfirmMessage').textContent=r.message;history.replaceState({},'',location.pathname);setTimeout(()=>{document.querySelector('#resetConfirmForm').hidden=true;document.querySelector('#loginForm').hidden=false;},1200);}catch(err){document.querySelector('#resetConfirmMessage').textContent=err.message;}});
const resetToken=new URLSearchParams(location.search).get('token');if(resetToken){document.querySelector('#loginForm').hidden=true;document.querySelector('#resetConfirmForm').hidden=false;dialog.showModal();}
document.querySelector('#registerForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{await api('/api/auth/register',{method:'POST',body:JSON.stringify({full_name:f.get('full_name'),email:f.get('email'),password:f.get('password')})});document.querySelector('#registerForm').hidden=true;document.querySelector('#loginForm').hidden=false;document.querySelector('#loginForm [name=email]').value=f.get('email');notify('Account created. Sign in to continue.');}catch(err){document.querySelector('#registerError').textContent=err.message;}});
document.querySelector('#loginForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{const result=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email:f.get('email'),password:f.get('password')})});localStorage.setItem('access_token',result.access_token);dialog.close();location.reload();}catch{document.querySelector('#loginError').textContent='Email or password is incorrect.';}});
document.querySelector('#newSubscription').addEventListener('click',()=>{if(!localStorage.getItem('access_token')){dialog.showModal();return;}showScreen(document.body.dataset.role==='customer'?'plans':'subscriptions');});
loadWorkspace();
