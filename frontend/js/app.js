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
  const allowed = role === 'customer' ? new Set(['overview','subscriptions','invoices','quotations','plans']) : role === 'staff' ? new Set(['overview','analytics','subscriptions','customers','invoices','products','taxes','quotations','discounts','payments']) : new Set(['overview','analytics','subscriptions','customers','staff','invoices','plans','products','taxes','quotations','discounts','payments']);
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
document.querySelectorAll('.nav-item').forEach(link=>link.addEventListener('click',event=>{const key=link.getAttribute('href').slice(1);if(key==='overview'){event.preventDefault();location.reload();return;}if(key==='analytics'){event.preventDefault();if(!localStorage.getItem('access_token')){dialog.showModal();return;}document.querySelectorAll('.nav-item').forEach(a=>a.classList.toggle('active',a===link));showAnalytics();return;}if(screens[key]){event.preventDefault();document.querySelectorAll('.nav-item').forEach(a=>a.classList.toggle('active',a===link));showScreen(key);}else if(key==='settings'){event.preventDefault();notify('Workspace settings are coming next.');}}));
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
/* -------------------------------------------------------------------------
   Smart subscription analytics
   ------------------------------------------------------------------------- */
const analyticsState = {
  filters: analyticsDefaults(),
  options: null,
  data: {},
  renewalDays: 30,
  planSort: 'revenue',
  loading: false,
};
const reasonLabels = {
  too_expensive: 'Too expensive', no_longer_needed: 'No longer needed',
  switching_provider: 'Switching provider', missing_features: 'Missing features',
  business_closed: 'Business closed', other: 'Other', unspecified: 'Not specified',
};
const statusLabels = { active: 'Active', paused: 'Paused', confirmed: 'Pending', closed: 'Closed', cancelled: 'Cancelled' };
const chartColors = ['#236b5a', '#7456e8', '#e0a43b', '#d86978', '#4c8be4', '#5cb99d'];

function analyticsDefaults() {
  const end = new Date();
  const start = new Date(end.getFullYear(), end.getMonth() - 11, 1);
  return {
    start_date: start.toISOString().slice(0, 10),
    end_date: end.toISOString().slice(0, 10),
    granularity: 'month', plan_id: '', product_id: '', customer_id: '',
  };
}
function analyticsQuery(extra = {}) {
  const params = new URLSearchParams();
  const f = analyticsState.filters;
  if (f.start_date) params.set('start_date', f.start_date);
  if (f.end_date) params.set('end_date', f.end_date);
  params.set('granularity', f.granularity || 'month');
  ['plan_id', 'product_id', 'customer_id'].forEach(key => {
    if (f[key]) params.set(key, f[key]);
  });
  Object.entries(extra).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined) params.set(key, value); });
  return params.toString();
}
function analyticsPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(digits)}%`;
}
function compactCurrency(value) {
  const n = Number(value || 0);
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  if (abs >= 10000000) return `${sign}₹${(abs / 10000000).toFixed(1)}Cr`;
  if (abs >= 100000) return `${sign}₹${(abs / 100000).toFixed(1)}L`;
  if (abs >= 1000) return `${sign}₹${(abs / 1000).toFixed(1)}k`;
  return `${sign}₹${Math.round(abs)}`;
}
function analyticsDelta(change) {
  if (!change) return '';
  if (change.percent_change !== null && change.percent_change !== undefined) {
    const pct = Number(change.percent_change);
    return `<span class="${pct >= 0 ? 'positive' : 'negative'}">${pct >= 0 ? '↑' : '↓'} ${Math.abs(pct).toFixed(1)}%</span>`;
  }
  if (change.point_change !== null && change.point_change !== undefined) {
    const value = Number(change.point_change);
    return `<span class="${value >= 0 ? 'negative' : 'positive'}">${value >= 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(2)} pts</span>`;
  }
  return '';
}
function analyticsEmpty(message) {
  return `<div class="analytics-empty">${esc(message)}</div>`;
}
function analyticsError(message) {
  return `<div class="analytics-error">${esc(message || 'Unable to load this section.')}</div>`;
}
function analyticsKpi(label, value, change, tooltip, note = '', accent = 'purple') {
  return `<article class="metric analytics-metric">
    <div class="metric-title">${esc(label)}<span class="metric-icon ${accent}" title="${esc(tooltip)}">ⓘ</span></div>
    <div class="metric-value">${value}</div>
    <div class="metric-foot">${analyticsDelta(change)}<span class="metric-neutral">${esc(note)}</span></div>
  </article>`;
}
function svgNode(tag, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}
function drawAnalyticsLine(containerId, labels, series, emptyMessage = 'No data for this range.') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  const total = series.reduce((sum, item) => sum + item.values.reduce((s, v) => s + Number(v || 0), 0), 0);
  if (!labels.length || total === 0) { container.innerHTML = analyticsEmpty(emptyMessage); return; }
  const width = 760, height = 250, left = 64, right = 16, top = 18, bottom = 32;
  const innerW = width - left - right, innerH = height - top - bottom;
  const max = Math.max(...series.flatMap(item => item.values.map(v => Number(v || 0))), 1) * 1.12;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, class: 'analytics-svg', role: 'img', 'aria-label': 'Analytics chart' });
  for (let i = 0; i <= 4; i += 1) {
    const y = top + innerH * i / 4;
    svg.append(svgNode('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'analytics-grid-line' }));
    const text = svgNode('text', { x: left - 8, y: y + 3, 'text-anchor': 'end', class: 'analytics-axis-label' });
    text.textContent = compactCurrency(max * (1 - i / 4));
    svg.append(text);
  }
  const step = labels.length > 1 ? innerW / (labels.length - 1) : 0;
  labels.forEach((label, index) => {
    if (labels.length > 8 && index % Math.ceil(labels.length / 6) !== 0 && index !== labels.length - 1) return;
    const text = svgNode('text', { x: left + step * index, y: height - 8, 'text-anchor': 'middle', class: 'analytics-axis-label' });
    text.textContent = label;
    svg.append(text);
  });
  series.forEach((item, seriesIndex) => {
    const points = item.values.map((value, index) => ({
      x: left + step * index,
      y: top + innerH - (Number(value || 0) / max) * innerH,
      value: Number(value || 0),
      label: labels[index],
    }));
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ');
    svg.append(svgNode('path', { d: path, fill: 'none', stroke: item.color || chartColors[seriesIndex % chartColors.length], 'stroke-width': 2.4, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    points.forEach(p => {
      const circle = svgNode('circle', { cx: p.x, cy: p.y, r: 4, fill: '#fff', stroke: item.color || chartColors[seriesIndex % chartColors.length], 'stroke-width': 2 });
      const title = svgNode('title');
      title.textContent = `${p.label} · ${item.name}: ${currency(p.value)}`;
      circle.append(title);
      svg.append(circle);
    });
  });
  container.append(svg);
}
function drawAnalyticsBars(containerId, labels, values, color = '#236b5a', emptyMessage = 'No data for this range.') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  if (!labels.length || values.every(value => !Number(value))) { container.innerHTML = analyticsEmpty(emptyMessage); return; }
  const width = 760, height = 230, left = 50, right = 14, top = 14, bottom = 30;
  const innerW = width - left - right, innerH = height - top - bottom;
  const max = Math.max(...values.map(value => Number(value || 0)), 1) * 1.12;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, class: 'analytics-svg', role: 'img', 'aria-label': 'Customer acquisition chart' });
  for (let i = 0; i <= 4; i += 1) {
    const y = top + innerH * i / 4;
    svg.append(svgNode('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'analytics-grid-line' }));
    const text = svgNode('text', { x: left - 8, y: y + 3, 'text-anchor': 'end', class: 'analytics-axis-label' });
    text.textContent = String(Math.round(max * (1 - i / 4)));
    svg.append(text);
  }
  const slot = innerW / Math.max(labels.length, 1), barWidth = Math.max(8, slot * 0.52);
  labels.forEach((label, index) => {
    const value = Number(values[index] || 0);
    const barHeight = value / max * innerH;
    const x = left + slot * index + (slot - barWidth) / 2;
    const bar = svgNode('rect', { x, y: top + innerH - barHeight, width: barWidth, height: Math.max(barHeight, value ? 2 : 0), rx: 4, fill: color });
    const title = svgNode('title');
    title.textContent = `${label}: ${value}`;
    bar.append(title);
    svg.append(bar);
    if (labels.length <= 8 || index % Math.ceil(labels.length / 6) === 0) {
      const text = svgNode('text', { x: x + barWidth / 2, y: height - 8, 'text-anchor': 'middle', class: 'analytics-axis-label' });
      text.textContent = label;
      svg.append(text);
    }
  });
  container.append(svg);
}
async function showAnalytics() {
  const role = document.body.dataset.role || 'customer';
  if (role === 'customer') { notify('Company analytics are available to staff and administrators only.'); return; }
  if (!analyticsState.filters.start_date || !analyticsState.filters.end_date) analyticsState.filters = analyticsDefaults();
  document.querySelector('.main').classList.add('manage-mode');
  document.querySelector('.breadcrumbs').innerHTML = 'Workspace <span>/</span> <strong>Analytics</strong>';
  const root = document.querySelector('.page');
  root.classList.add('analytics-page');
  root.innerHTML = `<div class="page-heading"><div><p class="eyebrow">BRIGHTLOOP INTELLIGENCE</p><h1>Subscription Analytics</h1><p class="subtitle">Recurring revenue, retention, plan performance and a transparent schedule-based forecast.</p></div><button class="select-button" id="analyticsRefresh">↻ Refresh</button></div>
    <section class="panel analytics-toolbar" aria-label="Analytics filters">
      <div class="analytics-filter-fields">
        <label>From<input type="date" id="anStart"></label>
        <label>To<input type="date" id="anEnd"></label>
        <label>Aggregation<select id="anGranularity"><option value="month">Monthly</option><option value="quarter">Quarterly</option><option value="year">Yearly</option></select></label>
        <label>Plan<select id="anPlan"><option value="">All plans</option></select></label>
        <label>Product<select id="anProduct"><option value="">All products</option></select></label>
        <label>Customer<select id="anCustomer"><option value="">All customers</option></select></label>
      </div>
      <div class="analytics-filter-actions"><button class="primary-button" id="anApply">Apply filters</button><button class="select-button" id="anReset">Reset</button></div>
      <p class="analytics-filter-note">MRR uses active subscriptions and recurring discount rules; tax is excluded. Date filters apply to event metrics such as collections, growth, churn and renewals.</p>
    </section>
    <section class="analytics-kpi-grid" id="analyticsKpis"><div class="analytics-loading">Loading KPIs…</div></section>
    <div class="analytics-grid-two">
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Revenue trend</h2><p>Collected payments versus invoiced value</p></div><span class="analytics-badge" id="revenueTotals">—</span></div><div class="analytics-chart" id="revenueTrend"></div><div class="analytics-legend" id="revenueLegend"></div></section>
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>MRR movement</h2><p>Recurring revenue bridge by period</p></div><span class="analytics-badge">Normalised</span></div><div class="analytics-chart" id="mrrTrend"></div><div id="mrrMovementTable"></div></section>
    </div>
    <div class="analytics-grid-two">
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Customer growth</h2><p>New registered customers per period</p></div></div><div class="analytics-chart" id="customerGrowth"></div><div id="customerSummary"></div></section>
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Subscription status</h2><p>Current lifecycle distribution</p></div></div><div id="statusDistribution"></div></section>
    </div>
    <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Customer segments</h2><p>Click a segment to inspect the customer list</p></div></div><div class="analytics-segment-grid" id="customerSegments"></div></section>
    <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Subscription movement</h2><p>New, renewed, cancelled and closed subscriptions</p></div></div><div class="analytics-chart" id="subscriptionGrowth"></div><div class="analytics-legend" id="subscriptionLegend"></div></section>
    <div class="analytics-grid-two">
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Churn & cancellation reasons</h2><p>Actual closures with recorded reasons</p></div></div><div id="churnPanel"></div></section>
      <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Revenue breakdown</h2><p>Collected revenue by actual business dimensions</p></div></div><div id="revenueBreakdown"></div></section>
    </div>
    <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Plan performance</h2><p>MRR, subscribers, growth and cancellation pressure</p></div><select id="planSort" class="select-button"><option value="revenue">Highest revenue</option><option value="subscribers">Most subscribers</option><option value="growth">Highest growth</option><option value="cancellations">Highest cancellations</option></select></div><div id="planPerformance"><div class="analytics-loading">Loading plans…</div></div></section>
    <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Upcoming renewals</h2><p>Subscriptions due by billing schedule</p></div><div class="analytics-tabs" id="renewalTabs"><button data-days="7">7 days</button><button data-days="15">15 days</button><button data-days="30" class="active">30 days</button></div></div><div id="renewalsTable"><div class="analytics-loading">Loading renewals…</div></div></section>
    <section class="panel analytics-chart-card"><div class="panel-heading"><div><h2>Revenue forecast</h2><p>Deterministic view of existing billing schedules</p></div><span class="analytics-badge">Not an AI model</span></div><div id="forecastPanel"><div class="analytics-loading">Loading forecast…</div></div></section>`;
  document.querySelector('#analyticsRefresh').onclick = () => loadAnalytics();
  document.querySelector('#anApply').onclick = () => {
    const value = id => document.querySelector(id).value;
    analyticsState.filters = {
      start_date: value('#anStart'), end_date: value('#anEnd'), granularity: value('#anGranularity'),
      plan_id: value('#anPlan'), product_id: value('#anProduct'), customer_id: value('#anCustomer'),
    };
    loadAnalytics();
  };
  document.querySelector('#anReset').onclick = () => {
    analyticsState.filters = analyticsDefaults();
    analyticsState.renewalDays = 30;
    analyticsState.planSort = 'revenue';
    fillAnalyticsFilterValues();
    loadAnalytics();
  };
  document.querySelector('#planSort').onchange = async event => {
    analyticsState.planSort = event.target.value;
    const panel = document.querySelector('#planPerformance');
    panel.innerHTML = '<div class="analytics-loading">Loading plans…</div>';
    try {
      const data = await api(`/api/analytics/plans?${analyticsQuery({ sort: analyticsState.planSort })}`);
      renderPlanPerformance(data);
    } catch (error) { panel.innerHTML = analyticsError(error.message); }
  };
  document.querySelectorAll('#renewalTabs button').forEach(button => {
    button.onclick = () => {
      analyticsState.renewalDays = Number(button.dataset.days);
      document.querySelectorAll('#renewalTabs button').forEach(item => item.classList.toggle('active', item === button));
      if (analyticsState.data.subscriptions) renderRenewals(analyticsState.data.subscriptions);
      else loadAnalytics();
    };
  });
  await loadAnalyticsOptions();
  fillAnalyticsFilterValues();
  const planSort = document.querySelector('#planSort');
  if (planSort) planSort.value = analyticsState.planSort;
  await loadAnalytics();
}
async function loadAnalyticsOptions() {
  try {
    analyticsState.options = await api('/api/analytics/options');
    const plan = document.querySelector('#anPlan'), product = document.querySelector('#anProduct'), customer = document.querySelector('#anCustomer');
    if (plan) plan.innerHTML = '<option value="">All plans</option>' + analyticsState.options.plans.map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
    if (product) product.innerHTML = '<option value="">All products</option>' + analyticsState.options.products.map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
    if (customer) customer.innerHTML = '<option value="">All customers</option>' + analyticsState.options.customers.map(item => `<option value="${item.id}">${esc(item.name)} — ${esc(item.email)}</option>`).join('');
  } catch (error) {
    notify(`Filter options unavailable: ${error.message}`);
  }
}
function fillAnalyticsFilterValues() {
  const f = analyticsState.filters;
  const set = (selector, value) => { const element = document.querySelector(selector); if (element) element.value = value ?? ''; };
  set('#anStart', f.start_date); set('#anEnd', f.end_date); set('#anGranularity', f.granularity);
  set('#anPlan', f.plan_id); set('#anProduct', f.product_id); set('#anCustomer', f.customer_id);
}
async function loadAnalytics() {
  if (analyticsState.loading) return;
  analyticsState.loading = true;
  const loading = ['analyticsKpis', 'revenueTrend', 'mrrTrend', 'customerGrowth', 'customerSegments', 'subscriptionGrowth', 'churnPanel', 'revenueBreakdown', 'planPerformance', 'renewalsTable', 'forecastPanel'];
  loading.forEach(id => { const element = document.getElementById(id); if (element) element.innerHTML = '<div class="analytics-loading">Loading…</div>'; });
  const query = analyticsQuery();
  const calls = {
    overview: api(`/api/analytics/overview?${query}`),
    revenue: api(`/api/analytics/revenue?${query}`),
    subscriptions: api(`/api/analytics/subscriptions?${query}&days=${analyticsState.renewalDays}&limit=100`),
    customers: api(`/api/analytics/customers?${query}`),
    plans: api(`/api/analytics/plans?${query}&sort=${analyticsState.planSort}`),
    forecast: api(`/api/analytics/forecast?${query}`),
  };
  const entries = await Promise.all(Object.entries(calls).map(async ([key, promise]) => {
    try { return [key, await promise]; } catch (error) { return [key, { __error: error.message }]; }
  }));
  entries.forEach(([key, data]) => { analyticsState.data[key] = data; });
  const map = Object.fromEntries(entries);
  renderAnalyticsKpis(map.overview);
  renderRevenueAnalytics(map.revenue);
  if (map.revenue && !map.revenue.__error) renderRevenueBreakdown(map.revenue.breakdown || {});
  else renderRevenueBreakdown(map.revenue);
  renderCustomerAnalytics(map.customers);
  renderSubscriptionAnalytics(map.subscriptions);
  renderPlanPerformance(map.plans);
  renderForecast(map.forecast);
  analyticsState.loading = false;
}
function renderAnalyticsKpis(data) {
  const target = document.querySelector('#analyticsKpis');
  if (!target) return;
  if (!data || data.__error) { target.innerHTML = analyticsError(data?.__error); return; }
  const changes = data.changes || {};
  const growth = data.revenue_growth || {};
  const mrrNote = changes.mrr?.source === 'snapshot' ? 'vs captured previous month' : 'vs reconstructed previous month';
  const cards = [
    analyticsKpi('Monthly recurring revenue', currency(data.mrr), changes.mrr, 'Active subscriptions, quantity, variants and eligible recurring discounts, normalised to a month. Tax excluded.', mrrNote, 'purple'),
    analyticsKpi('Annual recurring revenue', currency(data.arr), changes.arr, 'MRR × 12. Tax excluded.', 'MRR × 12', 'blue'),
    analyticsKpi('Active subscriptions', Number(data.active_subscriptions || 0).toLocaleString('en-IN'), changes.active_subscriptions, 'Subscriptions currently in the active status.', 'Currently active', 'amber'),
    analyticsKpi('Active customers', Number(data.active_customers || 0).toLocaleString('en-IN'), changes.active_customers, 'Unique customers with at least one active subscription.', 'Unique customers', 'rose'),
    analyticsKpi('Average revenue per customer', currency(data.arpu), changes.arpu, 'MRR divided by active customers. Returns zero when there are no active customers.', 'MRR / active customers', 'purple'),
    analyticsKpi('Monthly churn rate', data.churn_rate === null || data.churn_rate === undefined ? 'Unavailable' : analyticsPct(data.churn_rate), changes.churn_rate, 'Customers lost during the latest month divided by customers active at the start of that month. Scheduled cancellations are not counted until they take effect.', data.reliability?.available ? 'Latest calendar month' : 'Historical timestamps unavailable', 'blue'),
    analyticsKpi('Renewal rate', data.renewal_rate?.available ? analyticsPct(data.renewal_rate.value) : 'Unavailable', null, data.renewal_rate?.reason || 'Not available.', 'Needs complete renewal history', 'amber'),
    analyticsKpi('Outstanding revenue', currency(data.outstanding_revenue), null, 'Unpaid non-cancelled invoice balances, including tax.', 'Gross unpaid invoices', 'rose'),
    analyticsKpi('Revenue growth', growth.percent_change === null || growth.percent_change === undefined ? '—' : analyticsPct(growth.percent_change), growth, 'Collected payments in the selected period compared with the immediately preceding equal-length period.', `${currency(growth.current_period_revenue || 0)} vs ${currency(growth.previous_period_revenue || 0)}`, 'purple'),
  ];
  target.innerHTML = cards.join('');
}
function renderRevenueAnalytics(data) {
  const trend = document.querySelector('#revenueTrend');
  if (!data || data.__error) {
    document.querySelector('#revenueTrend').innerHTML = analyticsError(data?.__error);
    document.querySelector('#mrrTrend').innerHTML = '';
    document.querySelector('#mrrMovementTable').innerHTML = '';
    return;
  }
  const series = data.series || [];
  document.querySelector('#revenueTotals').textContent = `${currency(data.total_collected)} collected · ${currency(data.total_invoiced)} invoiced`;
  document.querySelector('#revenueLegend').innerHTML = `<span><i class="legend-dot collected"></i>Collected payments</span><span><i class="legend-dot invoiced"></i>Invoiced value</span>`;
  drawAnalyticsLine('revenueTrend', series.map(item => item.label), [
    { name: 'Collected', color: '#236b5a', values: series.map(item => item.collected) },
    { name: 'Invoiced', color: '#7456e8', values: series.map(item => item.invoiced) },
  ], 'No collected or invoiced revenue in this range.');
  const growth = data.mrr_growth;
  if (!growth || growth.__error) {
    document.querySelector('#mrrTrend').innerHTML = analyticsError(growth?.__error);
    document.querySelector('#mrrMovementTable').innerHTML = '';
    return;
  }
  const rows = growth.series || [];
  drawAnalyticsLine('mrrTrend', rows.map(item => item.label), [
    { name: 'Previous MRR', color: '#8993a4', values: rows.map(item => item.previous_mrr) },
    { name: 'Ending MRR', color: '#236b5a', values: rows.map(item => item.ending_mrr) },
  ], 'No MRR history available.');
  document.querySelector('#mrrMovementTable').innerHTML = rows.length ? `<div class="table-wrap"><table class="analytics-table compact"><thead><tr><th>PERIOD</th><th>PREVIOUS</th><th>NEW</th><th>EXPANSION*</th><th>CONTRACTION*</th><th>CHURNED</th><th>ENDING</th></tr></thead><tbody>${rows.map(item => `<tr><td>${esc(item.label)} <span class="analytics-source">${item.reconstructed ? 'reconstructed' : 'snapshot'}</span></td><td>${currency(item.previous_mrr)}</td><td>${currency(item.new_mrr)}</td><td>${currency(item.expansion_mrr)}</td><td>${currency(item.contraction_mrr)}</td><td>${currency(item.churned_mrr)}</td><td><strong>${currency(item.ending_mrr)}</strong></td></tr>`).join('')}</tbody></table></div><p class="analytics-note">* Expansion and contraction are zero because the application does not store quantity or price change history. Reconstructed months use current prices and subscription start/closure dates.</p>` : analyticsEmpty('No MRR movement history available.');
}
function renderCustomerAnalytics(data) {
  if (!data || data.__error) {
    document.querySelector('#customerGrowth').innerHTML = analyticsError(data?.__error);
    document.querySelector('#customerSummary').innerHTML = '';
    document.querySelector('#customerSegments').innerHTML = '';
    return;
  }
  const series = data.new_customers_series || [];
  drawAnalyticsBars('customerGrowth', series.map(item => item.label), series.map(item => item.new_customers), '#236b5a', 'No customer acquisition data in this range.');
  const clv = data.clv || {};
  document.querySelector('#customerSummary').innerHTML = `<div class="analytics-stat-row">
    <div><span>Total registered</span><strong>${Number(data.total_registered_customers || 0).toLocaleString('en-IN')}</strong></div>
    <div><span>New in period</span><strong>${Number(data.new_customers_in_period || 0).toLocaleString('en-IN')}</strong></div>
    <div><span>Active customers</span><strong>${Number(data.active_customers || 0).toLocaleString('en-IN')}</strong></div>
    <div><span>No active subscription</span><strong>${Number(data.customers_without_active_subscriptions || 0).toLocaleString('en-IN')}</strong></div>
    <div><span>Estimated CLV</span><strong>${clv.available ? currency(clv.estimate) : 'Unavailable'}</strong><small>${esc(clv.available ? clv.methodology : clv.unavailable_reason || '')}</small></div>
  </div>`;
  const segmentation = data.segmentation || {};
  const segments = segmentation.segments || [];
  document.querySelector('#customerSegments').innerHTML = segments.map((item, index) => `<button class="analytics-segment" data-segment="${esc(item.key)}" data-label="${esc(item.label)}">
    <span class="segment-dot" style="background:${chartColors[index % chartColors.length]}"></span>
    <span class="segment-copy"><strong>${Number(item.count || 0).toLocaleString('en-IN')}</strong><small>${esc(item.label)}</small></span>
    <span class="segment-arrow">↗</span>
  </button>`).join('');
  document.querySelectorAll('.analytics-segment').forEach(button => {
    button.onclick = () => showCustomerSegment(button.dataset.segment, button.dataset.label);
  });
}
function renderSubscriptionAnalytics(data) {
  if (!data || data.__error) {
    document.querySelector('#statusDistribution').innerHTML = analyticsError(data?.__error);
    document.querySelector('#subscriptionGrowth').innerHTML = '';
    document.querySelector('#churnPanel').innerHTML = '';
    document.querySelector('#renewalsTable').innerHTML = '';
    return;
  }
  const distribution = data.status_distribution || [];
  const total = distribution.reduce((sum, item) => sum + item.count, 0);
  if (!total) {
    document.querySelector('#statusDistribution').innerHTML = analyticsEmpty('No subscriptions match these filters.');
  } else {
    let cursor = 0;
    const stops = distribution.map((item, index) => {
      const start = cursor;
      cursor += total ? item.count / total * 100 : 0;
      return `${chartColors[index % chartColors.length]} ${start}% ${cursor}%`;
    });
    document.querySelector('#statusDistribution').innerHTML = `<div class="status-donut-wrap"><div class="status-donut" style="background:conic-gradient(${stops.join(',')})"><span><strong>${total}</strong><small>Total</small></span></div><div class="status-legend">${distribution.map((item, index) => `<div><i style="background:${chartColors[index % chartColors.length]}"></i><span>${esc(statusLabels[item.status] || item.status)}</span><strong>${item.count}</strong><small>${total ? analyticsPct(item.count / total * 100) : '0%'}</small></div>`).join('')}</div></div>`;
  }
  const growth = data.growth || [];
  drawAnalyticsLine('subscriptionGrowth', growth.map(item => item.label), [
    { name: 'New', color: '#236b5a', values: growth.map(item => item.new_subscriptions) },
    { name: 'Renewed', color: '#4c8be4', values: growth.map(item => item.renewed_subscriptions) },
    { name: 'Cancelled', color: '#d86978', values: growth.map(item => item.cancelled_subscriptions) },
    { name: 'Closed', color: '#8993a4', values: growth.map(item => item.closed_subscriptions) },
  ], 'No subscription movement in this range.');
  document.querySelector('#subscriptionLegend').innerHTML = `<span><i class="legend-dot" style="background:#236b5a"></i>New</span><span><i class="legend-dot" style="background:#4c8be4"></i>Renewed</span><span><i class="legend-dot" style="background:#d86978"></i>Cancelled</span><span><i class="legend-dot" style="background:#8993a4"></i>Closed</span>`;
  const churn = data.churn || {};
  const reasons = churn.reasons || [];
  document.querySelector('#churnPanel').innerHTML = `<div class="analytics-metric-strip">
      <div><span>Customer churn</span><strong>${churn.customer_churn_rate === null ? 'Unavailable' : analyticsPct(churn.customer_churn_rate)}</strong></div>
      <div><span>Subscription churn</span><strong>${churn.subscription_churn_rate === null ? 'Unavailable' : analyticsPct(churn.subscription_churn_rate)}</strong></div>
      <div><span>Churned MRR</span><strong>${currency(churn.churned_mrr)}</strong></div>
      <div><span>Churned subscriptions</span><strong>${Number(churn.churned_subscriptions || 0).toLocaleString('en-IN')}</strong></div>
    </div>
    ${reasons.length ? `<table class="analytics-table compact"><thead><tr><th>REASON</th><th>COUNT</th><th>LIST VALUE</th></tr></thead><tbody>${reasons.map(item => `<tr><td>${esc(reasonLabels[item.reason] || item.reason)}</td><td>${item.count}</td><td>${currency(item.list_value)}</td></tr>`).join('')}</tbody></table>` : analyticsEmpty('No cancellation reasons are recorded for this month.')}
    <p class="analytics-note">${esc(churn.definition || '')} ${churn.available === false ? 'Closure timestamps are not available for historical rows.' : ''}</p>`;
  renderRenewals(data);
}
function renderRenewals(data) {
  const target = document.querySelector('#renewalsTable');
  if (!target) return;
  const renewals = data.upcoming_renewals || {};
  const items = renewals.items || [];
  target.innerHTML = `<div class="analytics-table-caption">${renewals.count || 0} renewal(s) within ${renewals.days || analyticsState.renewalDays} days · ${currency(renewals.amount || 0)} recurring value</div>${items.length ? `<div class="table-wrap"><table class="analytics-table"><thead><tr><th>NUMBER</th><th>CUSTOMER</th><th>PLAN</th><th>RENEWAL DATE</th><th>AMOUNT</th><th>STATUS</th></tr></thead><tbody>${items.map(item => `<tr><td>${esc(item.number || '—')}</td><td><strong>${esc(item.customer_name)}</strong><small>${esc(item.customer_email)}</small></td><td>${esc(item.plan)}</td><td>${new Date(item.renewal_date).toLocaleDateString('en-IN')}</td><td>${currency(item.amount)}</td><td><span class="status-pill ${item.scheduled_cancellation ? 'pending' : 'paid'}">${item.scheduled_cancellation ? 'Cancels at period end' : 'Due'}</span></td></tr>`).join('')}</tbody></table></div>` : analyticsEmpty(`No subscriptions are due within ${renewals.days || analyticsState.renewalDays} days.`)}`;
}
function renderPlanPerformance(data) {
  const target = document.querySelector('#planPerformance');
  if (!target) return;
  if (!data || data.__error) { target.innerHTML = analyticsError(data?.__error); return; }
  const plans = data.plans || [];
  if (!plans.length) { target.innerHTML = analyticsEmpty('No plans match these filters.'); return; }
  target.innerHTML = `<div class="analytics-highlight-row">
      <div><span class="analytics-mini-label">Best performing</span>${(data.best_performing || []).map(item => `<strong>${esc(item.plan)}</strong><small>${currency(item.mrr)} MRR · ${item.active_subscribers} active</small>`).join('') || '<small>No active plan revenue yet.</small>'}</div>
      <div><span class="analytics-mini-label">Needs attention</span>${(data.needs_attention || []).map(item => `<strong>${esc(item.plan)}</strong><small>${item.cancelled_subscriptions} cancellation(s) · ${item.new_subscriptions} new</small>`).join('') || '<small>No cancellations in this period.</small>'}</div>
    </div>
    <div class="table-wrap"><table class="analytics-table"><thead><tr><th>PLAN</th><th>TOTAL</th><th>ACTIVE</th><th>NEW</th><th>CANCELLED</th><th>MRR</th><th>ARR</th><th>AVG QTY</th><th>REVENUE SHARE</th></tr></thead><tbody>${plans.map(item => `<tr><td><strong>${esc(item.plan)}</strong><small>${esc(item.interval)}</small></td><td>${item.total_subscribers}</td><td>${item.active_subscribers}</td><td>${item.new_subscriptions}</td><td>${item.cancelled_subscriptions}</td><td>${currency(item.mrr)}</td><td>${currency(item.arr)}</td><td>${Number(item.average_quantity || 0).toFixed(2)}</td><td><div class="analytics-share"><i style="width:${Math.min(100, Number(item.revenue_contribution_percent || 0))}%"></i><span>${analyticsPct(item.revenue_contribution_percent)}</span></div></td></tr>`).join('')}</tbody></table></div><p class="analytics-note">Revenue share is each plan's active MRR divided by total active MRR. Subscriber and cancellation counts use the selected period for events and current status for active totals.</p>`;
}
function renderForecast(data) {
  const target = document.querySelector('#forecastPanel');
  if (!target) return;
  if (!data || data.__error) { target.innerHTML = analyticsError(data?.__error); return; }
  const months = data.months || [];
  const scheduled = data.upcoming_scheduled_cancellations || [];
  const closures = data.upcoming_plan_closures || [];
  target.innerHTML = `<div class="analytics-metric-strip four">
      <div><span>Next 3 months</span><strong>${currency(data.next_3_month_total)}</strong><small>forecast recurring revenue</small></div>
      <div><span>Next 6 months</span><strong>${currency(data.next_6_month_total)}</strong><small>forecast recurring revenue</small></div>
      <div><span>Expected monthly revenue</span><strong>${currency(data.expected_recurring_revenue)}</strong><small>current month schedule</small></div>
      <div><span>Expected invoice value</span><strong>${currency(data.expected_invoice_value)}</strong><small>6 months, excluding tax</small></div>
    </div>
    <div class="table-wrap"><table class="analytics-table"><thead><tr><th>MONTH</th><th>FORECAST RECURRING</th><th>EXPECTED INVOICES</th><th>ACTIVE SUBS</th><th>SCHEDULED CANCELLATIONS</th><th>PLAN CLOSURES</th></tr></thead><tbody>${months.map(item => `<tr><td><strong>${esc(item.label)}</strong></td><td>${currency(item.forecasted_recurring_revenue)}</td><td>${currency(item.expected_invoice_value)}</td><td>${item.active_subscriptions}</td><td>${item.scheduled_cancellations}</td><td>${item.plan_closures}</td></tr>`).join('')}</tbody></table></div>
    <div class="analytics-grid-two analytics-forecast-lists">
      <div><h3>Upcoming scheduled cancellations</h3>${scheduled.length ? scheduled.map(item => `<div class="analytics-list-row"><span><strong>${esc(item.customer)}</strong><small>${esc(item.plan)} · ${esc(reasonLabels[item.reason] || item.reason)}</small></span><span>${new Date(item.ends_at).toLocaleDateString('en-IN')}</span></div>`).join('') : analyticsEmpty('No scheduled cancellations in the next six months.')}</div>
      <div><h3>Plan closures</h3>${closures.length ? closures.map(item => `<div class="analytics-list-row"><span><strong>${esc(item.plan)}</strong><small>Plan auto-close</small></span><span>${new Date(item.closes_at).toLocaleDateString('en-IN')}</span></div>`).join('') : analyticsEmpty('No plan closures scheduled in the next six months.')}</div>
    </div>
    <p class="analytics-note">${esc(data.methodology || '')} Forecasted recurring revenue, expected invoice value and actual collected revenue are separate measures.</p>`;
}
function renderRevenueBreakdown(data) {
  if (!data || data.__error) { document.querySelector('#revenueBreakdown').innerHTML = analyticsError(data?.__error); return; }
  const groups = [
    ['By product', data.by_product || []],
    ['By plan', data.by_plan || []],
    ['By payment method', data.by_payment_method || []],
    ['By current customer segment', data.by_customer_segment || []],
  ];
  document.querySelector('#revenueBreakdown').innerHTML = `<div class="analytics-breakdown-grid">${groups.map(([title, items]) => `<div><h3>${esc(title)}</h3>${items.length ? items.map(item => `<div class="analytics-breakdown-row"><div><span>${esc(item.label)}</span><strong>${currency(item.amount)}</strong></div><div class="analytics-share"><i style="width:${Math.min(100, Number(item.percentage || 0))}%"></i><span>${analyticsPct(item.percentage)}</span></div></div>`).join('') : analyticsEmpty('No collected revenue.')}</div>`).join('')}</div>`;
}
async function showCustomerSegment(segment, label) {
  const dialogElement = document.querySelector('#actionDialog');
  dialogElement.innerHTML = `<div class="segment-dialog"><button class="dialog-close" type="button" aria-label="Close">×</button><p class="eyebrow">CUSTOMER SEGMENT</p><h2>${esc(label)}</h2><div id="segmentRows"><div class="analytics-loading">Loading customers…</div></div></div>`;
  dialogElement.showModal();
  dialogElement.querySelector('.dialog-close').onclick = () => dialogElement.close();
  try {
    const data = await api(`/api/analytics/customer-segments/${encodeURIComponent(segment)}?${analyticsQuery({ limit: 100 })}`);
    const items = data.items || [];
    document.querySelector('#segmentRows').innerHTML = `<p class="analytics-note">${data.count} customer(s) in this segment.</p>${items.length ? `<div class="table-wrap"><table class="analytics-table"><thead><tr><th>CUSTOMER</th><th>MRR</th><th>FLAGS</th></tr></thead><tbody>${items.map(item => `<tr><td><strong>${esc(item.name)}</strong><small>${esc(item.email)}</small></td><td>${currency(item.mrr)}</td><td>${[item.overdue ? 'Overdue' : '', item.ending_soon ? 'Ending soon' : '', item.scheduled_cancellation ? 'Scheduled cancellation' : ''].filter(Boolean).map(flag => `<span class="status-pill active">${esc(flag)}</span>`).join(' ') || '—'}</td></tr>`).join('')}</tbody></table></div>` : analyticsEmpty('No customers in this segment.')}`;
  } catch (error) {
    document.querySelector('#segmentRows').innerHTML = analyticsError(error.message);
  }
}

loadWorkspace();
