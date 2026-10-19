"""Read-only probe: do datetime64[us] bar indexes change engine results?"""
import sys, dataclasses, json, time
sys.path.insert(0, '.')
from pathlib import Path
import pandas as pd
import scripts.gate_power_study as g
from fiboki.core.enums import Timeframe
from fiboki.marketstate.calendar import load_research_calendar
from fiboki.risk.accounting import _periods_per_year_arrays, _periods_per_year

cal = load_research_calendar()
docs = g.load_documents(['donchian_breakout_atr'])
out = {}
for inst in ['EURUSD', 'USDJPY']:
    series, skipped, meta = g.load_series(starter=False, data_root=Path('var/datastore'),
        instruments=[inst], timeframe=Timeframe('H4'), bars_from=g.K5_BARS_FROM)
    s = series[0]
    print(inst, 'index dtype', s.frame.index.dtype, 'ppy fast', _periods_per_year_arrays(s.frame.index), 'ppy ref', _periods_per_year(s.frame.index))
    for label, frame in [('as_stored', s.frame), ('ns', s.frame.set_axis(s.frame.index.as_unit('ns')))]:
        s2 = dataclasses.replace(s, frame=frame)
        t0 = time.time()
        ev = g._evaluator_for(docs[0], s2, frame, s2.dataset_version_id, cal, s2.price_lineage)(docs[0].default_values(), s2.research_window)
        tr = ev.trades
        res = dict(n=len(tr), net=round(ev.net_profit, 2), spread=round(sum(t.spread_cost for t in tr), 2),
                   stale=sum(bool(getattr(t, 'stale', False)) for t in tr), mean_size=round(sum(abs(getattr(t,'size',0) or 0) for t in tr)/max(1,len(tr)), 4),
                   secs=round(time.time()-t0, 1))
        print(inst, label, res, flush=True)
        out[f'{inst}:{label}'] = res
print(json.dumps(out, indent=1))
