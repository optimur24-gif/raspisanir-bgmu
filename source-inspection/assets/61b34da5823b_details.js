// Combine subgroup assignments only when group, teacher and location agree.
export function compactDetails(details = []) {
  const rows = new Map();
  const identity = (id, text) => Number(id) > 0 ? `id:${id}` : `name:${String(text || '').trim()}`;
  for (const detail of details) {
    const key = JSON.stringify([
      identity(detail.groupId, detail.group),
      identity(detail.teacherId, detail.teacher),
      identity(detail.roomId, detail.room),
      String(detail.building || '').trim(),
    ]);
    if (!rows.has(key)) rows.set(key, {...detail, subgroups: [], wholeGroup: false});
    const row = rows.get(key);
    const subgroup = String(detail.subgroup || '').trim();
    if (!subgroup) row.wholeGroup = true;
    else if (!row.subgroups.includes(subgroup)) row.subgroups.push(subgroup);
  }
  return [...rows.values()].map(row => ({...row,
    subgroup: row.wholeGroup ? '' : row.subgroups.sort((a,b)=>a.localeCompare(b,'ru',{numeric:true})).join(', '),
  }));
}

// Lecture audiences are already listed in the card header.
export function lectureDetails(details = []) {
  const unique = new Map();
  const identity = (id, text) => Number(id) > 0 ? `id:${id}` : `name:${String(text || '').trim()}`;
  for (const detail of details) {
    const key = JSON.stringify([
      identity(detail.teacherId, detail.teacher),
      identity(detail.roomId, detail.room),
      String(detail.building || '').trim(),
    ]);
    if (!unique.has(key)) unique.set(key, detail);
  }
  return [...unique.values()];
}
