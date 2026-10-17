"""Inspect the public website's date filter contract, without Telegram."""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from bgmu_bot.bgmu import BGMUSource
from bgmu_bot.website import WebsiteClient


async def main():
    result = {'filters': [], 'frontend_schedule_snippets': []}
    async with WebsiteClient.from_environment() as website:
        js = (await website.get('/assets/app.js')).text
        for match in list(re.finditer(r'api\.php|action:\s*[\'"]schedule|date_from|dateFrom|start_date|fetchSchedule', js))[:15]:
            result['frontend_schedule_snippets'].append(js[max(0,match.start()-100):match.end()+220])
        source = BGMUSource(website)
        exact = next(m for m in await source.search_groups('П-114') if m.labels[5]=='П-114')
        params = dict(action='schedule',type='group',id=exact.path[5],year=exact.path[1],semester=exact.path[2])
        today = datetime.now(ZoneInfo('Asia/Yekaterinburg')).date()
        first, last = today.isoformat(), (today+timedelta(days=13)).isoformat()
        for start_key,end_key in [(None,None),('date_from','date_to'),('from','to'),('start','end'),('start_date','end_date'),('dateFrom','dateTo')]:
            query = dict(params)
            if start_key: query.update({start_key:first,end_key:last})
            response = await website.get('/api.php',params=query)
            data = response.json()
            events = data.get('events',[])
            result['filters'].append({'keys':[start_key,end_key], 'count':len(events), 'bytes':len(response.content),
                'outside':sum(not first <= e.get('date','') <= last for e in events),
                'min_date':min((e.get('date','') for e in events),default=None),
                'max_date':max((e.get('date','') for e in events),default=None)})
    Path('verification/range-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

asyncio.run(main())
