"""Registers the generic pieces every project gets: queue signals, apply
handlers (plus a no-op for every record-only verdict) and the label-reference
providers. Project plugins add to or override these."""


def register(wb):
    from . import signals, store
    from .handlers import RecordOnly, content, schema
    wb.signals.update(signals.BUILTIN)
    wb.masked_signals.update(signals.MASKED)
    for h in content.handlers() + schema.handlers():
        wb.handler(h)
    if wb.cfg.verdicts.exists():
        for kind, vs in store.load_verdicts(wb.cfg.verdicts).items():
            for verdict, entry in vs.items():
                if entry["writes"] == "none":
                    wb.handler(RecordOnly(kind, verdict))
    wb.ref_providers.extend(content.REF_PROVIDERS)
