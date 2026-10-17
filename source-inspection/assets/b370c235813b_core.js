export const DAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
export const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2"/>',
  star:'<path d="m12 3 2.8 5.7 6.3.9-4.5 4.4 1 6.2L12 17.3l-5.6 2.9 1-6.2L3 9.6l6.2-.9z"/>',
  phone:'<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>',
  external:'<path d="M14 3h7v7m0-7L11 13m-1-9H6a3 3 0 0 0-3 3v11a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-4"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  'chevron-left':'<path d="m14 6-6 6 6 6"/>',
  'chevron-right':'<path d="m10 6 6 6-6 6"/>',
  link:'<path d="m10 13 4-4m-6 1L5 13a4.2 4.2 0 0 0 6 6l3-3m-4-8 3-3a4.2 4.2 0 0 1 6 6l-3 3"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 8a7 7 0 0 1 11.7-2.5L20 9M4 15l2.8 3.5A7 7 0 0 0 18.5 16"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  group:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
  teacher:'<circle cx="12" cy="8" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
  room:'<path d="M5 21V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v17M3 21h18M9 7h6m-6 4h6m-6 4h2m4 0h0m-5 6v-3h4v3"/>',
  coffee:'<path d="M4 8h12v6a6 6 0 0 1-12 0zm12 1h2a3 3 0 0 1 0 6h-2M3 22h16M7 3v2m5-3v3"/>',
};
export const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] ?? paths.calendar}</svg>`;
export function validDate(key) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key ?? '')) return false;
  const date = new Date(key + 'T12:00:00Z');
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0,10) === key;
}
export const addDays = (key, count) => { const d = new Date(key+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+count); return d.toISOString().slice(0,10); };
export const monday = key => addDays(key,-((new Date(key+'T12:00:00Z').getUTCDay()+6)%7));
export const weekDates = key => Array.from({length:7},(_,i)=>addDays(monday(key),i));
export const formatDate = (key, options) => new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC',...options}).format(new Date(key+'T12:00:00Z'));
export const todayUfa = (now = new Date()) => new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Yekaterinburg',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
export const academicWeek = (key,start) => 1 + Math.round((new Date(monday(key)+'T12:00:00Z')-new Date(monday(start)+'T12:00:00Z'))/(7*86400000));
export const normalizeSearch = text => String(text??'').toLocaleLowerCase('ru-RU').replace(/ё/g,'е').replace(/[^\p{L}\p{N}]/gu,'');
export function filterEvents(events, subgroup) {
  if (!subgroup) return events;
  return events.filter(e=>!e.details?.length||e.details.some(d=>!d.subgroup||d.subgroup===subgroup))
    .map(e=>({...e,details:e.details.filter(d=>!d.subgroup||d.subgroup===subgroup)}));
}
export function kindClass(kind) {
  const k=(kind||'').toLowerCase();
  if (/экзам|зач[её]т|аттест/.test(k)) return 'kind-exam';
  if (/лекци/.test(k)) return 'kind-lecture';
  if (/лаборат/.test(k)) return 'kind-lab';
  if (/практи|семинар/.test(k)) return 'kind-practice';
  return 'kind-other';
}
export function eventState(event, now = new Date()) {
  if (!event.start || !event.end) return '';
  const start = new Date(`${event.date}T${event.start}:00+05:00`);
  let end = new Date(`${event.date}T${event.end}:00+05:00`);
  if (end<start) end=new Date(+end+86400000);
  return now>=end ? 'is-past' : now>=start && now<end ? 'is-current' : '';
}
export const plural = (n, forms) => forms[n%100>=11&&n%100<=14?2:n%10===1?0:n%10>=2&&n%10<=4?1:2];
const icsEscape = s => String(s??'').replace(/\\/g,'\\\\').replace(/\r?\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;');
function foldLine(line) {
  const encoder = new TextEncoder(); let size=0,result='',part='';
  for(const char of line){const bytes=encoder.encode(char).length;if(size+bytes>75){result+=part+'\r\n ';part='';size=1;}part+=char;size+=bytes;}
  return result+part;
}
export function makeCalendar(events, title, source, now = new Date()) {
  const timestamp = date=>date.toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');
  const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//BGMU Schedule Viewer//RU','CALSCALE:GREGORIAN','METHOD:PUBLISH','X-WR-CALNAME:'+icsEscape(title)];
  let count=0;
  for(const event of events){
    if(!event.start||!event.end)continue;
    const start=new Date(`${event.date}T${event.start}:00+05:00`);let end=new Date(`${event.date}T${event.end}:00+05:00`);if(end<start)end=new Date(+end+86400000);
    const details=event.details.map(d=>[d.group,d.subgroup?'Подгруппа '+d.subgroup:'',d.teacher,d.room||d.building].filter(Boolean).join(' · '));
    const description=[event.kind,...details,event.comment,'Время занятий: Уфа, UTC+5',source].filter(Boolean).join('\n');
    const rooms=[...new Set(event.details.map(d=>d.room||d.building).filter(Boolean))].join('; ');
    lines.push('BEGIN:VEVENT',`UID:bgmu-${event.id}-${event.date}@schedule.local`,'DTSTAMP:'+timestamp(now),'DTSTART:'+timestamp(start),'DTEND:'+timestamp(end),'SUMMARY:'+icsEscape(event.subject),'DESCRIPTION:'+icsEscape(description),'LOCATION:'+icsEscape(rooms),'END:VEVENT');count++;
  }
  lines.push('END:VCALENDAR');return {text:lines.map(foldLine).join('\r\n')+'\r\n',count};
}
