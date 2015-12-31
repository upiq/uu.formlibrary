from plone.uuid.interfaces import IUUID

from uu.retrieval.catalog import SimpleCatalog
from uu.formlibrary.measure.cache import DataPointCache


def handle_multiform_modify(context, event):
    """Will add or replace catalog for a multiform"""
    if event and event.descriptions:
        if 'items' not in getattr(event.descriptions[0], 'attributes', ()):
            return
    context.catalog = SimpleCatalog(context)
    for uid, record in context.items():
        try:
            context.catalog.index(record)
        except TypeError:
            import traceback
            import sys
            exc_type, exc_value, exc_traceback = sys.exc_info()
            traceback.print_tb(exc_traceback, file=sys.stdout)
            print '--------' * 5
            print 'Cound not index record for context: %s' % context
            print 'Record %s' % record.record_uid
            print record.__dict__
    DataPointCache().reload(IUUID(context))


def handle_multiform_savedata(context):
    """Hook called from update() of multiform entry view/form"""
    handle_multiform_modify(context, None)  # for now, just replace catalog

