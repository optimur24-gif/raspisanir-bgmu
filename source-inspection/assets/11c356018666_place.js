// Shorten standard university room labels; keep unfamiliar locations intact.
export function shortPlace(room, building = '') {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  room = clean(room); building = clean(building);
  const full = room && building && !room.includes(building) ? `${room}, ${building}` : room || building;
  if (!full) return 'Аудитория уточняется';
  const corpus = /(?:учебный\s+)?корпус\s*№?\s*(\d+(?:[/-]\d+)?[а-яa-z]?)/iu;
  const match = full.match(corpus);
  const roomOnly = room.replace(corpus, '').replace(/^[\s,;·–—-]+|[\s,;·–—-]+$/g, '').trim();
  const standard = roomOnly.match(/^(?:(?:учебная\s+комната|(?:учебная\s+)?аудитория|ауд\.?|(?:учебный\s+)?кабинет|каб\.?)\s*№?)?\s*(\d+(?:[/-]\d+)?[а-яa-z]?)(?:\s+для\s+самостоятельной\s+работы)?$/iu);
  if (standard) {
    // Named hospitals and addresses must remain visible as well.
    const location = match ? `Корпус №${match[1]}` : building;
    return [location, `ауд. ${standard[1]}`].filter(Boolean).join(', ');
  }
  return full.replace(/(?:учебный\s+)?корпус\s*№?\s*/giu, 'Корпус №');
}
