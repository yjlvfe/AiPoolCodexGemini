"""Structured dashboard views of the same account and quota data as the CLI."""
from concurrent.futures import ThreadPoolExecutor
from account_manager import Manager, read_json
from usage_format import inspect, countdown


def windows(values, ag=False):
    index = {x.get('window' if ag else 'windowDurationMins'): x for x in values}
    result = {}
    for key, label in [('5h' if ag else 300, '5h'), ('weekly' if ag else 10080, 'wk')]:
        window = index.get(key, {})
        value = window.get('remainingFraction' if ag else 'usedPercent')
        result[label + '_pct'] = None if value is None else round(max(0, min(100, value * 100 if ag else 100 - value)))
        result[label + '_reset'] = countdown(window.get('resetTime' if ag else 'resetsAt'))
    return result


def metrics(accounts):
    result = {}
    for label in ('5h', 'wk'):
        values = [a[label + '_pct'] for a in accounts if a.get(label + '_pct') is not None]
        result.update({label + '_pool_total':sum(values), label + '_pool_max':len(values)*100, label + '_pool_pct':round(sum(values)/len(values)) if values else None, label + '_known_accounts':len(values)})
    return result


def pool_report(provider):
    manager = Manager(provider)
    ids = manager.ids()
    def one(n):
        item = {'account':int(n), 'is_active':manager.active() == n, 'email':f'Account {n}'}
        try:
            item['email'] = manager.identity(read_json(manager.credential(n))).get('email') or item['email']
            info = inspect(manager, n)
            item.update(status=info.get('status', 'CHECK FAILED'), email=info.get('email') or item['email'])
        except Exception as exc:
            info = {}
            item.update(status='CHECK FAILED', error=str(exc))
        if manager.ag:
            groups = info.get('usage', {}).get('groups') or []
            for name in ('gemini', 'claude'):
                group = next((g for g in groups if name in g.get('displayName', '').lower()), {})
                item[name] = windows(group.get('buckets') or [], ag=True)
        else:
            item.update(windows(info.get('windows') or []), plan=info.get('planType') or 'N/A')
        return item
    with ThreadPoolExecutor(max_workers=8) as executor:
        accounts = list(executor.map(one, ids))
    totals = {'total_accounts':len(accounts), 'healthy_accounts':sum(a['status'] == 'OK' for a in accounts)}
    if manager.ag:
        totals.update({name:metrics([a[name] for a in accounts]) for name in ('gemini', 'claude')})
    else:
        totals.update(metrics(accounts))
    return {'type':provider, 'active_account':int(manager.active()) if manager.active() else None, 'port':8123 if manager.ag else 8124, 'pool_metrics':totals, 'accounts':accounts, 'raw_usage':''}
