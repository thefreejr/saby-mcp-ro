# Рецепты для `execute`

Всё выполняется в code mode. `tools.saby.saby_digest({...})` возвращает `{ result: string }`,
где `result` — JSON-строкой. Парси один раз, наружу отдавай только нужное.

## 1. Серверное время (делай первым, если нужно «сегодня»)

```js
const v = await tools.saby.saby_call({ command_id: 'informaciya_o_versii', params: null });
const t = JSON.parse(v.result).result['ВнешнийИнтерфейс']['ДатаВремяЗапроса']; // "15.10.2026 16.20.06"
const [d, hm] = t.split(' ');
const [day, mon, year] = d.split('.');
const today = `${day}.${mon}.${year}`;                       // "15.10.2026" — точка НЕ ставится
const yesterday = (() => {                                 // вчерашняя дата в том же формате
  const dt = new Date(+year, +mon - 1, +day - 1);
  const p = n => String(n).padStart(2, '0');
  return `${p(dt.getDate())}.${p(dt.getMonth() + 1)}.${dt.getFullYear()}`;
})();
const nowDt = `${today} ${hm.replace(/:/g, '.')}`;          // "15.10.2026 16.20.06" — ТОЧКИ
return { today, yesterday, nowDt };
```

`hm.replace(/:/g,'.')` — обязателен: `parse_dt` в MCP ждёт `%d.%m.%Y %H.%M.%S`.

## 2. Сводка за сегодня + разобранный ответ одним вызовом

```js
const v   = await tools.saby.saby_call({ command_id: 'informaciya_o_versii', params: null });
const srv = JSON.parse(v.result).result['ВнешнийИнтерфейс']['ДатаВремяЗапроса'];
const [d, hm] = srv.split(' ');
const [dd, mm, yy] = d.split('.');
const today = `${dd}.${mm}.${yy}`;
const nowDt = `${today} ${hm.replace(/:/g, '.')}`;

const r = await tools.saby.saby_digest({ since: today, use_cursor: false });
const j = JSON.parse(r.result);

const p   = j['пришло'] || {};
const att = j['требует_внимания'] || {};
const ch  = j['изменилось'] || {};

return JSON.stringify({
  окно: j['окно'],
  пришло: { всего: p['всего'], по_типам: p['по_типам'], по_контрагентам: p['по_контрагентам'] },
  в_обработке: (p['события'] || [])
      .filter(e => e['требует_внимания'])
      .map(e => ({ t: e['время_события'], док: e['название'], кт: e['контрагент']?.['название'] })),
  на_мне: (att['списки']?.['НаМне'] || [])
      .map(e => ({ док: e['название'], кт: e['контрагент']?.['название'], дн: e['возраст_дней'], срок: e['срок'] })),
  изменилось: { всего: ch['всего'], по_действиям: ch['по_действиям'], полно: ch['полно'] },
  этапы: j['служебные_этапы']?.['необработанных'],
  notes: j['notes'],
  вызовы: j['вызовы']
}, null, 2);
```

## 3. Ошибки — не молчи, а объясни

`saby_digest` бросает исключение на плохой дату/окно. Оборачивай:

```js
try {
  const r = await tools.saby.saby_digest({ since: sinceVal });
  return r.result;
} catch (e) {
  return `Ошибка сводки: ${e?.message || e}. Частая причина — формат даты: нужно "ДД.ММ.ГГГГ" или ` +
         `"ДД.ММ.ГГГГ ЧЧ.ММ.СС" (точки, не двоеточия).`;
}
```

Типовые сбои:

| Симптом | Причина | Что делать |
|---|---|---|
| `Не понял дату '...'` | двоеточия в времени или плейсхолдер «вчера» | передай `ДД.ММ.ГГГГ` |
| `session_present: false` в `whoami` | сессия истекла | попроси человека перезапустить MCP / зайти в кабинет |
| `ok: false`, `Недостаточно прав` / код `...1fa0000e0000` | у сотрудника нет прав на метод | скажи человеку, что нужно открыть участок «Документы» |
| `вызовов к Saby: 48` и note про лимит | окно слишком широкое | сузь период |

## 4. Сводка «что нового» с курсором

```js
// показать, где остановились
const c = await tools.saby.saby_digest_cursor({});
// взять сводку от курсора (use_cursor по умолчанию true, since НЕ задавать — он перебьёт курсор)
const r = await tools.saby.saby_digest({ with_changes: true });
const j = JSON.parse(r.result);
// сохранять курсор — только если человек подтвердил
// const r2 = await tools.saby.saby_digest({ save_cursor: true });
```

`save_cursor: true` двигает точку отсчёта на конец окна. **Не ставь без явной просьбы.**

## 5. Кэш на время ответа (чтобы не жечь вызовы)

Если нужно два-три представления из одного и того же ответа — сохрани разобранный JSON
в переменную и собирай из него несколько строк, а не вызывай digest повторно:

```js
const raw = (await tools.saby.saby_digest({ since: today, use_cursor: false })).result;
const j = JSON.parse(raw);
const evts = j['пришло']?.['события'] || [];
const byState = {}, byCounter = {};
for (const e of evts) {
  (byState[e['состояние']] ??= []).push(e['название']);
  const k = e['контрагент']?.['название'] || '—';
  byCounter[k] = (byCounter[k] || 0) + 1;
}
return JSON.stringify({ byState, byCounter }, null, 2);
```

## Чего не делать

- Не вызывать `saby_fetch_all` ради сводки — он обходит пагинацию, а Saby её не принимает, и выдача
  молча усечётся. Для сводки есть `saby_digest`, он сам обходит реестр по суткам.
- Не фильтровать типы вручную по названию («счёт») — типы в реестре кодовые (`СчетВх`).
- Не строить выводы из `по_контрагентам`, если суммы нет: Saby отдаёт `сумма` пустой строкой почти всегда.