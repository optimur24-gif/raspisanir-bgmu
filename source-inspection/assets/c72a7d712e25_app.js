import {compactDetails,lectureDetails} from './details.js?v=16';
import {shortPlace} from './place.js?v=15';
import {DAY_NAMES,DAY_SHORT,escapeHTML as esc,icon,validDate,addDays,monday,weekDates,formatDate,todayUfa,academicWeek,normalizeSearch,filterEvents,kindClass,eventState,plural} from './core.js';

const config=JSON.parse(document.getElementById('app-config').textContent);
const $=id=>document.getElementById(id);
const LABELS={group:'Группы',teacher:'Преподаватели',room:'Аудитории'};
const ENTITY_LABELS={group:'РАСПИСАНИЕ ГРУППЫ',teacher:'РАСПИСАНИЕ ПРЕПОДАВАТЕЛЯ',room:'РАСПИСАНИЕ АУДИТОРИИ'};
const stored=(key,fallback)=>{try{return JSON.parse(localStorage.getItem('bgmu:'+key))??fallback;}catch{return fallback;}};
const save=(key,value)=>{try{localStorage.setItem('bgmu:'+key,JSON.stringify(value));}catch{/* Private browsing can deny storage. */}};
const validType=type=>['group','teacher','room'].includes(type);
const validId=id=>/^\d{1,9}$/.test(String(id??''))&&Number(id)>0;
const validFavorites=value=>Array.isArray(value)?value.filter(f=>f&&validType(f.type)&&validId(f.id)&&typeof f.name==='string'&&Number.isInteger(f.year)).slice(0,50):[];
const state={searchType:validType(stored('searchType','group'))?stored('searchType','group'):'group',year:Number(config.defaultYear),semester:Number(config.defaultSemester),selected:null,
  date:todayUfa(),view:stored('view','day')==='week'?'week':'day',subgroup:'',catalog:[],events:[],response:null,
  favorites:validFavorites(stored('favorites',[])),recent:validFavorites(stored('recent',[])).slice(0,9),catalogLimit:100,loading:false,scheduleError:'',catalogEpoch:0,scheduleEpoch:0};
const catalogs=new Map();let scheduleAbort=null;let toastTimer;let lastFocus=null;
const keyOf=entity=>`${entity.type}:${entity.id}:${entity.year??state.year}`;
const TYPE_NAMES={group:'Группа',teacher:'Преподаватель',room:'Аудитория'};
function preferenceKey(){return `${keyOf(state.selected)}:${state.semester}`;}
function rememberSelection(){
  if(!state.selected)return;
  const item={...state.selected,semester:state.semester};
  save('last',item);
  state.recent=[item,...state.recent.filter(f=>keyOf(f)!==keyOf(item))].slice(0,9);
  save('recent',state.recent);renderQuickAccess();
}
function renderQuickAccess(){
  const items=state.favorites.filter(f=>f.year===state.year);
  const unique=items.filter((f,i)=>items.findIndex(x=>keyOf(x)===keyOf(f))===i);
  $('quick-access').innerHTML=unique.length?unique.map(f=>{
    const active=state.selected&&keyOf(f)===keyOf(state.selected);
    const favorite=state.favorites.some(x=>keyOf(x)===keyOf(f));
    return `<button type="button" class="quick-card${active?' active':''}" data-quick="${esc(keyOf(f))}" aria-pressed="${Boolean(active)}"><span class="quick-icon">${icon(f.type)}</span><span class="quick-copy"><small>${TYPE_NAMES[f.type]}${favorite?' · ★':''}</small><strong>${esc(f.name)}</strong></span>${active?'<span class="quick-check" aria-hidden="true">✓</span>':''}</button>`;
  }).join(''):'<div class="quick-empty">Добавьте группу, преподавателя или аудиторию в избранное — нажмите на звёздочку рядом с названием расписания.</div>';
}
async function openSaved(item){
  closePersonal();
  state.searchType=item.type;$('search').value='';
  const loading=loadCatalog();await choose(item,item.type);await loading;
}

function searchText(type){const searches=stored('searches',{});return typeof searches?.[type]==='string'?searches[type].slice(0,100):'';}
function rememberSearch(){
  save('searchType',state.searchType);
  const raw=stored('searches',{});const searches=raw&&typeof raw==='object'&&!Array.isArray(raw)?raw:{};
  searches[state.searchType]=$('search').value.slice(0,100);save('searches',searches);
}
function hydrateIcons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
hydrateIcons();
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
function mobileNavigation(active='schedule'){
  for(const name of ['schedule','search','favorites']){
    const button=$('nav-'+name);
    if(name===active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  }
}
$('nav-search').addEventListener('click',()=>drawer(true));
$('nav-favorites').addEventListener('click',()=>$('open-personal').click());
$('nav-schedule').addEventListener('click',()=>{closePersonal();drawer(false);mobileNavigation();$('schedule').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'start'});});
function closePersonal(){
  if($('personal-dialog').open)$('personal-dialog').close();
}
function syncPersonal(){
  closePersonal();
}
$('open-personal').addEventListener('click',()=>{
  if($('search-panel').classList.contains('open'))drawer(false);
  if(!$('personal-dialog').open){mobileNavigation('favorites');$('personal-dialog').showModal();document.body.classList.add('favorites-open');$('open-personal').setAttribute('aria-expanded','true');}
});
$('close-personal').addEventListener('click',closePersonal);
$('personal-dialog').addEventListener('close',()=>{if(!$('search-panel').classList.contains('open'))mobileNavigation();document.body.classList.remove('favorites-open');$('open-personal').setAttribute('aria-expanded','false');});
$('personal-dialog').addEventListener('click',e=>{
  if(e.target!==$('personal-dialog'))return;
  const r=$('personal-dialog').getBoundingClientRect();
  if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closePersonal();
});
matchMedia('(max-width:850px)').addEventListener('change',syncPersonal);
syncPersonal();
$('search-panel').inert=matchMedia('(max-width:850px)').matches;
matchMedia('(max-width:850px)').addEventListener('change',e=>{$('search-panel').inert=e.matches&&!$('search-panel').classList.contains('open');});
function drawer(open){
  if(open)closePersonal();
  const panel=$('search-panel');
  mobileNavigation(open?'search':'schedule');
  if(!matchMedia('(max-width:850px)').matches){panel.inert=false;if(open)$('search').focus();return;}
  panel.inert=!open;
  if(open){lastFocus=document.activeElement;panel.classList.add('open');panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');$('drawer-backdrop').hidden=false;document.body.classList.add('drawer-open');$('open-search').setAttribute('aria-expanded','true');$('search').focus();}
  else{panel.classList.remove('open');panel.removeAttribute('role');panel.removeAttribute('aria-modal');$('drawer-backdrop').hidden=true;document.body.classList.remove('drawer-open');$('open-search').setAttribute('aria-expanded','false');if(lastFocus?.isConnected)lastFocus.focus();}
}
async function api(params,signal){
  const url=new URL('api.php',location.href);url.search=new URLSearchParams(params);
  let response;
  try{response=await fetch(url,{signal,headers:{Accept:'application/json'},cache:'no-store'});}catch(e){if(e.name==='AbortError')throw e;throw new Error('Нет соединения. Проверьте интернет и попробуйте снова.');}
  let data;try{data=await response.json();}catch{throw new Error('Сервер не вернул данные расписания. Проверьте установку PHP на хостинге.');}
  if(!response.ok||!data.success)throw new Error(data.message||'Не удалось загрузить расписание.');return data;
}
function updateURL(push=false){
  const url=new URL(location.href);url.search='';
  if(state.selected){url.searchParams.set('type',state.selected.type);url.searchParams.set('id',state.selected.id);}
  url.searchParams.set('year',state.year);url.searchParams.set('semester',state.semester);url.searchParams.set('date',state.date);url.searchParams.set('view',state.view);
  if(state.subgroup)url.searchParams.set('subgroup',state.subgroup);
  history[push?'pushState':'replaceState'](null,'',url);
}
function fillPeriodControls(){
  $('year').replaceChildren(...Object.entries(config.years).map(([value,label])=>new Option(label,value)));
  $('year').value=String(state.year);$('semester').value=String(state.semester);
}
function readURL(useSaved=false){
  const params=new URLSearchParams(location.search);
  const saved=stored('last',null);
  const last=useSaved&&!params.has('id')&&!params.has('type')&&saved&&validType(saved.type)&&validId(saved.id)?saved:null;
  const year=Number(params.get('year')||last?.year||config.defaultYear);
  state.year=Object.hasOwn(config.years,year)?year:Number(config.defaultYear);
  state.semester=['1','2'].includes(params.get('semester'))?Number(params.get('semester')):[1,2].includes(last?.semester)?last.semester:Number(config.defaultSemester);
  const date=params.get('date');state.date=validDate(date)?date:todayUfa();
  state.view=['day','week'].includes(params.get('view'))?params.get('view'):state.view;
  state.subgroup=(params.get('subgroup')||'').slice(0,24);
  const type=params.get('type')||last?.type;const id=params.get('id')||last?.id;
  state.selected=validType(type)&&validId(id)?{type,id:Number(id),name:last?.name||'Загрузка…',year:state.year}:null;
  state.searchType=useSaved&&validType(stored('searchType',null))?stored('searchType',null):validType(params.get('type'))?params.get('type'):validType(stored('searchType',null))?stored('searchType',null):state.selected?.type||'group';
  $('search').value=searchText(state.searchType);
  if(state.selected&&!params.has('subgroup')){const prefs=stored('subgroups',{});state.subgroup=typeof prefs?.[preferenceKey()]==='string'?prefs[preferenceKey()]:'';}
  fillPeriodControls();
}
function renderFavorites(){
  renderQuickAccess();
  const values=state.favorites.filter(f=>f.year===state.year);
  $('favorites-section').hidden=values.length===0;
  $('favorites').innerHTML=values.map(f=>`<button class="favorite-chip" type="button" data-favorite="${esc(keyOf(f))}" title="${esc(f.name)}">${TYPE_NAMES[f.type]} · ${esc(f.name)}</button>`).join('');
  if(state.selected){const active=state.favorites.some(f=>keyOf(f)===keyOf(state.selected));$('favorite-toggle').setAttribute('aria-pressed',String(active));$('favorite-toggle').setAttribute('aria-label',active?'Убрать из избранного':'Добавить в избранное');}
}
function searchDescription(item){return [item.name,item.subtitle,item.direction,item.faculty,item.building,item.organization].filter(Boolean).join(' ');}
function renderCatalog(){
  const query=normalizeSearch($('search').value);const faculty=$('faculty').value;const course=$('course').value;
  const found=state.catalog.filter(item=>(!query||normalizeSearch(searchDescription(item)).includes(query))
    &&(state.searchType!=='group'||(!faculty||item.faculty===faculty)&&(!course||String(item.course)===course)));
  $('catalog-count').textContent=String(found.length);
  $('catalog-status').hidden=found.length>0;
  if(!found.length){$('catalog-status').classList.remove('error');$('catalog-status').textContent=state.catalog.length?'Ничего не найдено. Попробуйте другое название или сбросьте фильтры.':'В этом семестре список пуст.';}
  $('catalog').innerHTML=found.slice(0,state.catalogLimit).map(item=>`<button type="button" class="catalog-item${state.selected?.type===state.searchType&&state.selected.id===item.id?' selected':''}" data-entity-id="${item.id}"${state.selected?.type===state.searchType&&state.selected.id===item.id?' aria-current="true"':''}><span class="item-mark">${icon(state.searchType)}</span><span class="item-copy"><strong>${esc(item.name)}</strong><small>${esc(item.subtitle)}</small></span></button>`).join('');
  $('more-results').hidden=found.length<=state.catalogLimit;
  $('more-results').textContent=`Показать ещё · осталось ${Math.max(0,found.length-state.catalogLimit)}`;
}
async function loadCatalog(){
  save('searchType',state.searchType);
  const epoch=++state.catalogEpoch;const type=state.searchType;const year=state.year;const semester=state.semester;
  document.querySelectorAll('[data-type]').forEach(button=>{const active=button.dataset.type===type;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
  $('catalog-panel').setAttribute('aria-labelledby','tab-'+type);$('catalog-label').textContent=LABELS[type];
  $('search').placeholder=type==='group'?'Например, Л-101':type==='teacher'?'Фамилия преподавателя':'Аудитория или корпус';
  $('search-hint').textContent=type==='group'?'Введите номер группы или выберите её из списка.':type==='teacher'?'Введите фамилию преподавателя.':'Введите номер аудитории или название корпуса.';
  $('group-filters').hidden=type!=='group';$('catalog').replaceChildren();$('catalog-count').textContent='';$('more-results').hidden=true;
  $('catalog-status').hidden=false;$('catalog-status').classList.remove('error');$('catalog-status').textContent='Загружаем список…';
  try{
    const key=`${type}:${year}:${semester}`;
    if(!catalogs.has(key))catalogs.set(key,await api({action:'catalog',type,year,semester}));
    if(epoch!==state.catalogEpoch)return;
    const result=catalogs.get(key);state.catalog=result.items.slice().sort((a,b)=>a.name.localeCompare(b.name,'ru',{numeric:true}));
    $('faculty').replaceChildren(new Option('Все факультеты',''),...[...new Set(state.catalog.map(i=>i.faculty).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru')).map(f=>new Option(f,f)));
    $('course').replaceChildren(new Option('Все курсы',''),...[...new Set(state.catalog.map(i=>i.course).filter(Boolean))].sort((a,b)=>a-b).map(c=>new Option(c+' курс',String(c))));
    state.catalogLimit=100;renderCatalog();renderFavorites();
    if(result.meta?.stale)toast('Список получен из сохранённой копии.');
  }catch(e){if(epoch!==state.catalogEpoch)return;state.catalog=[];$('catalog-status').hidden=false;$('catalog-status').classList.add('error');$('catalog-status').textContent=e.message;$('catalog-status').append(document.createElement('br'));const retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='Повторить';retry.addEventListener('click',loadCatalog);$('catalog-status').append(retry);}
}
function renderHeading(){
  if(!state.selected)return;
  $('welcome').hidden=true;$('schedule-shell').hidden=false;$('entity-type').textContent=ENTITY_LABELS[state.selected.type];
  $('entity-name').textContent=state.selected.name;
  $('entity-subtitle').textContent=state.selected.subtitle||'Загружаем расписание из ИСУ БГМУ…';
  $('source-link').href=state.response?.sourceUrl||'https://edu-isu.bashgmu.ru/ru/schedule/view/';
  document.title=`${state.selected.name} — ${config.title}`;renderFavorites();
}
function renderSubgroups(){
  const groups=state.selected?.type==='group'?[...new Set(state.events.flatMap(e=>e.details.map(d=>d.subgroup)).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru')):[];
  $('subgroup-control').hidden=!groups.length;
  $('subgroup').replaceChildren(new Option('Все подгруппы',''),...groups.map(g=>new Option('Подгруппа '+g,g)));
  if(!groups.includes(state.subgroup))state.subgroup='';$('subgroup').value=state.subgroup;
}
async function loadSchedule(quiet=false){
  if(!state.selected)return;
  scheduleAbort?.abort();scheduleAbort=new AbortController();const epoch=++state.scheduleEpoch;
  const selected={...state.selected};const year=state.year;const semester=state.semester;
  state.loading=true;state.scheduleError='';
  if(!quiet){state.events=[];state.response=null;$('subgroup-control').hidden=true;renderHeading();renderSchedule();}
  $('refresh').disabled=true;$('events').setAttribute('aria-busy','true');
  try{
    const data=await api({action:'schedule',type:selected.type,id:selected.id,year,semester},scheduleAbort.signal);
    if(epoch!==state.scheduleEpoch)return;
    state.response=data;state.events=data.events;state.selected={...data.entity,type:selected.type,year};state.loading=false;
    renderSubgroups();renderHeading();renderSchedule();renderCatalog();
    rememberSelection();updateURL();
  }catch(e){if(e.name==='AbortError'||epoch!==state.scheduleEpoch)return;state.loading=false;state.scheduleError=e.message;
    if(quiet&&state.response){$('notice').textContent='Не удалось проверить изменения. На экране данные от '+formatUpdate(state.response.meta.updatedAt)+'.';$('notice').hidden=false;}
    else renderSchedule();
  }finally{if(epoch===state.scheduleEpoch){state.loading=false;$('refresh').disabled=false;$('events').setAttribute('aria-busy','false');}}
}
function formatUpdate(iso){
  const d=new Date(iso);return Number.isNaN(+d)?'неизвестного времени':new Intl.DateTimeFormat('ru-RU',{timeZone:config.timezone,day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d);
}
function renderDays(events){
  const dates=weekDates(state.date).slice(0,6);const today=todayUfa();
  $('day-tabs').innerHTML=dates.map((date,i)=>`<button type="button" class="day-tab${date===today?' is-today':''}" aria-pressed="${date===state.date}" aria-label="${esc(DAY_NAMES[i]+', '+formatDate(date,{day:'numeric',month:'long'}))}" data-date="${date}"><span class="weekday">${DAY_SHORT[i]}</span><span class="day-number">${Number(date.slice(-2))}</span>${events.some(e=>e.date===date)?'<span class="day-dot" aria-label="Есть занятия"></span>':'<span class="day-dot" hidden></span>'}</button>`).join('');
  const yearLabel=String(config.years[state.year]||'');
  const academicStart=/^20\d{2}\//.test(yearLabel)?yearLabel.slice(0,4)+'-09-01':config.academicStart;
  const week=academicWeek(state.date,academicStart);$('week-number').textContent=week>0?`${week} неделя`:'Учебная неделя';
  $('week-range').textContent=formatDate(dates[0],{day:'numeric',month:'short'})+' — '+formatDate(dates.at(-1),{day:'numeric',month:'short'});
  $('date-picker').value=state.date;$('day-view').setAttribute('aria-pressed',String(state.view==='day'));$('week-view').setAttribute('aria-pressed',String(state.view==='week'));
}
function lessonCard(event){
  const activity=eventState(event);const nowLabel=activity==='is-current'?'<span class="time-current">Сейчас идёт</span>':'';
  const time=event.start?`<strong>${esc(event.start)}</strong>${event.end?`<span>${esc(event.end)}</span>`:''}`:'<strong>Время<br>уточняется</strong>';
  const mergedDetails=compactDetails(event.details);
  const headerGroups=new Map();
  for(const d of mergedDetails){
    const name=d.group||event.groups?.find(g=>g.id===d.groupId)?.name||(state.selected.type==='group'?state.selected.name:'');
    if(!name)continue;
    const id=d.groupId||event.groups?.find(g=>g.name===name)?.id||(state.selected.type==='group'?state.selected.id:0);
    const key=String(id||name);
    if(!headerGroups.has(key))headerGroups.set(key,{name,id,subgroups:new Set(),whole:false});
    const group=headerGroups.get(key);
    if(d.wholeGroup||!d.subgroup)group.whole=true;
    for(const subgroup of d.subgroups||[])group.subgroups.add(subgroup);
  }
  for(const g of event.groups||[])if(![...headerGroups.values()].some(x=>x.id===g.id||x.name===g.name))headerGroups.set(String(g.id),{...g,subgroups:new Set(),whole:true});
  const topGroups=[...headerGroups.values()].map(g=>{
    const subs=g.whole?'':[...g.subgroups].sort((a,b)=>a.localeCompare(b,'ru',{numeric:true})).join(', ');
    const label=[g.name,subs].filter(Boolean).join(' ');
    return state.selected.type!=='group'&&g.id?`<button type="button" class="subgroup-badge lesson-group-badge" data-event-group="${Number(g.id)}">${esc(label)}</button>`:`<span class="subgroup-badge lesson-group-badge">${esc(label)}</span>`;
  }).join('');
  const isLecture=kindClass(event.kind)==='kind-lecture';
  const visibleDetails=isLecture?lectureDetails(mergedDetails):mergedDetails;
  const details=visibleDetails.map(d=>{
    const groupName=d.group||event.groups?.find(g=>g.id===d.groupId)?.name||'';
    const groupLabel=state.selected.type==='group'?d.subgroup:[groupName,d.subgroup].filter(Boolean).join(' ');
    const groupId=d.groupId||event.groups?.find(g=>g.name===groupName)?.id;
    const subgroup=!isLecture&&mergedDetails.length>1&&groupLabel?(state.selected.type!=='group'&&groupId?`<button type="button" class="subgroup-badge group-badge-link" data-event-group="${Number(groupId)}">${esc(groupLabel)}</button>`:`<span class="subgroup-badge">${esc(groupLabel)}</span>`):'';
    const teacher=d.teacherId?`<button type="button" class="detail-link" data-detail-type="teacher" data-detail-id="${Number(d.teacherId)}">${esc(d.teacher||'Преподаватель')}</button>`:`<span>${esc(d.teacher||'Преподаватель уточняется')}</span>`;
    const roomFull=[d.room,d.building].filter(Boolean).filter((v,i,a)=>!a.some((x,j)=>j!==i&&x.includes(v)&&x!==v)).filter((v,i,a)=>a.indexOf(v)===i).join(', ');
    const place=shortPlace(d.room,d.building);
    const room=d.roomId?`<button type="button" class="detail-link" data-detail-type="room" data-detail-id="${Number(d.roomId)}" title="${esc(roomFull)}">${esc(place)}</button>`:`<span title="${esc(roomFull)}">${esc(place)}</span>`;
    return `<div class="detail-row">${subgroup}<span class="detail-line">${icon('teacher')}${teacher}</span><span class="detail-line room-line">${icon('room')}${room}</span></div>`;
  }).join('');
  const groups='';
  return `<article class="lesson-row ${activity}"><div class="lesson-time">${time}${nowLabel}</div><div class="lesson-card ${kindClass(event.kind)}"><div class="lesson-topline"><span class="lesson-kind">${esc(event.kind||'Занятие')}</span><div class="lesson-top-groups">${topGroups}</div>${event.cycle?'<span class="cycle-tag">Цикл</span>':''}</div><h3>${esc(event.subject)}</h3><div class="lesson-details">${details}</div>${groups}${event.comment?`<p class="lesson-comment">${esc(event.comment)}</p>`:''}</div></article>`;
}
const emptyDay=()=>`<div class="empty-day">${icon('coffee')}<h3>Занятий не найдено</h3><p>На выбранную дату в источнике нет занятий${state.subgroup?' для этой подгруппы':''}.</p></div>`;
function renderSchedule(){
  if(!state.selected)return;
  const events=filterEvents(state.events,state.subgroup);renderDays(events);
  const summaryEvents=events.filter(e=>state.view==='week'?weekDates(state.date).slice(0,6).includes(e.date):e.date===state.date);
  const first=summaryEvents[0],last=summaryEvents.at(-1);
  $('mobile-lesson-summary').textContent=state.loading?'Загружаем занятия…':state.scheduleError&&!state.response?'Не удалось загрузить занятия':`${summaryEvents.length} ${plural(summaryEvents.length,['занятие','занятия','занятий'])}${state.view==='week'?' на неделю':''}${state.view==='day'&&first?.start?' · '+first.start+(last?.end?' – '+last.end:''):''}`;
  $('notice').hidden=true;$('schedule-status').hidden=true;$('events').setAttribute('aria-busy',String(state.loading));
  if(state.loading){$('events').innerHTML='<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';$('freshness').textContent='Загружаем расписание…';return;}
  if(state.scheduleError&&!state.response){$('events').replaceChildren();$('schedule-status').hidden=false;$('schedule-status').classList.add('error');$('schedule-status').textContent=state.scheduleError;$('freshness').textContent='Не удалось загрузить';return;}
  $('schedule-status').classList.remove('error');
  if(state.response){
    const meta=state.response.meta;const fullUpdate=formatUpdate(meta.updatedAt);$('freshness').title=(meta.stale?'Сохранено ':'Обновлено ')+fullUpdate+' · Уфа';$('freshness').textContent=(meta.stale?'Сохранено ':'Обновлено ')+(matchMedia('(max-width:850px)').matches?(fullUpdate.match(/\d{2}:\d{2}/)?.[0]||fullUpdate):fullUpdate)+' · Уфа';
    const warnings=[meta.warning,state.scheduleError?'Не удалось проверить изменения. На экране данные от '+formatUpdate(meta.updatedAt)+'.':'',state.response.undatedRecords?'В источнике есть записи без точной даты. Проверьте их в ИСУ БГМУ.':''].filter(Boolean);
    if(warnings.length){$('notice').textContent=warnings.join(' ');$('notice').hidden=false;}
  }
  if(state.view==='day'){
    const found=events.filter(e=>e.date===state.date);const index=(new Date(state.date+'T12:00:00Z').getUTCDay()+6)%7;
    $('events').innerHTML=`<section class="day-events"><div class="day-title-row"><h2>${DAY_NAMES[index]}, ${esc(formatDate(state.date,{day:'numeric',month:'long'}))}</h2><span>${found.length} ${plural(found.length,['занятие','занятия','занятий'])}</span></div>${found.length?found.map(lessonCard).join(''):emptyDay()}</section>`;
  }else{
    $('events').innerHTML=`<div class="week-list">${weekDates(state.date).slice(0,6).map((date,i)=>{const found=events.filter(e=>e.date===date);return `<section class="day-events week-section"><div class="day-title-row"><h2>${DAY_NAMES[i]}, ${formatDate(date,{day:'numeric',month:'long'})}</h2><span>${found.length} ${plural(found.length,['занятие','занятия','занятий'])}</span></div>${found.length?found.map(lessonCard).join(''):emptyDay()}</section>`;}).join('')}</div>`;
  }
  
}
async function choose(item,type=state.searchType,push=true){
  state.selected={...item,type,year:state.year};
  const prefs=stored('subgroups',{});state.subgroup=typeof prefs?.[preferenceKey()]==='string'?prefs[preferenceKey()]:'';
  state.events=[];state.response=null;state.scheduleError='';rememberSelection();
  updateURL(push);drawer(false);renderHeading();await loadSchedule();
}
async function changeType(type){if(!validType(type))return;rememberSearch();state.searchType=type;$('search').value=searchText(type);save('searchType',type);await loadCatalog();}
function setDate(date,push=false){if(!validDate(date))return;state.date=date;updateURL(push);renderSchedule();}
function setView(view){state.view=view;save('view',view);updateURL();renderSchedule();}

$('quick-access').addEventListener('click',e=>{const button=e.target.closest('[data-quick]');if(!button)return;const item=state.favorites.find(f=>keyOf(f)===button.dataset.quick);if(item)openSaved(item);});
$('open-search').addEventListener('click',()=>drawer(true));$('close-search').addEventListener('click',()=>drawer(false));$('drawer-backdrop').addEventListener('click',()=>drawer(false));$('welcome-search').addEventListener('click',()=>drawer(true));
document.querySelector('.entity-tabs').addEventListener('click',e=>{const button=e.target.closest('[data-type]');if(button)changeType(button.dataset.type);});
document.querySelector('.entity-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const types=['group','teacher','room'];let index=types.indexOf(state.searchType);index=e.key==='Home'?0:e.key==='End'?2:(index+(e.key==='ArrowRight'?1:2))%3;changeType(types[index]);$('tab-'+types[index]).focus();});
$('search').addEventListener('input',()=>{rememberSearch();state.catalogLimit=100;renderCatalog();});
for(const name of ['faculty','course'])$(name).addEventListener('change',()=>{state.catalogLimit=100;renderCatalog();});
$('more-results').addEventListener('click',()=>{state.catalogLimit+=100;renderCatalog();});
$('catalog').addEventListener('click',e=>{const button=e.target.closest('[data-entity-id]');if(!button)return;const item=state.catalog.find(i=>i.id===Number(button.dataset.entityId));if(item)choose(item);});
$('favorites').addEventListener('click',async e=>{const button=e.target.closest('[data-favorite]');if(!button)return;const favorite=state.favorites.find(f=>keyOf(f)===button.dataset.favorite);if(!favorite)return;state.searchType=favorite.type;$('search').value='';const loading=loadCatalog();await choose(favorite,favorite.type);await loading;});
$('favorite-toggle').addEventListener('click',()=>{if(!state.selected)return;const key=keyOf(state.selected);const index=state.favorites.findIndex(f=>keyOf(f)===key);if(index>=0)state.favorites.splice(index,1);else{if(state.favorites.length>=50){toast('В избранном уже 50 записей. Удалите одну, чтобы добавить новую.');return;}state.favorites.push({...state.selected});}save('favorites',state.favorites);renderFavorites();});
$('day-tabs').addEventListener('click',e=>{const button=e.target.closest('[data-date]');if(button){state.view='day';save('view','day');setDate(button.dataset.date);}});
$('prev-week').addEventListener('click',()=>setDate(addDays(state.date,-7)));$('next-week').addEventListener('click',()=>setDate(addDays(state.date,7)));$('today').addEventListener('click',()=>setDate(todayUfa()));
$('date-picker').addEventListener('change',()=>setDate($('date-picker').value));$('day-view').addEventListener('click',()=>setView('day'));$('week-view').addEventListener('click',()=>setView('week'));
$('subgroup').addEventListener('change',()=>{state.subgroup=$('subgroup').value;if(state.selected){const raw=stored('subgroups',{});const prefs=raw&&typeof raw==='object'&&!Array.isArray(raw)?raw:{};prefs[preferenceKey()]=state.subgroup;save('subgroups',prefs);}updateURL();renderSchedule();});$('refresh').addEventListener('click',()=>{if(state.selected)loadSchedule(true);});
for(const name of ['year','semester'])$(name).addEventListener('change',async()=>{
  state[name]=Number($(name).value);state.subgroup='';state.events=[];state.response=null;state.scheduleError='';catalogs.clear();
  $('search').value='';const loading=loadCatalog();if(state.selected){state.selected.year=state.year;updateURL(true);await loadSchedule();}else updateURL();await loading;
});
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',async()=>{state.searchType='group';$('search').value=button.dataset.example;await loadCatalog();const item=state.catalog.find(i=>i.name===button.dataset.example);if(item)choose(item,'group');else{drawer(true);renderCatalog();}}));
$('events').addEventListener('click',async e=>{
  const detail=e.target.closest('[data-detail-type]');
  if(detail){
    const type=detail.dataset.detailType;const id=Number(detail.dataset.detailId);
    state.searchType=type;$('search').value='';await loadCatalog();
    if(state.searchType!==type)return;
    const item=state.catalog.find(i=>i.id===id);
    if(item)await choose(item,type);else{drawer(true);toast('Не найдено в списке этого семестра. Попробуйте поиск по названию.');}
    return;
  }
  const button=e.target.closest('[data-event-group]');if(!button)return;state.searchType='group';$('search').value='';await loadCatalog();const item=state.catalog.find(i=>i.id===Number(button.dataset.eventGroup));if(item)choose(item,'group');else toast('Этой группы нет в списке выбранного семестра.');});
$('copy-link').addEventListener('click',async()=>{
  updateURL();try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(location.href);else{const input=document.createElement('textarea');input.value=location.href;input.className='copy-fallback';document.body.append(input);input.select();const copied=document.execCommand('copy');input.remove();if(!copied)throw new Error();}toast('Ссылка на расписание скопирована');}catch{toast('Скопируйте ссылку из адресной строки браузера.');}
});
document.addEventListener('keydown',e=>{
  const isInput=e.target instanceof Element&&e.target.matches('input,textarea,select,[contenteditable="true"]');
  if(e.key==='Escape'&&$('search-panel').classList.contains('open'))drawer(false);
  if(e.key==='/'&&!isInput){e.preventDefault();drawer(true);}
  if(e.key==='Tab'&&$('search-panel').classList.contains('open')){const focusable=[...$('search-panel').querySelectorAll('a[href],button,input,select')].filter(el=>!el.disabled&&el.getClientRects().length);const first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
});
matchMedia('(max-width:850px)').addEventListener('change',e=>{if(!e.matches){$('search-panel').classList.remove('open');$('search-panel').removeAttribute('role');$('search-panel').removeAttribute('aria-modal');$('drawer-backdrop').hidden=true;document.body.classList.remove('drawer-open');$('open-search').setAttribute('aria-expanded','false');}});
window.addEventListener('popstate',async()=>{scheduleAbort?.abort();state.scheduleEpoch++;state.events=[];state.response=null;readURL();const loading=loadCatalog();if(state.selected)await loadSchedule();else{$('schedule-shell').hidden=true;$('welcome').hidden=false;document.title=config.title;}await loading;});
setInterval(()=>{if(!document.hidden&&state.selected&&!state.loading)loadSchedule(true);},300000);
setInterval(()=>{if(!document.hidden&&state.response&&!state.loading)renderSchedule();},60000);

async function init(){
  try{const data=await api({action:'period'});config.years=data.years;config.defaultYear=data.period.year;config.defaultSemester=data.period.semester;config.academicStart=data.period.academicStart;}catch{/* Configured period remains usable if automatic detection is unavailable. */}
  readURL(true);renderFavorites();const loading=loadCatalog();if(state.selected)await loadSchedule();await loading;
}
init();
