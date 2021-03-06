import urllib

from plone.z3cform.interfaces import IWrappedForm
from zope.component.hooks import getSite
from zope.interface import alsoProvides
from zope.schema import getFieldNamesInOrder
from Products.CMFCore.utils import getToolByName

from uu.formlibrary.interfaces import IFormDefinition
from uu.formlibrary.forms import ComposedForm
from uu.formlibrary.utils import local_query
from common import BaseFormView


class FormInputView(BaseFormView):

    def __init__(self, context, request):
        super(FormInputView, self).__init__(context, request)
        self._form = ComposedForm(self.context, request)
        # non-intuitive, but the way to tell the renderer not to wrap
        # the form is to tell it that it is already wrapped.
        alsoProvides(self._form, IWrappedForm)

    def fieldnames(self):
        return getFieldNamesInOrder(self.definition.schema)

    def groups(self):
        return self._form.groups

    def render_form(self):
        for group in self._form.groups:
            fieldgroup = self._form.components.groups[group.__name__]
            if fieldgroup.group_usage != 'grid':
                group.updateWidgets()
        self._form.updateWidgets()
        # note: by this point, updateWidgets() may be called twice:
        # once by form update prior to above, and then by above; this
        # appears to have no adverse consequence.
        if self._form.save_attempt:
            data, errors = self._form.extractData()
        return self._form.render()

    def update(self, *args, **kwargs):
        self._form.update(*args, **kwargs)


class DefinitionPreview(FormInputView):
    """
    Definition preview view, mocks what a form looks/acts like,
    and provides links to contained items.
    """

    BATCHSIZE = 8

    _fieldgroups = _formsets = _filters = None  # default, uncached

    def __init__(self, context, request):
        super(DefinitionPreview, self).__init__(context, request)
        self.path = '/'.join(context.getPhysicalPath())
        self.portal = getSite()
        self.catalog = getToolByName(self.portal, 'portal_catalog')
        self.batchsize = int(request.form.get('batchsize', self.BATCHSIZE))
        self.showmore = []

    def sorted_query(self, query, types=None):
        query = dict(query.items())  # copy
        query = local_query(self.context, query, types)
        query['sort_on'] = 'modified'
        query['sort_order'] = 'descending'
        return query

    def _contents(self, types=None):
        """
        Returns a lazy sequence of catalog brain objects for contained
        items, filtered by one or more portal_type, sorted newest first.
        """
        if types is None:
            q = local_query(self.context, {})
        else:
            if isinstance(types, basestring):
                types = (str(types),)  # wrap in tuple
            q = self.sorted_query({}, types=types)
        return self.catalog.unrestrictedSearchResults(q)

    def more_contents_url(self, spec):
        portal_state = self.context.unrestrictedTraverse('@@plone_portal_state')
        search_url = '%s/search' % (portal_state.navigation_root_url(),)
        pathq = 'path=%s' % (urllib.quote_plus(self.path),)
        spec_types = {
            'fieldgroups': ('uu.formlibrary.fieldgroup',),
            'filters': (
                'uu.formlibrary.recordfilter',
                'uu.formlibrary.compositefilter',
                ),
            'formsets': ('uu.formlibrary.setspecifier',),
            }
        if spec not in spec_types:
            return '/'.join(self.context.absolute_url(), 'folder_contents')
        ftis = spec_types.get(spec)
        typekey = 'portal_type:list'
        typeq = urllib.urlencode(zip((typekey,) * len(ftis), ftis))
        return '%s?%s&%s' % (search_url, pathq, typeq)

    def fieldgroups(self):
        if self._fieldgroups is None:
            r = self._contents(
                types='uu.formlibrary.fieldgroup',
                )
            if len(r) <= self.batchsize:
                self._fieldgroups = r
            else:
                self._fieldgroups = r[:self.batchsize]
                self.showmore.append('fieldgroups')
        return self._fieldgroups

    def filters(self):
        if self._filters is None:
            r = self._contents(
                types=(
                    'uu.formlibrary.recordfilter',
                    'uu.formlibrary.compositefilter',
                    ),
                )
            if len(r) <= self.batchsize:
                self._filters = r
            else:
                self._filters = r[:self.batchsize]
                self.showmore.append('filters')
        return self._filters

    def formsets(self):
        if self._formsets is None:
            r = self._contents(
                types='uu.formlibrary.setspecifier',
                )
            if len(r) <= self.batchsize:
                self._formsets = r
            else:
                self._formsets = r[:self.batchsize]
                self.showmore.append('formsets')
        return self._formsets


class FormDisplayView(FormInputView):
    """ Display form: Form view in display mode without buttons """

    VIEWNAME = 'view'

    def __init__(self, context, request):
        super(FormDisplayView, self).__init__(context, request)
        self._form.mode = 'display'


class FieldRules(object):
    """
    Simple view to obtain form rules JSON from definition of form context.
    """

    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self, *args, **kwargs):
        definition = IFormDefinition(self.context)
        data = getattr(definition, 'field_rules', '{}').strip()
        setHeader = self.request.response.setHeader
        setHeader('Content-type', 'application/json')
        setHeader('Content-length', len(data))
        return data

